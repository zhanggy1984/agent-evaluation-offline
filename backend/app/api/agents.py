"""agent 与接口管理（§4.2）+ 权重/阈值（§4.6）+ 契约探测（B.5）。

- base_url 注册时白名单校验（SSRF）；出站仍由 core/http.py 二次校验
- 创建 agent 时自动初始化 agent 级默认权重（DEFAULT_WEIGHTS）
- 阈值双签：首次改 → pending_approval(approved_by_1)；第二人 → approved(approved_by_2)；approved 才参与快照
- 契约探测：POST /{id}/probe 对 agent 接口发真实请求逐字段验证（B.5，core/probe.py）
"""
import json
from dataclasses import asdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.engine import ConfigEngine
from app.api.deps import get_current_user, require_role
from app.core.constants import ALLOWED_ADAPTER_TYPES, DEFAULT_WEIGHTS
from app.core.db import get_db
from app.core.errors import ApiError, E_CONFLICT, E_NOT_FOUND, E_VALIDATION
from app.core.http import build_agent_client, validate_base_url
from app.core.probe import probe_interface
from app.core.response import ok
from app.core.security import fernet_decrypt, fernet_encrypt
from app.models import (
    Agent, AgentDimensionWeight, AgentInterface, BaselineTarget, SystemConfig,
    TestCase, TestSuite,
)
from app.models.user import User

router = APIRouter(prefix="/agents", tags=["agents"])

Admin = Depends(require_role("admin"))
Staff = Depends(require_role("admin", "evaluator"))


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    base_url: str = Field(min_length=1, max_length=512)
    adapter_type: str = "config"
    adapter_config: dict | None = None
    contract_version: str | None = None
    owner_id: int | None = None


class AgentUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    adapter_config: dict | None = None
    contract_version: str | None = None
    enabled: bool | None = None
    owner_id: int | None = None


class InterfaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    path: str = Field(min_length=1, max_length=512)
    method: str = "POST"
    contract_type: str = Field(pattern="^(sse|sync)$")
    contract_version: str = Field(min_length=1, max_length=32)
    retryable: bool = True


class AgentAuthBody(BaseModel):
    secrets: dict  # 凭证字段（{auth.*} 模板域），Fernet 加密落库


class WeightsBody(BaseModel):
    weights: dict  # {dimension_code: float}


class TargetsBody(BaseModel):
    target_scores: dict  # {dimension_code: float}


class AgentModel(BaseModel):
    class Config:
        from_attributes = True


async def _get_agent(db: AsyncSession, agent_id: int) -> Agent:
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise ApiError(E_NOT_FOUND, "agent 不存在", 404)
    return agent


async def _get_allowlist_cidrs(db: AsyncSession) -> list[str]:
    cfg = await db.get(SystemConfig, "base_url_allowlist")
    if cfg and isinstance(cfg.value, list):
        return [str(x) for x in cfg.value]
    return []


async def _init_default_weights(db: AsyncSession, agent_id: int) -> None:
    """agent 注册时初始化 agent 级默认权重（interface_id=0）。"""
    for dim, weight in DEFAULT_WEIGHTS.items():
        db.add(AgentDimensionWeight(agent_id=agent_id, interface_id=0, dimension_code=dim, weight=weight))


def _agent_out(a: Agent) -> dict:
    return {
        "id": a.id, "name": a.name, "base_url": a.base_url, "adapter_type": a.adapter_type,
        "adapter_config": a.adapter_config, "contract_version": a.contract_version,
        "owner_id": a.owner_id, "enabled": a.enabled,
        "auth_configured": bool(a.auth_config),  # GET 不回传凭证（§九-4）
    }


def _decrypt_auth(agent: Agent) -> dict:
    """解密 agent 出站凭证（Fernet 认证加密落库，GET 不回传明文）。"""
    if not agent.auth_config:
        return {}
    try:
        cfg = agent.auth_config
        if isinstance(cfg, str):
            cfg = cfg.encode("ascii")
        return json.loads(fernet_decrypt(cfg))
    except Exception:
        return {}


@router.get("")
async def list_agents(_: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Agent).order_by(Agent.id.desc()))).scalars().all()
    return ok([_agent_out(a) for a in rows])


