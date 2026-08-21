"""4.3 初始化脚手架：接口自动发现 + 场景清单提取（铁律：平台定标准，agent 适配标准）。

- POST /agents/{id}/discover（Staff）：调 agent 标准端点 GET /api/contracts → parse →
  diff 现有 agent_interface，返回候选与差异。网络/非200/非JSON/校验失败返回
  `200 + ok=false + errors[] + raw_sample`（诊断用，不抛 5xx）；仅入参错（agent 不存在/
  停用/SSRF 白名单）抛错。
- POST /agents/{id}/interfaces/sync（Admin）：人工确认后批量补录，幂等按 (method,path)
  skipped；同 agent name 冲突自动改名 _2/_3。
- POST/GET /agents/{id}/scenes（Admin/登录）：场景清单写读，幂等按 uk_scene。
- POST/GET /cases/{id}/scenes（Staff/登录）：给 case 打场景标签，tag 必须已存在于该
  agent 的 scene_catalog（否则 400）；幂等按复合主键 (case_id, scene_tag)。
- 6.5 POST /agents/{id}/skeleton/generate（Staff）：按场景×接口 LLM 生成用例骨架
  （status=draft，黄金答案/断言留空人工补齐）；每场景 1 次调用（并发限流 3），
  prompt 要求每 case 输出 interface 字段，align_cases 按名反查对齐、对不上名回落
  顺序，缺失接口（含漏中间）精确计入返回 missing；幂等按 (interface_id, scene_tag)
  skipped（LLM 输出的 name 不稳定不可作 key）；单场景失败不中断，全败 400。
"""
import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.agents import _get_agent, _get_allowlist_cidrs
from app.api.deps import get_current_user, require_role
from app.core.contracts import diff_manifest, parse_manifest, unique_name
from app.core.db import get_db
from app.core.errors import ApiError, E_NOT_FOUND, E_VALIDATION
from app.core.http import build_agent_client
from app.core.llm import LlmClient, get_llm_config, is_configured
from app.core.response import ok
from app.core.skeleton_gen import align_cases, build_gen_messages, normalize_case
from app.models import Agent, AgentInterface, CaseScene, SceneCatalog, TestCase, TestSuite
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["scaffold"])

Admin = Depends(require_role("admin"))
Staff = Depends(require_role("admin", "evaluator"))

RAW_SAMPLE_LIMIT = 500


def _truncate(text: str) -> str | None:
    if not text:
        return None
    return text if len(text) <= RAW_SAMPLE_LIMIT else text[:RAW_SAMPLE_LIMIT] + "…"


def _iface_to_dict(i: AgentInterface) -> dict:
    return {"id": i.id, "name": i.name, "path": i.path, "method": i.method,
            "contract_type": i.contract_type, "contract_version": i.contract_version,
            "retryable": i.retryable, "enabled": i.enabled}


# ---------------- 接口发现 ----------------
@router.post("/agents/{agent_id}/discover")
async def discover_agent(agent_id: int, _: User = Staff, db: AsyncSession = Depends(get_db)):
    """拉取 agent 标准契约清单并 diff 现有接口。失败返回 ok=false 不抛错（诊断用）。"""
    agent = await _get_agent(db, agent_id)
    if not agent.enabled:
        raise ApiError(E_VALIDATION, "agent 已停用，无法发现", 400)
    url = agent.base_url.rstrip("/") + "/api/contracts"
    cidrs = await _get_allowlist_cidrs(db)
    try:
        async with build_agent_client(extra_cidrs=cidrs) as client:
            resp = await client.get(url, timeout=30.0)
    except ApiError:
        raise  # SSRF/白名单入参类错误，向上抛
    except Exception as exc:
        logger.warning("discover 连接失败 agent=%s %s: %s", agent_id, url, exc)
        return ok({"ok": False, "agent_id": agent_id, "base_url": agent.base_url,
                   "errors": [f"连接失败: {type(exc).__name__}"], "raw_sample": None})

    if resp.status_code != 200:
        return ok({"ok": False, "agent_id": agent_id, "base_url": agent.base_url,
                   "errors": [f"标准端点返回 HTTP {resp.status_code}"],
                   "raw_sample": _truncate(resp.text)})
    try:
        payload = resp.json()
    except Exception:
        return ok({"ok": False, "agent_id": agent_id, "base_url": agent.base_url,
                   "errors": ["标准端点响应不是 JSON"], "raw_sample": _truncate(resp.text)})

    manifest, errors = parse_manifest(payload)
    if manifest is None:
        return ok({"ok": False, "agent_id": agent_id, "base_url": agent.base_url,
                   "errors": errors, "raw_sample": _truncate(resp.text)})

    existing = (await db.execute(select(AgentInterface).where(
        AgentInterface.agent_id == agent_id))).scalars().all()
    diff = diff_manifest(manifest, [_iface_to_dict(i) for i in existing])
    return ok({
        "ok": True, "agent_id": agent_id, "base_url": agent.base_url,
        "agent": manifest.agent, "contract_version": manifest.contract_version,
        "scenes": [s.model_dump() for s in manifest.scenes],
        "interfaces": [i.model_dump() for i in manifest.interfaces],
        **diff,
    })


