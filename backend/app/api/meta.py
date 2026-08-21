"""元评测（6.4）：judge 漂移检测触发 + 历史序列。

POST /meta/drift/check 手动触发（Staff）：重判 is_gold 用例比对金标准，写
JudgeDriftHistory + 超阈值告警。重判烧 judge LLM token，低频高成本操作，故手动而非周期任务。
GET /meta/drift 读漂移历史序列（登录可读，前端按维度渲染趋势）。
"""
import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_role
from app.core.db import get_db
from app.core.response import ok
from app.models import JudgeDriftHistory
from app.models.user import User

router = APIRouter(prefix="/meta", tags=["meta"])

Admin = Depends(require_role("admin"))
Staff = Depends(require_role("admin", "evaluator"))

logger = logging.getLogger(__name__)


class DriftCheckBody(BaseModel):
    """漂移检测粒度限定（可选）：不传 → 全量重判 is_gold 用例。"""
    dimension_code: str | None = None
    agent_id: int | None = None


@router.get("/drift")
async def list_drift(dimension_code: str | None = None, limit: int = 100, offset: int = 0,
                     _: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """漂移历史序列（created_at 倒序，按维度过滤可选）。"""
    logger.debug("list_drift in: dimension_code=%s", dimension_code)
    stmt = select(JudgeDriftHistory).order_by(JudgeDriftHistory.created_at.desc())
    if dimension_code:
        stmt = stmt.where(JudgeDriftHistory.dimension_code == dimension_code)
    rows = (await db.execute(stmt.limit(min(limit, 500)).offset(max(offset, 0)))).scalars().all()
    out = [{
        "id": h.id, "dimension_code": h.dimension_code,
        "consistency_rate": float(h.consistency_rate),
        "judged_case_cnt": h.judged_case_cnt, "drift_flag": h.drift_flag,
        "created_at": h.created_at.isoformat() if h.created_at else None,
    } for h in rows]
    logger.debug("list_drift out: count=%s", len(out))
    return ok(out)


@router.post("/drift/check")
async def trigger_drift(body: DriftCheckBody | None = None, _: User = Staff):
    """手动触发漂移检测：重判 is_gold 用例比对金标准，写历史 + 超阈值告警。

    粒度可选（body.dimension_code / body.agent_id）：只重判指定维度或指定 agent 的
    gold 用例，避免全量重判浪费 judge token。不传 body → 全量。
    同步执行（当前金标准样本小，judge 调用量可控）；返回本次阈值/维度一致率/逐用例明细。
    """
    from app.judge.drift_check import run_drift_check
    logger.debug("trigger_drift in: user=%s dimension_code=%s agent_id=%s",
                 _.username, body.dimension_code if body else None,
                 body.agent_id if body else None)
    result = await run_drift_check(body.dimension_code if body else None,
                                   body.agent_id if body else None)
    logger.debug("trigger_drift out: history=%s details=%s",
                 len(result["history"]), len(result["details"]))
    return ok(result)


@router.post("/cleanup")
async def trigger_cleanup(request: Request, user: User = Staff,
                          db: AsyncSession = Depends(get_db)):
    """手动触发数据清理：保留 retain_runs 次分批删（排除 pinned/issue 关联 run），审计留痕。

    级联清理 eval_result/judge_task/export_token（DB CASCADE）；agent 侧清库走预留钩子。
    """
    from app.core.audit import write_audit
    from app.runner.cleanup_rules import run_cleanup
    logger.debug("trigger_cleanup in: user=%s", user.username)
    result = await run_cleanup(db)
    await write_audit(db, user, request, "data_cleanup", "eval_run", "-",
                      detail={"purged": result["purged"], "retain": result["retain"]})
    await db.commit()
    logger.debug("trigger_cleanup out: %s", result)
    return ok(result)
