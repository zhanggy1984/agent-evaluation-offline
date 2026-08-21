"""6.4 元评测：留出集复测 overfit 判定。

overfit_gap = 最近 manual run agent_score − 最近 N 次 held_out run agent_score 均值。
N = 全局热配置 overfit_heldout_window（缺省 3）：单次 held_out 跑偏（如一次低分）不触发降级，
摊平留出集波动。纯函数（overfit_gap/is_overfit）宿主单测直接跑；check_overfit 为 DB 编排，
在 held_out run 评分完成后接入（score_run 末尾，同 verify_issues_for_run 模式）。
超阈值 → 写 issue 告警（复用 6.2 问题闭环；同 agent 未关闭告警幂等跳过）。
L0 门禁降级由 dashboard_rules.build_gate_cards 计算（读全局热配置 overfit_threshold /
overfit_heldout_window，与 get_overfit_config 同口径，防看板与告警分叉）。
"""
from __future__ import annotations

import logging

from sqlalchemy import select

logger = logging.getLogger(__name__)

OVERFIT_ISSUE_TITLE = "过拟合告警-{agent}"
# 参与幂等去重的未关闭状态（与 issue_rules.ISSUE_ACTIVE_STATUS 同口径，防导入环）
_ACTIVE = ("open", "fixing", "fixed", "verified")
# 有评分的终态（timeout/cancelled/scoring_failed 无 agent_score 不取）
_SCORED_STATUS = ("completed", "partial_failed")


def overfit_gap(manual_score: float | None, held_out_scores: list[float]) -> float | None:
    """留出集差距 = manual − 最近 N 次 held_out 均值；manual 缺失或 held_out 空 → None。"""
    if manual_score is None or not held_out_scores:
        return None
    return round(manual_score - sum(held_out_scores) / len(held_out_scores), 2)


def is_overfit(gap: float | None, threshold: float) -> bool:
    """过拟合判定：gap > 阈值 → 过拟合（gap 越大 = 留出集掉分越狠）。"""
    return gap is not None and gap > threshold


async def get_overfit_config(db) -> tuple[float, int]:
    """overfit 阈值/窗口全局热配置（system_config，is_hot 热生效）。

    唯一读取点：看板 gate 与 held_out 告警共用（防口径分叉）；
    缺省 15 / 3（与 seed.DEFAULT_SYSTEM_CONFIG 一致）。
    值解析安全：显式判 None（不吞合法 0——threshold=0 即 gap>0 判过拟合）；
    非数字脏值回落默认（配置被改脏不打挂只读看板端点）。
    """
    from app.models import SystemConfig

    def _num(value, default: float) -> float:
        if value is None:
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            logger.warning("overfit 配置非数字（%r），回落默认 %s", value, default)
            return default

    thr = await db.get(SystemConfig, "overfit_threshold")
    win = await db.get(SystemConfig, "overfit_heldout_window")
    threshold = _num(thr.value if thr else None, 15.0)
    window = max(int(_num(win.value if win else None, 3.0)), 1)
    return threshold, window


async def _recent_scores(db, agent_id: int, trigger_type: str, n: int,
                         exclude_id: int | None = None) -> list[float]:
    """该 agent 最近 n 次有评分的同类型终态 run 的 agent_score（时间倒序，exclude_id 排除当前 run）。"""
    from app.models import EvalRun
    stmt = select(EvalRun.agent_score).where(
        EvalRun.agent_id == agent_id, EvalRun.trigger_type == trigger_type,
        EvalRun.status.in_(_SCORED_STATUS), EvalRun.agent_score.isnot(None),
    )
    if exclude_id is not None:
        stmt = stmt.where(EvalRun.id != exclude_id)
    rows = (await db.execute(
        stmt.order_by(EvalRun.started_at.desc(), EvalRun.id.desc()).limit(n))).scalars().all()
    return [float(s) for s in rows]


async def _notify_overfit(db, run, *, active: bool, gap: float, threshold: float,
                          manual_score: float | None = None, held_avg: float | None = None) -> None:
    """6.6 通知（统一入口；db 仅只读 agent 名；异常隔离——通知失败绝不影响 run 生命周期）。"""
    try:
        from app.core.alarm import notify_alarm
        from app.models import Agent
        agent = await db.get(Agent, run.agent_id)
        name = agent.name if agent else f"agent{run.agent_id}"
        if active:
            summary = (f"agent {name} 留出集复测 gap={gap}%"
                       f"（manual {manual_score} − held_out 均值 {held_avg}），"
                       f"超过阈值 {threshold}%，可能过拟合训练集")
        else:
            summary = (f"agent {name} 留出集复测 gap={gap}% 未超阈值 {threshold}%，overfit 告警解除")
        await notify_alarm("overfit", f"overfit-{run.agent_id}", run.id, active, summary)
    except Exception:
        logger.exception("overfit 通知异常（不影响 run 生命周期）")


async def check_overfit(run_id: int) -> None:
    """held_out run 评分完成后计算 overfit_gap（最近 N 次 held_out 均值），超阈值写告警。

    仅对 trigger_type=held_out 的 run 生效（manual run 不做 overfit 判定）；
    manual 或 held_out 缺评分 → 静默返回（数据不足以判定不误报）。
    窗口 N = 全局热配置 overfit_heldout_window（缺省 3），含当前 run，历史补前 N-1 次；
    阈值 = 全局热配置 overfit_threshold（缺省 15）——看板 gate 同口径。
    """
    from app.core.db import SessionLocal
    from app.models import Agent, EvalRun, Issue

    async with SessionLocal() as db:
        run = await db.get(EvalRun, run_id)
        if run is None or run.trigger_type != "held_out":
            return
        manual = await _recent_scores(db, run.agent_id, "manual", 1)
        if not manual or run.agent_score is None:
            logger.debug("run %s 无 manual 对照评分，跳过 overfit 判定", run_id)
            return
        threshold, window = await get_overfit_config(db)
        past = await _recent_scores(db, run.agent_id, "held_out", window - 1, exclude_id=run_id)
        held_scores = [float(run.agent_score)] + past
        gap = overfit_gap(manual[0], held_scores)
        if not is_overfit(gap, threshold):
            logger.info("run %s overfit_gap=%s 未超阈值 %s", run_id, gap, threshold)
            await _notify_overfit(db, run, active=False, gap=gap, threshold=threshold)
            return
        agent = await db.get(Agent, run.agent_id)
        title = OVERFIT_ISSUE_TITLE.format(agent=agent.name if agent else f"agent{run.agent_id}")
        held_avg = round(sum(held_scores) / len(held_scores), 2)
        exists = (await db.execute(select(Issue.id).where(
            Issue.title == title, Issue.status.in_(_ACTIVE)))).first()
        if exists:
            # 告警进行中：不重复建 issue（6.4 幂等），但邮件仍走 dedupe_window 去重（持续超阈值周期提醒）
            await _notify_overfit(db, run, active=True, gap=gap, threshold=threshold,
                                  manual_score=manual[0], held_avg=held_avg)
            return
        db.add(Issue(
            agent_id=run.agent_id, title=title,
            description=f"留出集复测 gap={gap}%（manual {manual[0]} − held_out 均值 {held_avg}，"
                        f"{window} 次），超过阈值 {threshold}%，可能过拟合训练集",
            severity="medium", status="open",
        ))
        await db.commit()
        logger.warning("过拟合告警：agent=%s gap=%s", run.agent_id, gap)
        await _notify_overfit(db, run, active=True, gap=gap, threshold=threshold,
                              manual_score=manual[0], held_avg=held_avg)
