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
"""
import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.agents import _get_agent, _get_allowlist_cidrs
from app.api.deps import get_current_user, require_role
from app.core.contracts import diff_manifest, parse_manifest, unique_name
from app.core.contracts_v2 import AdapterDraft, build_adapter_config, parse_manifest_v2
from app.core.db import get_db
from app.core.errors import ApiError, E_NOT_FOUND, E_VALIDATION
from app.core.http import build_agent_client
from app.core.response import ok
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

    # ---- v2 路径（Q2）：contract 段存在即 v2，附带 adapter 草案（软语义：校验失败不抛）----
    if _is_v2_manifest(payload):
        manifest, errors = parse_manifest_v2(payload)
        if manifest is None:
            return ok({"ok": False, "agent_id": agent_id, "base_url": agent.base_url,
                       "errors": errors, "raw_sample": _truncate(resp.text)})
        existing = (await db.execute(select(AgentInterface).where(
            AgentInterface.agent_id == agent_id))).scalars().all()
        diff = diff_manifest(manifest, [_iface_to_dict(i) for i in existing])
        return ok({
            "ok": True, "agent_id": agent_id, "base_url": agent.base_url,
            "agent": manifest.agent, "contract_version": manifest.contract_version,
            "manifest_version": "2.0",
            "scenes": [s.model_dump() for s in manifest.scenes],
            "interfaces": [i.model_dump() for i in manifest.interfaces],
            "adapter": _adapter_block(payload),
            **diff,
        })

    # ---- v1 路径（零改动）----
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


# ---------------- v2 manifest 支持（Q2：adapter 草案生成 + 确认落库） ----------------

def _is_v2_manifest(payload) -> bool:
    """contract 段存在即视为 v2。契约版本号不强制 "2.0"——4 家现有 agent Q3 才升。

    防御非 dict：discover 里 payload 来自 agent 端点 resp.json()，可能是 list/string/null；
    非 dict 一律按 v1 走（parse_manifest 抛 pydantic ValidationError → ok=false 不 5xx）。
    """
    return isinstance(payload, dict) and isinstance(payload.get("contract"), dict)


def _adapter_block(payload: dict) -> dict:
    """v2 payload → discover 响应的 adapter 块。软语义：硬错误返回 valid=false 不抛（诊断用）。"""
    draft, errs = build_adapter_config(payload)
    if draft is None:
        return {"valid": False, "draft": None, "input_fields": [],
                "requires_auth": False, "warnings": [], "errors": errs}
    return {"valid": True, "draft": draft.adapter_config,
            "input_fields": draft.input_fields, "requires_auth": draft.requires_auth,
            "warnings": draft.warnings, "errors": []}


def _confirm_adapter_check(manifest: dict) -> tuple[AdapterDraft | None, list[str]]:
    """/agents/{id}/adapter 纯校验部分：无 contract 段或 build 硬错误 → (None, errors)。"""
    if not isinstance(manifest.get("contract"), dict):
        return None, ["manifest 无 contract 段（v1），无法生成 adapter；v1 adapter_config 请手工配置"]
    return build_adapter_config(manifest)


class AdapterConfirmBody(BaseModel):
    manifest: dict  # 完整 v2 manifest（含 contract 段），前端可编辑后回传


@router.post("/agents/{agent_id}/adapter")
async def confirm_adapter(agent_id: int, body: AdapterConfirmBody, _: User = Admin,
                          db: AsyncSession = Depends(get_db)):
    """确认 v2 manifest → 服务端权威生成 adapter_config 落库（快捷接入 Q2）。

    body 收完整 v2 manifest（真相源），adapter_config 由 build_adapter_config 生成，
    Q1 硬错误校验在此兜底（防坏 adapter 入库）；manifest 快照内嵌
    adapter_config._manifest_v2（ConfigEngine 按 key 读取忽略未知键），为 Q3 单一真相源铺路。
    """
    agent = await _get_agent(db, agent_id)
    draft, errs = _confirm_adapter_check(body.manifest)
    if draft is None:
        raise ApiError(E_VALIDATION, "; ".join(errs), 400)
    agent.adapter_config = {**draft.adapter_config, "_manifest_v2": body.manifest}
    agent.contract_version = (body.manifest.get("contract_version") or "2.0")[:32]
    await db.commit()
    return ok({"agent_id": agent_id, "contract_version": agent.contract_version,
               "requires_auth": draft.requires_auth,
               "input_fields": draft.input_fields,
               "warnings": draft.warnings})


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
