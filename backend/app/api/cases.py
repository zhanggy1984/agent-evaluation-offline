"""5.2 用例管理：用例集(suite) + 用例(case) CRUD / 作废 / 场景打标 / 留出集可见性。

- 权限：suite/case 写操作 Admin/Staff；作废软删（status=invalidated）保留历史
- 字段级权限：owner 改自己 agent 的 case 时禁止改 golden_answer(expected)/assertions
  （deps.require_owner_or_qa，职责分离 §四-4.5）
- 留出集：当前用户为该 agent owner 时，suite 列表与 case 列表过滤 is_held_out=true（§四-4.5）
- 场景打标：复用 scene_catalog 校验（未知 tag 400），与 scaffold.add_case_scenes 同口径
"""
import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_role
from app.core.case_rules import check_owner_denies_golden, is_agent_owner
from app.core.db import get_db
from app.core.errors import ApiError, E_CONFLICT, E_NOT_FOUND, E_VALIDATION
from app.core.response import ok
from app.models import (
    Agent, AgentInterface, CaseScene, SceneCatalog, TestCase, TestSuite,
)
from app.models.user import User

logger = logging.getLogger(__name__)

# suite 子资源（/suites 及其下 cases）；case 单体操作走 /cases/{id}
router = APIRouter(prefix="/suites", tags=["cases"])
case_router = APIRouter(prefix="/cases", tags=["cases"])

Admin = Depends(require_role("admin"))
Staff = Depends(require_role("admin", "evaluator"))

CASE_STATUSES = {"draft", "active", "invalidated"}


class SuiteCreate(BaseModel):
    agent_id: int
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None


class SuiteUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None


class CaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    interface_id: int
    input_type: str = Field(pattern="^(text|file|conversation)$")
    input: dict | None = None
    input_turns: dict | None = None
    file_ref: str | None = None
    expected: dict = Field(default_factory=dict)
    assertions: list = Field(default_factory=list)  # 断言数组：run_assertions 消费 list[dict]
    metrics: dict = Field(default_factory=dict)
    is_gold: bool = False
    is_held_out: bool = False
    scenes: list[str] | None = None


class CaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    input_type: str | None = Field(default=None, pattern="^(text|file|conversation)$")
    input: dict | None = None
    input_turns: dict | None = None
    file_ref: str | None = None
    expected: dict | None = None
    assertions: list | None = None
    metrics: dict | None = None
    status: str | None = None
    is_gold: bool | None = None
    is_held_out: bool | None = None


async def _get_suite(db: AsyncSession, suite_id: int) -> TestSuite:
    suite = await db.get(TestSuite, suite_id)
    if suite is None:
        raise ApiError(E_NOT_FOUND, "suite 不存在", 404)
    return suite


async def _get_case(db: AsyncSession, case_id: int) -> TestCase:
    case = await db.get(TestCase, case_id)
    if case is None:
        raise ApiError(E_NOT_FOUND, "用例不存在", 404)
    return case


async def _validate_scenes(db: AsyncSession, agent_id: int, tags: list[str]) -> None:
    """场景标签必须已存在于该 agent 的 scene_catalog（与 scaffold 同口径）。"""
    catalog = {r.scene_tag for r in (await db.execute(
        select(SceneCatalog).where(SceneCatalog.agent_id == agent_id))).scalars().all()}
    unknown = [t for t in tags if t not in catalog]
    if unknown:
        raise ApiError(E_VALIDATION, f"场景标签不在该 agent 场景清单中: {unknown}", 400)


async def _scene_tags_map(db: AsyncSession, case_ids: list[int]) -> dict[int, list[str]]:
    """case_id → 场景标签（已按 tag 排序）。"""
    if not case_ids:
        return {}
    rows = (await db.execute(
        select(CaseScene).where(CaseScene.case_id.in_(case_ids)))).scalars().all()
    tags: dict[int, list[str]] = {}
    for r in rows:
        tags.setdefault(r.case_id, []).append(r.scene_tag)
    for v in tags.values():
        v.sort()
    return tags


