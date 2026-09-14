"""R-3 版本差集对账（详设 §7.3 / phase2 §5.3 v0.7）。

**为什么需要它**（phase2 §5.3 v0.7 逐字）：「洪峰丢版本洞：活跃闸=1（§5.2 创建器校验）期间
连发的中间版本（v2 于 v1 running 时触发被拒建、v3 后 latest 前移）**其版本号从未建过 run**
→ 永久丢——claim fix_version 落在该版本 → 无推送入站 → **静默 14d TTL**」。§5.2 的自动触发按
(agent, version) 键工作、看不到别的版本，故须有这条**按 agent 的版本集合对账**兜底。

差集口径（§7.3 逐字）::

    signal_versions = 已到终态的 manual/held_out run 的 distinct version（排除 error_regression
                      自身，防自指）；「已到终态」= status NOT IN ('pending','running')
    have_versions   = 已有 error run 的 version（**含 timeout/cancelled 坏终态**：视为有 run）
    diff            = signal − have

与 §5.2 吸收态正交：吸收态防「同版本反复建」，本模块管「从未建过的版本」——缺版必然没有
error run ⇒ 门禁里的 latest 为 None ⇒ 必建，不会被吸收态挡死。
"""
import asyncio
import logging

from sqlalchemy import func, select, text

from app.core.db import SessionLocal
from app.models import EvalRun, TestCase, TestSuite
from app.runner import orchestrator

logger = logging.getLogger(__name__)

# 周期 60s（§11.3 要求 ≥ pull_loop 的 30s）。用常量而非 system_config key：与 scanner /
# pull_loop 既有惯例一致，且当前无运维消费方要调它。
_INTERVAL = 60
_LOCK = "backflow_reconcile_loop"
# 信号侧只认这两类 run（error_regression run 不作信号，防自指）
_SIGNAL_TRIGGERS = ("manual", "held_out")
# 「未到终态」（phase2 §5.1 逐字）
_NON_TERMINAL = ("pending", "running")


def _version_diff(signal_versions, have_versions) -> set:
    """版本差集 = 信号全集 − 已有 error run 的版本集合。"""
    return set(signal_versions) - set(have_versions)


def _pick_next_version(anchor_by_version: dict) -> str | None:
    """选本周期要补的版本 = **锚 run id 最小**者（到达序）。

    规格只写「一周期至多补 1 个」未定序。取锚 id 而非 semver：semver 字典序对 `10.0.0` vs
    `9.0.0` 是错的，而 run id 单调、天然表达「哪一版先到」。
    """
    if not anchor_by_version:
        return None
    return min(anchor_by_version, key=anchor_by_version.get)


async def _error_agents(db) -> list:
    """有 active error case 的 agent（§7.3 逐字「对每个有 active error case 的 agent」）。

    刻意**不用** `_runnable_error_case_count`（形态判定 + held_out 过滤的那套）——本处按规格
    字面取「有没有 case」，能不能跑交由 `maybe_auto_schedule` 内的空集门禁挡回并留日志。

    ⚠️ 此处**不含**「等 §6.3 backfill 把断言补齐」的语义：`assertions IS NULL` 的 error case
    在现实现下产不出来（`pull_loop._activate` 同一次写入内就构造 assertions，词表为空则 fail-closed
    不建 case；存量真库实测 0 条），且 backfill 已判「前提不可达」不实现（`error-backflow-task.md`
    O-E.2 的处置裁定注）。若日后真出现该形态，它会被 `case_loader._is_error_case` 剔除、不加载，
    表现为「本 agent 被扫描到但建不出 run」——那是失败面，不是待 backfill 的中间态。
    """
    stmt = (select(TestSuite.agent_id)
            .join(TestCase, TestCase.suite_id == TestSuite.id)
            .where(TestSuite.is_error_suite.is_(True),
                   TestCase.status == "active",
                   TestCase.case_type.isnot(None))
            .distinct())
    return list((await db.execute(stmt)).scalars().all())