# ---------------- 接口同步 ----------------
class InterfaceSyncItem(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    path: str = Field(min_length=1, max_length=512)
    method: str = Field(pattern="^(GET|POST|PUT|DELETE|PATCH)$")
    contract_type: str = Field(pattern="^(sse|sync)$")


class InterfaceSyncBody(BaseModel):
    interfaces: list[InterfaceSyncItem]


@router.post("/agents/{agent_id}/interfaces/sync")
async def sync_interfaces(agent_id: int, body: InterfaceSyncBody, _: User = Admin,
                          db: AsyncSession = Depends(get_db)):
    """人工确认后批量补录标准清单中的评测接口（只接受显式列表，不自动 refetch）。

    幂等：已有 (method.upper(), path) 则 skipped；name 与同 agent 现有接口冲突自动
    改名 _2/_3 并记录 renamed。
    """
    agent = await _get_agent(db, agent_id)
    version = agent.contract_version or "1.0"
    existing = (await db.execute(select(AgentInterface).where(
        AgentInterface.agent_id == agent_id))).scalars().all()
    by_key = {(i.method.upper(), i.path): i for i in existing}
    taken_names = {i.name for i in existing}

    created, skipped, renamed = [], [], []
    for item in body.interfaces:
        key = (item.method.upper(), item.path)
        if key in by_key:
            skipped.append({"name": item.name, "path": item.path, "method": item.method})
            continue
        name = unique_name(item.name, taken_names)
        taken_names.add(name)
        db.add(AgentInterface(agent_id=agent_id, name=name, path=item.path,
                              method=item.method.upper(), contract_type=item.contract_type,
                              contract_version=version))
        created.append({"name": name, "path": item.path, "method": item.method,
                        "contract_type": item.contract_type})
        if name != item.name:
            renamed.append({"from": item.name, "to": name, "path": item.path})
    await db.commit()
    return ok({"created": created, "skipped": skipped, "renamed": renamed})


# ---------------- 场景清单 ----------------
class SceneItem(BaseModel):
    tag: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=256)


class SceneBody(BaseModel):
    scenes: list[SceneItem]


@router.post("/agents/{agent_id}/scenes")
async def add_scenes(agent_id: int, body: SceneBody, _: User = Admin,
                     db: AsyncSession = Depends(get_db)):
    """批量写入 agent 场景清单，幂等按 uk_scene（agent_id, scene_tag）。"""
    await _get_agent(db, agent_id)
    existing_tags = {r.scene_tag for r in (await db.execute(
        select(SceneCatalog).where(SceneCatalog.agent_id == agent_id))).scalars().all()}
    created, skipped = [], []
    for s in body.scenes:
        if s.tag in existing_tags:
            skipped.append({"tag": s.tag})
            continue
        db.add(SceneCatalog(agent_id=agent_id, scene_tag=s.tag, description=s.description))
        created.append({"tag": s.tag, "description": s.description})
    await db.commit()
    return ok({"created": created, "skipped": skipped})


@router.get("/agents/{agent_id}/scenes")
async def list_scenes(agent_id: int, _: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_db)):
    await _get_agent(db, agent_id)
    rows = (await db.execute(select(SceneCatalog).where(
        SceneCatalog.agent_id == agent_id).order_by(SceneCatalog.id.desc()))).scalars().all()
    return ok([{"id": r.id, "scene_tag": r.scene_tag, "description": r.description}
               for r in rows])


# ---------------- 用例场景标签 ----------------
class CaseSceneBody(BaseModel):
    scene_tags: list[str]


