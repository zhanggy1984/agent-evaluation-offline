"""7.8 前置③：pending_human 人工复核（低置信判分回写）。

机制（judge/worker.py:150-164）：judge LLM confidence < judge_review_confidence →
JudgeTask.status=pending_human，t.result 已写 {dimension, level, score, reason,
rubric_version, confidence}；run 阻塞在 scoring（scorer.py:219 见 pending_human 即
return），scanner 超时（human_review_timeout 默认 86400s）才兜底标 failed。

复核动作：
- approve：采纳 t.result 原判定 → status=done → score_run 收敛
- reject 带 score/reason：人工改判覆盖 score/reason → status=done
- reject 不带分：该维度放弃 → status=failed（评分侧该维度 N/A）

约束：
- 仅 Staff（admin/evaluator）可复核；viewer 不可见证据级内容
- 留出集：owner 不可复核 held-out run 的任务（is_held_out_visible 同语义，deps.py）
- 审计 action=judge.review；复核信息（reviewed_by/reviewed_at）写入 t.result，不改表
- JudgeTask 主键为复合键（run_id, case_id, dimension_code），无单列 id，请求体传三键
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, is_held_out_visible, require_role
from app.core.audit import write_audit
from app.core.db import get_db
from app.core.errors import ApiError, E_NOT_FOUND, E_VALIDATION
from app.core.response import ok
from app.models import Agent, EvalRun, JudgeTask, TestCase
from app.models.user import User
from app.runner.scorer import score_run

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reviews", tags=["reviews"])

Staff = Depends(require_role("admin", "evaluator"))

REVIEWABLE = ("pending_human",)


class ReviewBody(BaseModel):
    """复合键定位任务 + 复核动作（JudgeTask 无单列 id）。"""
    run_id: int
    case_id: int
    dimension_code: str = Field(min_length=1, max_length=64)
    action: str = Field(pattern="^(approve|reject)$")
    score: float | None = Field(default=None, ge=0, le=100)
    reason: str | None = Field(default=None, max_length=2000)


async def _is_held_out_hidden(db: AsyncSession, run: EvalRun, user: User) -> bool:
    """留出集 owner 隐藏（owner 不可见/不可复核 held-out run 的任务，deps 同口径）。"""
    agent = await db.get(Agent, run.agent_id)
    return not is_held_out_visible(run.trigger_type, user, agent.owner_id if agent else None)


@router.get("/pending")
async def list_pending(user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_db)):
    """待复核队列：全库 pending_human 任务（Staff 可见；owner 对 held-out 隐藏）。"""
    logger.debug("reviews pending in: user=%s", user.username)
    tasks = (await db.execute(
        select(JudgeTask).where(JudgeTask.status.in_(REVIEWABLE))
        .order_by(JudgeTask.updated_at.desc()))).scalars().all()
    if not tasks:
        return ok([])
    run_ids = {t.run_id for t in tasks}
    case_ids = {t.case_id for t in tasks}
    runs = {r.id: r for r in (await db.execute(
        select(EvalRun).where(EvalRun.id.in_(run_ids)))).scalars().all()}
    cases = {c.id: c for c in (await db.execute(
        select(TestCase).where(TestCase.id.in_(case_ids)))).scalars().all()}
    agent_ids = {r.agent_id for r in runs.values()}
    agents = {a.id: a.name for a in (await db.execute(
        select(Agent).where(Agent.id.in_(agent_ids)))).scalars().all()}

    out = []
    for t in tasks:
        run = runs.get(t.run_id)
        if run is None:
            continue
        if await _is_held_out_hidden(db, run, user):
            continue  # 留出集 owner：视为不存在（同 runs.py 结果端点语义）
        case = cases.get(t.case_id)
        out.append({
            "run_id": t.run_id, "case_id": t.case_id,
            "case_name": case.name if case else "",
            "agent_name": agents.get(run.agent_id, ""),
            "suite_id": run.suite_id,
            "dimension_code": t.dimension_code,
            "confidence": (t.result or {}).get("confidence"),
            "result": t.result,
            "status": t.status,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        })
    logger.debug("reviews pending out: count=%s", len(out))
    return ok(out)


@router.post("/review")
async def review_task(body: ReviewBody, user: User = Staff, request: Request = None,
                      db: AsyncSession = Depends(get_db)):
    """人工复核回写：approve 采纳 / reject 改判或放弃，处理后触发 score_run 收敛。"""
    logger.debug("review_task in: run=%s case=%s dim=%s action=%s user=%s",
                 body.run_id, body.case_id, body.dimension_code, body.action, user.username)
    t = await db.get(JudgeTask, (body.run_id, body.case_id, body.dimension_code))
    if t is None or t.status != "pending_human":
        raise ApiError(E_NOT_FOUND, "复核任务不存在或已处理", 404)
    run = await db.get(EvalRun, body.run_id)
    if run is None or await _is_held_out_hidden(db, run, user):
        raise ApiError(E_NOT_FOUND, "复核任务不存在或已处理", 404)
    # 改判必须给理由（防无依据改分）；reason 已限长
    if body.action == "reject" and body.score is not None and not (body.reason or "").strip():
        raise ApiError(E_VALIDATION, "改判必须填写理由", 400)

    result = dict(t.result or {})
    result["reviewed_by"] = user.username
    result["reviewed_at"] = datetime.utcnow().isoformat()
    if body.action == "approve":
        result["review_action"] = "approve"
        t.status = "done"  # 采纳原判定（score/level/reason 原样保留）
    else:
        result["review_action"] = "reject"
        if body.score is not None:
            result["score"] = body.score       # 人工改判分（level 保留原值，理由见 review reason）
            result["reason"] = body.reason
            t.status = "done"
        else:
            result["score"] = None             # 维度放弃 → 评分侧 N/A
            result["level"] = None
            result["reason"] = body.reason or "人工复核驳回（未给分，维度记 N/A）"
            t.status = "failed"
    t.result = result
    await write_audit(db, user, request, "judge.review", "judge_task",
                      body.run_id, {"case_id": body.case_id,
                                    "dimension_code": body.dimension_code,
                                    "action": body.action, "score": body.score,
                                    "reason": body.reason})
    await db.commit()
    # 收敛：该 run 若仍有其他 pending_human，score_run 内部阻塞返回；全部终态才评分
    await score_run(body.run_id)
    logger.info("task run=%s case=%s %s 复核 %s → %s", body.run_id, body.case_id,
                body.dimension_code, body.action, t.status)
    return ok({"run_id": body.run_id, "case_id": body.case_id,
               "dimension_code": body.dimension_code, "status": t.status})