# ---------------- suite ----------------
@router.get("")
async def list_suites(agent_id: int | None = None, user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_db)):
    logger.debug("list_suites in: agent_id=%s user=%s", agent_id, user.username)
    stmt = select(TestSuite)
    if agent_id is not None:
        stmt = stmt.where(TestSuite.agent_id == agent_id)
    rows = (await db.execute(stmt.order_by(TestSuite.id.desc()))).scalars().all()
    agents = {a.id: a for a in (await db.execute(select(Agent))).scalars().all()}
    total_cnt = {sid: n for sid, n in (await db.execute(
        select(TestCase.suite_id, func.count()).group_by(TestCase.suite_id))).all()}
    out = []
    for s in rows:
        agent = agents.get(s.agent_id)
        n = total_cnt.get(s.id, 0)
        # owner 视角排除 held-out（数量与列表口径一致，不留存在痕迹）
        if is_agent_owner(agent, user):
            held = (await db.execute(select(func.count()).where(
                TestCase.suite_id == s.id, TestCase.is_held_out == True))).scalar_one()
            n -= held
        out.append({
            "id": s.id, "agent_id": s.agent_id, "agent_name": agent.name if agent else "",
            "name": s.name, "description": s.description, "case_count": n,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        })
    logger.debug("list_suites out: count=%s", len(out))
    return ok(out)


@router.post("")
async def create_suite(body: SuiteCreate, _: User = Staff, db: AsyncSession = Depends(get_db)):
    logger.debug("create_suite in: %s", body.model_dump())
    agent = await db.get(Agent, body.agent_id)
    if agent is None:
        raise ApiError(E_NOT_FOUND, "agent 不存在", 404)
    dup = (await db.execute(select(TestSuite.id).where(
        TestSuite.agent_id == body.agent_id, TestSuite.name == body.name))).first()
    if dup:
        raise ApiError(E_CONFLICT, "同 agent 下 suite 名称已存在", 409)
    suite = TestSuite(agent_id=body.agent_id, name=body.name, description=body.description)
    db.add(suite)
    await db.commit()
    logger.debug("create_suite out: id=%s", suite.id)
    return ok({"id": suite.id, "agent_id": suite.agent_id, "name": suite.name})


@router.put("/{suite_id}")
async def update_suite(suite_id: int, body: SuiteUpdate, _: User = Staff,
                       db: AsyncSession = Depends(get_db)):
    logger.debug("update_suite in: id=%s body=%s", suite_id, body.model_dump())
    suite = await _get_suite(db, suite_id)
    if body.name is not None:
        suite.name = body.name
    if body.description is not None:
        suite.description = body.description
    await db.commit()
    return ok({"id": suite.id})


@router.delete("/{suite_id}")
async def delete_suite(suite_id: int, _: User = Admin, db: AsyncSession = Depends(get_db)):
    """物理删 suite；其下仍有用例（含作废）则 409。"""
    suite = await _get_suite(db, suite_id)
    cnt = (await db.execute(select(func.count()).where(TestCase.suite_id == suite_id))).scalar_one()
    if cnt:
        raise ApiError(E_CONFLICT, "suite 下仍有用例，请先作废/删除用例", 409)
    await db.delete(suite)
    await db.commit()
    return ok({"id": suite_id})


# ---------------- case（suite 子资源） ----------------
@router.get("/{suite_id}/cases")
async def list_cases(suite_id: int, user: User = Depends(get_current_user),
                     db: AsyncSession = Depends(get_db)):
    logger.debug("list_cases in: suite_id=%s user=%s", suite_id, user.username)
    suite = await _get_suite(db, suite_id)
    agent = await db.get(Agent, suite.agent_id)
    stmt = select(TestCase).where(TestCase.suite_id == suite_id)
    if is_agent_owner(agent, user):
        stmt = stmt.where(TestCase.is_held_out == False)
    cases = (await db.execute(stmt.order_by(TestCase.id.desc()))).scalars().all()
    ifaces = {i.id: i.name for i in (await db.execute(
        select(AgentInterface).where(AgentInterface.agent_id == suite.agent_id))).scalars().all()}
    tags = await _scene_tags_map(db, [c.id for c in cases])
    out = [{
        "id": c.id, "suite_id": c.suite_id, "name": c.name,
        "interface_id": c.interface_id, "interface_name": ifaces.get(c.interface_id, ""),
        "input_type": c.input_type, "status": c.status, "is_gold": c.is_gold,
        "is_held_out": c.is_held_out, "annotation_status": c.annotation_status,
        "scene_tags": tags.get(c.id, []),
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    } for c in cases]
    logger.debug("list_cases out: count=%s", len(out))
    return ok(out)