@router.post("/cases/{case_id}/scenes")
async def add_case_scenes(case_id: int, body: CaseSceneBody, _: User = Staff,
                          db: AsyncSession = Depends(get_db)):
    """给 case 打场景标签。tag 必须已存在于该 agent 的 scene_catalog（否则 400）。

    agent 归属由 case→suite→agent 推导；幂等按复合主键 (case_id, scene_tag)。
    """
    case = await db.get(TestCase, case_id)
    if case is None:
        raise ApiError(E_NOT_FOUND, "用例不存在", 404)
    suite = await db.get(TestSuite, case.suite_id)
    if suite is None:
        raise ApiError(E_NOT_FOUND, "用例所属 suite 不存在", 404)
    catalog = {r.scene_tag for r in (await db.execute(
        select(SceneCatalog).where(SceneCatalog.agent_id == suite.agent_id))).scalars().all()}
    unknown = [t for t in body.scene_tags if t not in catalog]
    if unknown:
        raise ApiError(E_VALIDATION, f"场景标签不在该 agent 场景清单中: {unknown}", 400)

    existing = {r.scene_tag for r in (await db.execute(
        select(CaseScene).where(CaseScene.case_id == case_id))).scalars().all()}
    added, skipped = [], []
    for tag in body.scene_tags:
        if tag in existing:
            skipped.append(tag)
            continue
        db.add(CaseScene(case_id=case_id, scene_tag=tag))
        added.append(tag)
    await db.commit()
    return ok({"case_id": case_id, "added": added, "skipped": skipped})


@router.get("/cases/{case_id}/scenes")
async def list_case_scenes(case_id: int, _: User = Depends(get_current_user),
                           db: AsyncSession = Depends(get_db)):
    case = await db.get(TestCase, case_id)
    if case is None:
        raise ApiError(E_NOT_FOUND, "用例不存在", 404)
    rows = (await db.execute(select(CaseScene).where(
        CaseScene.case_id == case_id).order_by(CaseScene.scene_tag))).scalars().all()
    return ok([{"case_id": r.case_id, "scene_tag": r.scene_tag} for r in rows])


# ---------------- 6.5 用例骨架 LLM 增强生成 ----------------
GEN_SUITE_NAME = "LLM 生成骨架"


class SkeletonGenerateBody(BaseModel):
    interface_ids: list[int] | None = None   # 限定接口（不传 = 全部 enabled 接口）
    scene_tags: list[str] | None = None      # 限定场景（不传 = 全部场景）


async def _input_samples(db, agent_id: int) -> list[dict]:
    """该 agent 已有用例输入样例（每种 input_type 取 1 条），引导 LLM 对齐 adapter 输入结构。"""
    # select(单列).scalars() → 标量 int 列表，不是 Row
    suite_ids = [sid for sid in (await db.execute(
        select(TestSuite.id).where(TestSuite.agent_id == agent_id))).scalars().all()]
    if not suite_ids:
        return []
    rows = (await db.execute(
        select(TestCase.input_type, TestCase.input).where(
            TestCase.suite_id.in_(suite_ids), TestCase.input.isnot(None),
        ))).all()
    seen, samples = set(), []
    for input_type, input_ in rows:
        if input_type in seen:
            continue
        seen.add(input_type)
        samples.append({"input_type": input_type, "input": input_})
        if len(seen) >= 3:
            break
    return samples


def _coerce_list(data: Any) -> list:
    """LLM 输出兼容：list 原样；dict 剥首个 list 值（部分模型包 {"cases": [...]}）；否则空。"""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                return v
    return []


