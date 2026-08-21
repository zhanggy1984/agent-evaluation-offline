"""6.2 issue 复现验证编排：run 终态后对未关闭 issue 判定并落 last_verify_*。

触发双路径（幂等防重，靠 issue.last_verify_run_id == run.id）：
- 实时：scorer.score_run 末尾（run 已 completed/partial_failed，pass_fail 已重算）
- 兜底：scanner ⑤ 槽位（补评分异常 / scoring_failed 未走实时路径的 run）

判定口径（用户拍板，见 core/issue_rules.judge_issue）：
- 维度优先 + 整体回退；error/na 用例跳过（验收要求）
- 无 result（case 不在 run）也记 last_verify_run_id + result=None（挑战点 3），
  使 scanner 不会每轮重复处理同一 run
- 回归自动重开：reproduced ∧ {fixed,verified} → status=open + audit_log("issue.reopen")
  审计追溯（user_id=0 系统动作），见 audit-helper-convention

基线达标分解析与 scorer 门禁同口径（interface 级优先，回退 agent 默认 interface_id=0）。
"""
import logging

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.dashboard_rules import TERMINAL_STATUS
from app.core.issue_rules import ISSUE_ACTIVE_STATUS, _resolve_targets, judge_issue, should_reopen
from app.models import AuditLog, EvalResult, EvalRun, Issue

logger = logging.getLogger(__name__)


async def verify_issues_for_run(run_id: int) -> int:
    """对 run 覆盖的全部未关闭 issue 复现验证，返回更新条数（单事务）。

    异常抛出由调用方隔离（score_run / scanner 各自 try/except），
    不影响 run 生命周期（run 状态在调用前已落终态）。
    """
    async with SessionLocal() as db:
        run = await db.get(EvalRun, run_id)
        if run is None or run.status not in TERMINAL_STATUS:
            return 0
        issues = (await db.execute(select(Issue).where(
            Issue.agent_id == run.agent_id,
            Issue.status.in_(ISSUE_ACTIVE_STATUS)))).scalars().all()
        if not issues:
            return 0
        results = (await db.execute(select(EvalResult).where(
            EvalResult.run_id == run_id))).scalars().all()
        result_by_case = {r.case_id: r for r in results}
        iface_by_case = await _interface_by_case(db, list(result_by_case))
        targets = await _load_targets(db, run.agent_id)

        updated = 0
        for issue in issues:
            if issue.last_verify_run_id == run_id:
                continue  # 幂等：本 run 已处理过
            result = result_by_case.get(issue.related_case_id)
            rdict = None
            if result is not None:
                rdict = {"pass_fail": result.pass_fail,
                         "score_per_dimension": result.score_per_dimension or []}
            dim_target = None
            if issue.related_dimension and result is not None:
                iid = iface_by_case.get(issue.related_case_id, 0)
                dim_target = _resolve_targets(targets, iid).get(issue.related_dimension)
            old_status = issue.status
            vr, _reason = judge_issue(
                result=rdict, related_dimension=issue.related_dimension,
                dim_target=dim_target, issue_status=issue.status)
            issue.last_verify_run_id = run_id
            issue.last_verify_result = vr
            updated += 1
            if should_reopen(old_status, vr):
                issue.status = "open"
                db.add(AuditLog(user_id=0, action="issue.reopen",
                                target_type="issue", target_id=str(issue.id),
                                detail={"old_status": old_status, "run_id": run_id,
                                        "verify_result": vr}))
                logger.warning("issue %s 回归自动重开（run %s, 原 %s）", issue.id, run_id, old_status)
        await db.commit()
        if updated:
            logger.info("run %s 复现验证更新 %d 个 issue", run_id, updated)
        return updated


# ---- baseline 达标分解析（与 scorer.py 同口径，复制防跨模块耦合；漂移时需同步）----

async def _load_targets(db, agent_id: int) -> dict:
    from app.models import BaselineTarget
    rows = (await db.execute(select(BaselineTarget).where(
        BaselineTarget.agent_id == agent_id))).scalars().all()
    return {(r.interface_id, r.dimension_code): float(r.target_score) for r in rows}


async def _interface_by_case(db, case_ids: list[int]) -> dict:
    """用例 → interface_id 映射（test_case.interface_id，达标分按接口解析）。"""
    if not case_ids:
        return {}
    from app.models import TestCase
    rows = (await db.execute(select(TestCase.id, TestCase.interface_id).where(
        TestCase.id.in_(set(case_ids))))).all()
    return {row[0]: row[1] for row in rows}