@router.post("")
async def create_agent(body: AgentCreate, _: User = Admin, db: AsyncSession = Depends(get_db)):
    if body.adapter_type not in ALLOWED_ADAPTER_TYPES:
        raise ApiError(E_VALIDATION, f"adapter_type 仅支持 {ALLOWED_ADAPTER_TYPES}", 400)
    validate_base_url(body.base_url, await _get_allowlist_cidrs(db))
    dup = (await db.execute(select(Agent.id).where(Agent.name == body.name))).first()
    if dup:
        raise ApiError(E_CONFLICT, "agent 名称已存在", 409)
    agent = Agent(
        name=body.name, base_url=body.base_url, adapter_type=body.adapter_type,
        adapter_config=body.adapter_config, contract_version=body.contract_version,
        owner_id=body.owner_id,
    )
    db.add(agent)
    await db.flush()  # 拿 id
    await _init_default_weights(db, agent.id)
    await db.commit()
    return ok(_agent_out(agent))


@router.get("/{agent_id}")
async def get_agent(agent_id: int, _: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return ok(_agent_out(await _get_agent(db, agent_id)))


@router.put("/{agent_id}")
async def update_agent(
    agent_id: int, body: AgentUpdate, _: User = Admin, db: AsyncSession = Depends(get_db),
):
    agent = await _get_agent(db, agent_id)
    if body.name is not None:
        agent.name = body.name
    if body.base_url is not None:
        validate_base_url(body.base_url, await _get_allowlist_cidrs(db))
        agent.base_url = body.base_url
    if body.adapter_config is not None:
        agent.adapter_config = body.adapter_config
    if body.contract_version is not None:
        agent.contract_version = body.contract_version
    if body.enabled is not None:
        agent.enabled = body.enabled
    # owner_id 显式传 null 时清除指派（不能只看值非 None，否则指派后无法收回）
    if "owner_id" in body.model_fields_set:
        agent.owner_id = body.owner_id
    await db.commit()
    return ok(_agent_out(agent))


@router.delete("/{agent_id}")
async def disable_agent(agent_id: int, _: User = Admin, db: AsyncSession = Depends(get_db)):
    agent = await _get_agent(db, agent_id)
    agent.enabled = False  # 停用不物理删
    await db.commit()
    return ok()


@router.put("/{agent_id}/auth")
async def set_agent_auth(agent_id: int, body: AgentAuthBody, _: User = Admin,
                         db: AsyncSession = Depends(get_db)):
    """写入 agent 出站凭证（§九-4）：Fernet 认证加密落库，GET 不回传明文。

    secrets 字段进入 adapter_config 模板域 {auth.*}（如 {auth.username}/{auth.password}）。
    """
    import json
    agent = await _get_agent(db, agent_id)
    agent.auth_config = fernet_encrypt(json.dumps(body.secrets, ensure_ascii=False).encode("utf-8"))
    await db.commit()
    return ok({"auth_configured": True})


@router.post("/{agent_id}/probe")
async def probe_agent(agent_id: int, suite_id: int | None = None, interface_id: int | None = None,
                      _: User = Staff, db: AsyncSession = Depends(get_db)):
    """契约探测（B.5）：对 agent 接口发真实请求，逐字段验证评测契约（§5.1/§5.2）。

    探测输入 = suite 第一个 active case（真实请求需渲染 {case.input}）；interface_id 指定时
    只探该接口，否则探 agent 全部 enabled interface。探测失败返回结果不抛错——工具是诊断用。
    """
    agent = await _get_agent(db, agent_id)
    if not agent.enabled:
        raise ApiError(E_VALIDATION, "agent 已停用，无法探测", 400)
    # 收集待探接口
    if interface_id is not None:
        iface = await db.get(AgentInterface, interface_id)
        if iface is None or iface.agent_id != agent_id or not iface.enabled:
            raise ApiError(E_NOT_FOUND, "接口不存在或已停用", 404)
        ifaces = [iface]
    else:
        ifaces = (await db.execute(select(AgentInterface).where(
            AgentInterface.agent_id == agent_id, AgentInterface.enabled == True))).scalars().all()
    if not ifaces:
        raise ApiError(E_VALIDATION, "agent 无启用的评测接口", 400)
    # 探测输入：suite 第一个 active case
    if suite_id is not None:
        suite = await db.get(TestSuite, suite_id)
        if suite is None or suite.agent_id != agent_id:
            raise ApiError(E_VALIDATION, "suite 不存在或不属于该 agent", 400)
    else:
        suite = (await db.execute(select(TestSuite).where(
            TestSuite.agent_id == agent_id).order_by(TestSuite.id).limit(1))).scalar_one_or_none()
        if suite is None:
            raise ApiError(E_VALIDATION, "agent 无 suite，无法构造探测输入", 400)
    case = (await db.execute(select(TestCase).where(
        TestCase.suite_id == suite.id, TestCase.status == "active").order_by(TestCase.id).limit(1))).scalar_one_or_none()
    if case is None:
        raise ApiError(E_VALIDATION, f"suite({suite.id}) 无 active 用例，无法构造探测请求", 400)
    # 单接口探测超时：读 scope=run 配置，缺省 120s
    timeout_s = 120.0
    cfg = await db.get(SystemConfig, "case_timeout")
    if cfg and isinstance(cfg.value, (int, float)):
        timeout_s = float(cfg.value)
    # 逐个探测（顺序，真实 LLM 调用）
    secret = _decrypt_auth(agent)
    results = []
    async with build_agent_client() as client:
        for iface in ifaces:
            adapter = ConfigEngine(agent, iface, agent.adapter_config or {}, secret)
            results.append(await probe_interface(adapter, client, case, timeout_s))
    return ok({"ok": all(r.ok for r in results),
               "interfaces": [asdict(r) for r in results]})


# ---------------- interfaces ----------------
@router.get("/{agent_id}/interfaces")
async def list_interfaces(agent_id: int, _: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await _get_agent(db, agent_id)
    rows = (await db.execute(select(AgentInterface).where(AgentInterface.agent_id == agent_id))).scalars().all()
    return ok([{**{c: getattr(i, c) for c in ("id", "agent_id", "name", "path", "method", "contract_type", "contract_version", "retryable", "enabled")}} for i in rows])


@router.post("/{agent_id}/interfaces")
async def create_interface(agent_id: int, body: InterfaceCreate, _: User = Admin, db: AsyncSession = Depends(get_db)):
    await _get_agent(db, agent_id)
    iface = AgentInterface(agent_id=agent_id, name=body.name, path=body.path, method=body.method,
                           contract_type=body.contract_type, contract_version=body.contract_version,
                           retryable=body.retryable)
    db.add(iface)
    await db.commit()
    return ok({"id": iface.id})


@router.put("/{agent_id}/interfaces/{iid}")
async def update_interface(agent_id: int, iid: int, body: InterfaceCreate, _: User = Admin, db: AsyncSession = Depends(get_db)):
    iface = await db.get(AgentInterface, iid)
    if iface is None or iface.agent_id != agent_id:
        raise ApiError(E_NOT_FOUND, "接口不存在", 404)
    iface.name = body.name
    iface.path = body.path
    iface.method = body.method
    iface.contract_type = body.contract_type
    iface.contract_version = body.contract_version
    iface.retryable = body.retryable
    await db.commit()
    return ok({"id": iface.id})


@router.delete("/{agent_id}/interfaces/{iid}")
async def delete_interface(agent_id: int, iid: int, _: User = Admin, db: AsyncSession = Depends(get_db)):
    iface = await db.get(AgentInterface, iid)
    if iface is None or iface.agent_id != agent_id:
        raise ApiError(E_NOT_FOUND, "接口不存在", 404)
    iface.enabled = False
    await db.commit()
    return ok()


# ---------------- weights（agent 级默认 + interface 覆盖） ----------------
@router.get("/{agent_id}/weights")
async def get_agent_weights(agent_id: int, _: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await _get_agent(db, agent_id)
    rows = (await db.execute(select(AgentDimensionWeight).where(
        AgentDimensionWeight.agent_id == agent_id, AgentDimensionWeight.interface_id == 0))).scalars().all()
    return ok({r.dimension_code: float(r.weight) for r in rows})


@router.put("/{agent_id}/weights")
async def set_agent_weights(agent_id: int, body: WeightsBody, _: User = Staff, db: AsyncSession = Depends(get_db)):
    await _get_agent(db, agent_id)
    for dim, w in body.weights.items():
        row = (await db.execute(select(AgentDimensionWeight).where(
            AgentDimensionWeight.agent_id == agent_id,
            AgentDimensionWeight.interface_id == 0,
            AgentDimensionWeight.dimension_code == dim))).scalar_one_or_none()
        if row is None:
            row = AgentDimensionWeight(agent_id=agent_id, interface_id=0, dimension_code=dim, weight=w)
            db.add(row)
        else:
            row.weight = w
    await db.commit()
    return ok(body.weights)


@router.get("/{agent_id}/interfaces/{iid}/weights")
async def get_interface_weights(agent_id: int, iid: int, _: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await _get_agent(db, agent_id)
    rows = (await db.execute(select(AgentDimensionWeight).where(
        AgentDimensionWeight.agent_id == agent_id, AgentDimensionWeight.interface_id == iid))).scalars().all()
    return ok({r.dimension_code: float(r.weight) for r in rows})


@router.put("/{agent_id}/interfaces/{iid}/weights")
async def set_interface_weights(agent_id: int, iid: int, body: WeightsBody, _: User = Staff, db: AsyncSession = Depends(get_db)):
    await _get_agent(db, agent_id)
    for dim, w in body.weights.items():
        row = (await db.execute(select(AgentDimensionWeight).where(
            AgentDimensionWeight.agent_id == agent_id,
            AgentDimensionWeight.interface_id == iid,
            AgentDimensionWeight.dimension_code == dim))).scalar_one_or_none()
        if row is None:
            db.add(AgentDimensionWeight(agent_id=agent_id, interface_id=iid, dimension_code=dim, weight=w))
        else:
            row.weight = w
    await db.commit()
    return ok(body.weights)


# ---------------- baseline targets（双签） ----------------
@router.get("/{agent_id}/interfaces/{iid}/targets")
async def get_targets(agent_id: int, iid: int, _: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await _get_agent(db, agent_id)
    rows = (await db.execute(select(BaselineTarget).where(
        BaselineTarget.agent_id == agent_id, BaselineTarget.interface_id == iid))).scalars().all()
    return ok([{
        "dimension_code": r.dimension_code, "target_score": float(r.target_score),
        "calibration_source": r.calibration_source,
        "approved_by_1": r.approved_by_1, "approved_by_2": r.approved_by_2,
        "approval_status": r.approval_status,
    } for r in rows])


@router.put("/{agent_id}/interfaces/{iid}/targets")
async def set_targets(
    agent_id: int, iid: int, body: TargetsBody,
    user: User = Depends(require_role("admin", "evaluator")),
    db: AsyncSession = Depends(get_db),
):
    """阈值双签：approved 才参与快照。auto=自动标定；手动改 → pending_approval；第二人 → approved。"""
    await _get_agent(db, agent_id)
    for dim, score in body.target_scores.items():
        row = (await db.execute(select(BaselineTarget).where(
            BaselineTarget.agent_id == agent_id,
            BaselineTarget.interface_id == iid,
            BaselineTarget.dimension_code == dim))).scalar_one_or_none()
        if row is None:
            row = BaselineTarget(agent_id=agent_id, interface_id=iid, dimension_code=dim,
                                 target_score=score, calibration_source="手动")
            db.add(row)
            row.approved_by_1 = user.id
            row.approval_status = "pending_approval"
        else:
            if row.approval_status == "approved" and user.role != "admin":
                raise ApiError(E_VALIDATION, "已双签通过，需 admin 或走重新标定", 400)
            row.target_score = score
            row.calibration_source = "手动"
            if row.approved_by_1 is None or row.approved_by_1 == user.id:
                row.approved_by_1 = user.id
                row.approval_status = "pending_approval"
            else:
                row.approved_by_2 = user.id
                row.approval_status = "approved"
    await db.commit()
    return ok()