@router.post("/{suite_id}/cases")
async def create_case(suite_id: int, body: CaseCreate, _: User = Staff,
                      db: AsyncSession = Depends(get_db)):
    logger.debug("create_case in: suite_id=%s body=%s", suite_id,
                 {**body.model_dump(), "input": "<redacted>"})
    suite = await _get_suite(db, suite_id)
    iface = await db.get(AgentInterface, body.interface_id)
    if iface is None or iface.agent_id != suite.agent_id:
        raise ApiError(E_VALIDATION, "接口不存在或不属于该 agent", 400)
    if body.scenes:
        await _validate_scenes(db, suite.agent_id, body.scenes)
    case = TestCase(
        suite_id=suite.id, interface_id=body.interface_id, name=body.name,
        input_type=body.input_type, input=body.input, input_turns=body.input_turns,
        file_ref=body.file_ref, expected=body.expected or {},
        assertions=body.assertions or [], metrics=body.metrics or {},
        is_gold=body.is_gold, is_held_out=body.is_held_out,
    )
    db.add(case)
    await db.flush()
    if body.scenes:
        db.add_all([CaseScene(case_id=case.id, scene_tag=t) for t in body.scenes])
    await db.commit()
    logger.debug("create_case out: id=%s", case.id)
    return ok({"id": case.id, "suite_id": suite.id, "name": case.name})


# ---------------- case（单体操作，/cases/{id}） ----------------
@case_router.get("/{case_id}")
async def get_case(case_id: int, user: User = Depends(get_current_user),
                   db: AsyncSession = Depends(get_db)):
    logger.debug("get_case in: id=%s user=%s", case_id, user.username)
    case = await _get_case(db, case_id)
    suite = await _get_suite(db, case.suite_id)
    agent = await db.get(Agent, suite.agent_id)
    # 留出集：owner 视为不存在
    if is_agent_owner(agent, user) and case.is_held_out:
        raise ApiError(E_NOT_FOUND, "用例不存在", 404)
    iface = await db.get(AgentInterface, case.interface_id)
    tags = (await _scene_tags_map(db, [case.id])).get(case.id, [])
    out = {
        "id": case.id, "suite_id": case.suite_id, "agent_id": suite.agent_id, "name": case.name,
        "description": case.description, "interface_id": case.interface_id,
        "interface_name": iface.name if iface else "",
        "input_type": case.input_type, "input": case.input, "input_turns": case.input_turns,
        "file_ref": case.file_ref, "expected": case.expected, "assertions": case.assertions,
        "metrics": case.metrics, "status": case.status, "is_gold": case.is_gold,
        "is_held_out": case.is_held_out, "annotation_status": case.annotation_status,
        "scene_tags": tags,
        "created_at": case.created_at.isoformat() if case.created_at else None,
        "updated_at": case.updated_at.isoformat() if case.updated_at else None,
    }
    logger.debug("get_case out: name=%s status=%s", out["name"], out["status"])
    return ok(out)


@case_router.put("/{case_id}")
async def update_case(case_id: int, body: CaseUpdate, user: User = Staff,
                      db: AsyncSession = Depends(get_db)):
    logger.debug("update_case in: id=%s body=%s", case_id,
                 {**body.model_dump(exclude_unset=True), "input": "<redacted>",
                  "expected": "<redacted>", "assertions": "<redacted>"})
    case = await _get_case(db, case_id)
    suite = await _get_suite(db, case.suite_id)
    agent = await db.get(Agent, suite.agent_id)
    # 留出集：owner 改自己不可见的 case → 视为不存在
    if is_agent_owner(agent, user) and case.is_held_out:
        raise ApiError(E_NOT_FOUND, "用例不存在", 404)
    # 字段级权限：golden_answer(expected)/assertions 由 admin 或独立 QA（owner 禁改）
    if "expected" in body.model_fields_set or "assertions" in body.model_fields_set:
        check_owner_denies_golden(agent.owner_id, user)
    data = body.model_dump(exclude_unset=True)
    if "status" in data and data["status"] not in CASE_STATUSES:
        raise ApiError(E_VALIDATION, "status 仅支持 draft/active/invalidated", 400)
    for field, value in data.items():
        setattr(case, field, value)
    await db.commit()
    return ok({"id": case.id})


@case_router.delete("/{case_id}")
async def invalidate_case(case_id: int, _: User = Staff, db: AsyncSession = Depends(get_db)):
    """作废（软删）：status=invalidated，保留历史，可再次启用。"""
    logger.debug("invalidate_case in: id=%s", case_id)
    case = await _get_case(db, case_id)
    case.status = "invalidated"
    await db.commit()
    return ok({"id": case.id})
