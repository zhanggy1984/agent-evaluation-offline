"""元评测（6.4）：数据清理手动触发（scanner ⑦ 自动兜底，此处手动入口）。

POST /meta/cleanup（Staff）：保留 retain_runs 次分批删最老 run（排除 pinned），审计留痕。
"""
import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.core.db import get_db
from app.core.response import ok
from app.models.user import User

router = APIRouter(prefix="/meta", tags=["meta"])

Staff = Depends(require_role("admin", "evaluator"))

logger = logging.getLogger(__name__)


@router.post("/cleanup")
async def trigger_cleanup(request: Request, user: User = Staff,
                          db: AsyncSession = Depends(get_db)):
    """手动触发数据清理：保留 retain_runs 次分批删（排除 pinned run），审计留痕。

    级联清理 eval_result/judge_task/export_token（DB CASCADE）；agent 侧清库走预留钩子。
    """
    from app.core.audit import write_audit
    from app.runner.cleanup_rules import run_cleanup
    logger.debug("trigger_cleanup in: user=%s", user.username)
    result = await run_cleanup(db)
    # C2：target_id "-"→None（对照 scanner.py:132-135 同 data_cleanup 已修——"-" 非 ID 属语义占位，
    # 未来数值比较 CAST 会 1292；None 才是「无目标」正解）
    await write_audit(db, user, request, "data_cleanup", "eval_run", None,
                      detail={"purged": result["purged"], "retain": result["retain"]})
    await db.commit()
    logger.debug("trigger_cleanup out: %s", result)
    return ok(result)
