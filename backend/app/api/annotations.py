"""5.2 用例标注：双人标注流转 + 待办队列。

- 同人同维度不重复（UK case_id+annotator_id+dimension_code），更新覆盖
- 整体状态机（POST 后重算落库）：
  draft（无标注）→ single（有维度仅 1 人）→ double（所有已标维度均双人）→
  consensus（双人且 level 全部一致）/ disputed（任一维度两人 level 不一致）
- 待办队列：annotation_status in (draft, single)；admin/evaluator 默认全部、
  可带 agent_id 过滤；owner 仅返回自己 agent（留出集 owner 不可见 §四-4.5）
- 留出集：owner 不可见/不可标 held-out case（与 cases 列表同口径）
"""
import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_role
from app.core.case_rules import recalc_annotation_status
from app.core.db import get_db
from app.core.errors import ApiError, E_NOT_FOUND, E_VALIDATION
from app.core.response import ok
from app.models import Agent, CaseAnnotation, Dimension, TestCase, TestSuite
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/annotations", tags=["annotations"])

Staff = Depends(require_role("admin", "evaluator"))

TODO_STATUSES = ("draft", "single")


class AnnotationCreate(BaseModel):
    dimension_code: str = Field(min_length=1, max_length=64)
    level: float | None = Field(default=None, ge=0, le=10)
    note: str | None = None


async def _get_case(db: AsyncSession, case_id: int) -> TestCase:
    case = await db.get(TestCase, case_id)
    if case is None:
        raise ApiError(E_NOT_FOUND, "用例不存在", 404)
    return case


async def _owner_of_agent(db: AsyncSession, agent_id: int) -> int | None:
    agent = await db.get(Agent, agent_id)
    return agent.owner_id if agent else None


@router.get("/todo")
async def annotation_todo(agent_id: int | None = None, user: User = Staff,
                          db: AsyncSession = Depends(get_db)):
    """待办队列：annotation_status in (draft, single) 的 case。

    admin/evaluator 默认全部，可带 agent_id 过滤；owner 只返回自己 agent。
    """
    logger.debug("annotation_todo in: agent_id=%s user=%s", agent_id, user.username)
    stmt = (select(TestCase, TestSuite.agent_id)
            .join(TestSuite, TestCase.suite_id == TestSuite.id)
            .where(TestCase.annotation_status.in_(TODO_STATUSES)))
    if agent_id is not None:
        stmt = stmt.where(TestSuite.agent_id == agent_id)
    elif user.role != "admin":
        owned = (await db.execute(
            select(Agent.id).where(Agent.owner_id == user.id))).scalars().all()
        if owned:
            stmt = stmt.where(TestSuite.agent_id.in_(owned))
        # 非 owner 的 evaluator：全部 agent 都可见
    rows = (await db.execute(stmt.order_by(TestCase.id.desc()))).all()
    case_ids = [r[0].id for r in rows]
    dims: dict[int, list[str]] = {}
    if case_ids:
        anns = (await db.execute(
            select(CaseAnnotation.dimension_code, CaseAnnotation.case_id)
            .where(CaseAnnotation.case_id.in_(case_ids)))).all()
        for dim, cid in anns:
            dims.setdefault(cid, []).append(dim)
    out = [{
        "case_id": c.id, "case_name": c.name, "suite_id": c.suite_id,
        "agent_id": agent_id_of, "annotation_status": c.annotation_status,
        "dimension_codes": sorted(set(dims.get(c.id, []))),
    } for c, agent_id_of in rows]
    logger.debug("annotation_todo out: count=%s", len(out))
    return ok(out)


@router.get("/cases/{case_id}")
async def list_case_annotations(case_id: int, user: User = Depends(get_current_user),
                                db: AsyncSession = Depends(get_db)):
    logger.debug("list_case_annotations in: case_id=%s user=%s", case_id, user.username)
    case = await _get_case(db, case_id)
    suite = await db.get(TestSuite, case.suite_id)
    owner_id = await _owner_of_agent(db, suite.agent_id)
    # 留出集：owner 视为不存在
    if owner_id == user.id and case.is_held_out:
        raise ApiError(E_NOT_FOUND, "用例不存在", 404)
    rows = (await db.execute(
        select(CaseAnnotation).where(CaseAnnotation.case_id == case_id)
        .order_by(CaseAnnotation.dimension_code, CaseAnnotation.id))).scalars().all()
    annotator_ids = {r.annotator_id for r in rows}
    annotators = {}
    if annotator_ids:
        annotators = {u.id: u.username for u in (await db.execute(
            select(User).where(User.id.in_(annotator_ids)))).scalars().all()}
    out = [{
        "id": r.id, "annotator_id": r.annotator_id,
        "annotator_name": annotators.get(r.annotator_id, str(r.annotator_id)),
        "dimension_code": r.dimension_code,
        "level": float(r.level) if r.level is not None else None,
        "note": r.note, "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]
    logger.debug("list_case_annotations out: count=%s", len(out))
    return ok(out)


@router.post("/cases/{case_id}")
async def add_annotation(case_id: int, body: AnnotationCreate, user: User = Staff,
                         db: AsyncSession = Depends(get_db)):
    """写入标注（同人同维度覆盖更新）并重算整体状态机。"""
    logger.debug("add_annotation in: case_id=%s user=%s dim=%s", case_id, user.username,
                 body.dimension_code)
    case = await _get_case(db, case_id)
    suite = await db.get(TestSuite, case.suite_id)
    owner_id = await _owner_of_agent(db, suite.agent_id)
    # 留出集：owner 不可标自己不可见的 held-out case
    if owner_id == user.id and case.is_held_out:
        raise ApiError(E_NOT_FOUND, "用例不存在", 404)
    # 维度必须已注册（FK 指向 dimension.code，非主键 id，按 code 查）
    dim = (await db.execute(
        select(Dimension).where(Dimension.code == body.dimension_code))).scalar_one_or_none()
    if dim is None:
        raise ApiError(E_VALIDATION, f"评测维度 {body.dimension_code} 未注册", 400)

    row = (await db.execute(select(CaseAnnotation).where(
        CaseAnnotation.case_id == case_id, CaseAnnotation.annotator_id == user.id,
        CaseAnnotation.dimension_code == body.dimension_code))).scalar_one_or_none()
    if row is None:
        db.add(CaseAnnotation(case_id=case_id, annotator_id=user.id,
                              dimension_code=body.dimension_code, level=body.level, note=body.note))
    else:
        row.level = body.level
        row.note = body.note

    # 状态机重算（读当前全部标注）
    all_rows = (await db.execute(
        select(CaseAnnotation).where(CaseAnnotation.case_id == case_id))).scalars().all()
    recalc_annotation_status(case, all_rows)
    await db.commit()
    logger.debug("add_annotation out: case_id=%s status=%s", case_id, case.annotation_status)
    return ok({"case_id": case_id, "annotation_status": case.annotation_status})
