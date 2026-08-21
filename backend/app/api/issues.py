"""问题跟踪（5.4 基础版）：登记 / 编辑 / 状态流转。6.2 补自动复现验证。

- 权限：列表/详情登录可读（viewer 只读）；登记/编辑/流转 staff（admin/evaluator）
- 状态机：open→fixing→fixed→verified→closed，另允许 open→closed（登记作废）
- 非法转移 400（E_VALIDATION）；closed 终态不可再动
"""
import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_role
from app.core.db import get_db
from app.core.errors import ApiError, E_NOT_FOUND, E_VALIDATION
from app.core.issue_rules import ISSUE_SEVERITY, ISSUE_STATUS, can_transition
from app.core.response import ok
from app.models.agent import Agent
from app.models.dimension import Dimension
from app.models.misc import Issue
from app.models.run import EvalRun
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/issues", tags=["issues"])

Staff = Depends(require_role("admin", "evaluator"))


def _issue_out(i: Issue, agent_name: str) -> dict:
    return {
        "id": i.id, "agent_id": i.agent_id, "agent_name": agent_name,
        "title": i.title, "description": i.description,
        "severity": i.severity, "status": i.status,
        "related_case_id": i.related_case_id, "related_dimension": i.related_dimension,
        "created_run_id": i.created_run_id, "resolved_version": i.resolved_version,
        "last_verify_run_id": i.last_verify_run_id, "last_verify_result": i.last_verify_result,
        "created_at": i.created_at.isoformat() if i.created_at else None,
        "updated_at": i.updated_at.isoformat() if i.updated_at else None,
    }


@router.get("")
async def list_issues(
    agent_id: int | None = None, status: str | None = None, severity: str | None = None,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """问题列表（筛选 agent/status/severity，按 updated_at 倒序；viewer 可读）。"""
    logger.debug("issues in: agent_id=%s status=%s severity=%s user=%s",
                 agent_id, status, severity, user.username)
    conds = []
    if agent_id is not None:
        conds.append(Issue.agent_id == agent_id)
    if status:
        conds.append(Issue.status == status)
    if severity:
        conds.append(Issue.severity == severity)
    rows = (await db.execute(
        select(Issue).where(*conds).order_by(Issue.updated_at.desc()))).scalars().all()
    agents = {a.id: a.name for a in (await db.execute(select(Agent))).scalars()}
    out = [_issue_out(i, agents.get(i.agent_id, "")) for i in rows]
    logger.debug("issues out: count=%s", len(out))
    return ok(out)


class IssueCreate(BaseModel):
    agent_id: int
    title: str = Field(min_length=1, max_length=256)
    description: str | None = None
    severity: str = "medium"
    related_case_id: int | None = None
    related_dimension: str | None = None


@router.post("")
async def create_issue(body: IssueCreate, _: User = Staff, db: AsyncSession = Depends(get_db)):
    """登记问题（staff），status 默认 open。"""
    logger.debug("issue create in: agent_id=%s title=%s severity=%s",
                 body.agent_id, body.title, body.severity)
    if body.severity not in ISSUE_SEVERITY:
        raise ApiError(E_VALIDATION, f"severity 必须为 {'/'.join(ISSUE_SEVERITY)}", 400)
    if await db.get(Agent, body.agent_id) is None:
        raise ApiError(E_NOT_FOUND, "agent 不存在", 404)
    if body.related_dimension is not None and \
            (await db.execute(select(Dimension).where(
                Dimension.code == body.related_dimension))).scalar_one_or_none() is None:
        # db.get 按主键 id 查，维度用 code 标识 → 必须显式按 code 过滤
        raise ApiError(E_VALIDATION, "related_dimension 不存在", 400)
    issue = Issue(
        agent_id=body.agent_id, title=body.title, description=body.description,
        severity=body.severity, status="open",
        related_case_id=body.related_case_id, related_dimension=body.related_dimension,
    )
    db.add(issue)
    await db.commit()
    await db.refresh(issue)
    logger.info("issue %s 已登记 agent=%s", issue.id, issue.agent_id)
    return ok({"id": issue.id, "status": issue.status})


class IssueUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=256)
    description: str | None = None
    severity: str | None = None
    related_case_id: int | None = None
    related_dimension: str | None = None


@router.put("/{issue_id}")
async def update_issue(issue_id: int, body: IssueUpdate, _: User = Staff,
                       db: AsyncSession = Depends(get_db)):
    """编辑问题基本信息（staff）。"""
    logger.debug("issue update in: id=%s", issue_id)
    issue = await db.get(Issue, issue_id)
    if issue is None:
        raise ApiError(E_NOT_FOUND, "问题不存在", 404)
    if body.title is not None:
        issue.title = body.title
    if body.description is not None:
        issue.description = body.description
    if body.severity is not None:
        if body.severity not in ISSUE_SEVERITY:
            raise ApiError(E_VALIDATION, f"severity 必须为 {'/'.join(ISSUE_SEVERITY)}", 400)
        issue.severity = body.severity
    if body.related_case_id is not None:
        issue.related_case_id = body.related_case_id
    if body.related_dimension is not None:
        if (await db.execute(select(Dimension).where(
                Dimension.code == body.related_dimension))).scalar_one_or_none() is None:
            # db.get 按主键 id 查，维度用 code 标识 → 必须显式按 code 过滤
            raise ApiError(E_VALIDATION, "related_dimension 不存在", 400)
        issue.related_dimension = body.related_dimension
    await db.commit()
    logger.info("issue %s 已编辑", issue_id)
    return ok()


class IssueTransition(BaseModel):
    to: str


@router.post("/{issue_id}/transition")
async def transition_issue(issue_id: int, body: IssueTransition, _: User = Staff,
                           db: AsyncSession = Depends(get_db)):
    """状态流转（staff）：合法转移校验，非法跳转 400。"""
    logger.debug("issue transition in: id=%s to=%s", issue_id, body.to)
    issue = await db.get(Issue, issue_id)
    if issue is None:
        raise ApiError(E_NOT_FOUND, "问题不存在", 404)
    if body.to not in ISSUE_STATUS:
        raise ApiError(E_VALIDATION, f"status 必须为 {'/'.join(ISSUE_STATUS)}", 400)
    if not can_transition(issue.status, body.to):
        raise ApiError(E_VALIDATION,
                       f"状态不可从 {issue.status} 流转到 {body.to}（合法路径见状态机）", 400)
    old = issue.status
    issue.status = body.to
    if body.to == "verified" and not issue.resolved_version and issue.last_verify_run_id:
        # 6.2 闭环版本证据：verified 时回填最近验证 run 的 agent version（有则写，无则留空）
        verify_run = await db.get(EvalRun, issue.last_verify_run_id)
        if verify_run is not None and verify_run.version:
            issue.resolved_version = verify_run.version
    await db.commit()
    logger.info("issue %s 流转 %s -> %s", issue_id, old, body.to)
    return ok({"id": issue.id, "status": issue.status})