@router.post("/agents/{agent_id}/skeleton/generate")
async def generate_skeleton(agent_id: int, body: SkeletonGenerateBody, _: User = Staff,
                            db: AsyncSession = Depends(get_db)):
    """按场景×接口用 LLM 生成用例骨架（status=draft，黄金答案/断言留空人工补齐）。

    每场景 1 次 LLM 调用产出多接口 case（JSON 数组，接口顺序对齐）；同 (suite, name)
    已存在则 skipped（幂等重复触发）；单场景失败记 failure 继续（同 drift_check 单点
    失败不中断模式），全部失败 → 400。LLM 未配置 → 400 明确报错。
    """
    logger.debug("skeleton/generate in: agent_id=%s body=%s", agent_id, body.model_dump())
    agent = await _get_agent(db, agent_id)
    if not agent.enabled:
        raise ApiError(E_VALIDATION, "agent 已停用，无法生成骨架", 400)
    cfg = await get_llm_config(db)
    if not is_configured(cfg["api_key"], cfg["base_url"], cfg["model"]):
        raise ApiError(E_VALIDATION,
                       "llm 未配置，无法生成骨架（配置中心 llm.base_url/llm.model_name + env LLM_API_KEY）", 400)

    interfaces = (await db.execute(select(AgentInterface).where(
        AgentInterface.agent_id == agent_id, AgentInterface.enabled == True,
    ).order_by(AgentInterface.id))).scalars().all()
    if body.interface_ids is not None:
        wanted = set(body.interface_ids)
        unknown = wanted - {i.id for i in interfaces}
        if unknown:
            raise ApiError(E_VALIDATION, f"接口不存在或已停用: {sorted(unknown)}", 400)
        interfaces = [i for i in interfaces if i.id in wanted]
    scenes = (await db.execute(select(SceneCatalog).where(
        SceneCatalog.agent_id == agent_id).order_by(SceneCatalog.id))).scalars().all()
    if body.scene_tags is not None:
        wanted_tags = set(body.scene_tags)
        unknown = wanted_tags - {s.scene_tag for s in scenes}
        if unknown:
            raise ApiError(E_VALIDATION, f"场景标签不在该 agent 场景清单: {sorted(unknown)}", 400)
        scenes = [s for s in scenes if s.scene_tag in wanted_tags]
    if not interfaces:
        raise ApiError(E_VALIDATION, "该 agent 无已启用接口，无法生成骨架", 400)
    if not scenes:
        raise ApiError(E_VALIDATION, "该 agent 无场景清单，无法生成骨架", 400)

    # 目标 suite 幂等查建
    suite = (await db.execute(select(TestSuite).where(
        TestSuite.agent_id == agent_id, TestSuite.name == GEN_SUITE_NAME))).scalars().first()
    if suite is None:
        suite = TestSuite(agent_id=agent_id, name=GEN_SUITE_NAME,
                          description="6.5 LLM 增强生成的用例骨架（draft，人工补齐黄金答案/断言后启用）")
        db.add(suite)
        await db.flush()
    # 幂等按 (interface_id, scene_tag)：场景×接口固定，LLM 每次输出 name 可能不同，
    # 用 name 去重不可靠（实测两次生成 name 不一致 → 重复入库）
    existing_keys = set((await db.execute(
        select(TestCase.interface_id, CaseScene.scene_tag).select_from(TestCase)
        .join(CaseScene, CaseScene.case_id == TestCase.id)
        .where(TestCase.suite_id == suite.id))).all())

    samples = await _input_samples(db, agent_id)
    iface_list = [{"name": i.name, "method": i.method, "path": i.path,
                   "contract_type": i.contract_type} for i in interfaces]
    by_name = {i.name: i for i in interfaces}
    client = LlmClient(base_url=cfg["base_url"], model=cfg["model"], api_key=cfg["api_key"],
                       allowlist=cfg["allowlist"], timeout=cfg["timeout"])

    # LLM 请求阶段并发（场景间无依赖，deepseek 支持并发，judge worker 同量级），
    # Semaphore 限 3 防打爆厂商；DB 写入保持串行（AsyncSession 非并发安全，且
    # existing_keys 去重集合需无竞争追加）。全量 4 家 agent 约 16 场景 → 总时长降到 ~1/3。
    sem = asyncio.Semaphore(3)

    async def _request(scene):
        messages = build_gen_messages(agent={"name": agent.name, "adapter_type": agent.adapter_type},
                                      interfaces=iface_list,
                                      scene={"scene_tag": scene.scene_tag,
                                             "description": scene.description or ""},
                                      samples=samples)
        async with sem:
            return await client.generate_json(messages)

    results = await asyncio.gather(*[_request(s) for s in scenes], return_exceptions=True)

    generated = skipped = 0
    failures: list[dict] = []
    missing: list[dict] = []
    for scene, result in zip(scenes, results):
        if isinstance(result, BaseException):  # LlmError 单场景失败不中断整体
            logger.warning("skeleton 场景 %s 生成失败：%s", scene.scene_tag, result)
            failures.append({"scene_tag": scene.scene_tag, "error": str(result)})
            continue
        cases = _coerce_list(result)
        if not cases:
            failures.append({"scene_tag": scene.scene_tag, "error": "LLM 返回空结果，未生成任何骨架"})
            continue
        # interface 字段反查对齐（对不上名回落顺序），缺失接口（含漏中间）精确报告
        aligned, missing_names = align_cases(cases, iface_list)
        if missing_names:
            missing.append({"scene_tag": scene.scene_tag, "interface_names": missing_names})
        for raw, iface_name in aligned:
            item = normalize_case(raw)
            iface_row = by_name[iface_name]
            key = (iface_row.id, scene.scene_tag)
            if item is None or key in existing_keys:
                skipped += 1
                continue
            case = TestCase(
                suite_id=suite.id, interface_id=iface_row.id,
                name=item["name"], description=item["description"],
                input_type=item["input_type"], input=item["input"],
                input_turns=item["input_turns"], file_ref=item["file_ref"],
                expected=item["expected"], assertions=item["assertions"], metrics=item["metrics"],
                status="draft",
            )
            db.add(case)
            await db.flush()
            db.add(CaseScene(case_id=case.id, scene_tag=scene.scene_tag))
            existing_keys.add(key)
            generated += 1

    if failures and generated == 0:
        raise ApiError(E_VALIDATION, f"全部场景生成失败（LLM 调用异常）：{failures[0]['error']}", 400)
    await db.commit()
    out = {"suite_id": suite.id, "generated": generated, "skipped": skipped,
           "missing": missing, "failures": failures}
    logger.debug("skeleton/generate out: %s", out)
    return ok(out)