async def _signal_anchors(db, agent_id: int) -> dict:
    """该 agent 的信号版本 → 该版最近终态 manual/held_out run id（两条信息一次查出）。

    锚取 `max(id)`（id 单调等价于到达序，与 orchestrator 既有 latest 锚查询同法）。
    """
    stmt = (select(EvalRun.version, func.max(EvalRun.id))
            .where(EvalRun.agent_id == agent_id,
                   EvalRun.trigger_type.in_(_SIGNAL_TRIGGERS),
                   EvalRun.status.notin_(_NON_TERMINAL))
            .group_by(EvalRun.version))
    return {v: run_id for v, run_id in (await db.execute(stmt)).all()}


async def _have_versions(db, agent_id: int) -> set:
    """该 agent 已有 error run 的 version（**不按状态过滤**：坏终态同样视为有 run）。"""
    stmt = (select(EvalRun.version)
            .where(EvalRun.agent_id == agent_id,
                   EvalRun.trigger_type == "error_regression")
            .distinct())
    return set((await db.execute(stmt)).scalars().all())


async def _reconcile_agent(agent_id: int) -> int | None:
    """对单个 agent 跑一次差集对账，返回补建出的 error run id（未补建返回 None）。"""
    async with SessionLocal() as db:
        anchors = await _signal_anchors(db, agent_id)
        diff = _version_diff(anchors, await _have_versions(db, agent_id))
    anchors = {v: i for v, i in anchors.items() if v in diff}
    version = _pick_next_version(anchors)
    if version is None:
        return None
    # 「一周期至多补 1」= 只尝试一个版本，无需计数器：活跃闸（ERROR_ACTIVE_QUOTA=1）与空集
    # 门禁都在 maybe_auto_schedule 内，被拒时返回 None，下周期幂等重试。
    run_id = await orchestrator.maybe_auto_schedule(agent_id, version, anchors[version])
    if run_id is None:
        logger.debug("agent=%s version=%s 差集对账未补建（活跃闸/空集门禁/吸收态），下周期重试",
                     agent_id, version)
    else:
        logger.info("agent=%s version=%s 差集对账补建 error run=%s（锚 run=%s）",
                    agent_id, version, run_id, anchors[version])
    return run_id


async def reconcile_once() -> list:
    """跑一轮对账，返回本轮补建出的 run id 列表。"""
    async with SessionLocal() as db:
        agent_ids = await _error_agents(db)
    built = []
    for agent_id in agent_ids:
        try:
            run_id = await _reconcile_agent(agent_id)
        except Exception:
            # 单 agent 出错不影响其余（同 pull_loop「一步炸不退出循环」）
            logger.exception("差集对账 agent=%s 异常，跳过（下周期重试）", agent_id)
            continue
        if run_id is not None:
            built.append(run_id)
    return built


async def reconcile_loop() -> None:
    """后台循环。异常不退出循环（与 scanner_loop / pull_loop 同约定）。"""
    from app.core.config import settings

    if not settings.backflow_enabled:
        logger.info("差集对账未启用（BACKFLOW_ENABLED=false），跳过启动")
        return
    logger.info("差集对账启动（interval=%ss）", _INTERVAL)
    while True:
        try:
            async with SessionLocal() as db:
                got = (await db.execute(text(f"SELECT GET_LOCK('{_LOCK}', 0)"))).scalar()
                if not got:
                    logger.debug("差集对账锁被其它 worker 持有，跳过本轮")
                else:
                    try:
                        built = await reconcile_once()
                        if built:
                            logger.info("差集对账本轮补建 %d 个 run", len(built))
                    finally:
                        await db.execute(text(f"SELECT RELEASE_LOCK('{_LOCK}')"))
        except Exception:
            logger.exception("差集对账异常")
        await asyncio.sleep(_INTERVAL)
