"""后台扫描任务（§15.1）：run 生命周期兜底。

- 心跳租约超时：pending/running 态 lease_until 过期 → 标 timeout（进程崩溃/假死/未接管的 run 兜底）
- hard_deadline：running 态超过硬超时 → 标 timeout（不随心跳续期，兜底死循环/无限流式）
- 回收时同步置 orchestrator 取消标志，防止已标 timeout 的 run 仍在执行用例
- 回收后调 score_run_salvage：timeout/scoring_failed 的 run 也出分（7.5a，不丢已采集数据）
- ④（7.5e）：scoring 无活跃 judge 任务 → score_run 兜底收敛（worker 无任务早退不触发的最终评分）

仅回收 pending/running：scoring 是「采集完成待评分」的终态，_finish 后心跳已停止（lease 不再刷新），
若纳入租约回收会被误覆盖成 timeout（丢采集已完成的事实）。scoring 超时仍由 ③ 标 scoring_failed，
再由 score_run_salvage 出分（评分恢复机制，§15.4）。
"""
import asyncio
import logging
import time
from datetime import datetime, timedelta

from sqlalchemy import select, text

from app.core.db import SessionLocal
from app.models.run import EvalRun, JudgeTask
from app.runner.orchestrator import orchestrator
from app.runner.scorer import score_run, score_run_salvage

logger = logging.getLogger(__name__)

_SCAN_INTERVAL = 15          # 扫描间隔（秒）
_LEASE_GRACE = 10            # 心跳 30s 刷新、租约 90s；给网络抖动留余量，不误杀
_CLEANUP_INTERVAL = 3600     # 数据清理低频（每小时一次），与 15s 扫描解耦


async def _now() -> datetime:
    return datetime.utcnow()


async def _reap_one_pass() -> int:
    """一轮回收：返回标 timeout 的 run 数。"""
    now = await _now()
    reaped: list[int] = []
    scoring_failed: list[tuple[int, int]] = []  # (run_id, scoring_timeout)
    async with SessionLocal() as db:
        # ① 心跳租约超时（pending/running：pending 未接管、running 崩溃/假死兜底；
        #    scoring 是终态，心跳已停，不参与租约回收）
        lease_rows = (await db.execute(select(EvalRun).where(
            EvalRun.status.in_(("pending", "running")),
            EvalRun.lease_until.isnot(None),
            EvalRun.lease_until < now - timedelta(seconds=_LEASE_GRACE),
        ))).scalars().all()
        # ② hard_deadline 超时（running 且超过硬超时）
        deadline_rows = (await db.execute(select(EvalRun).where(
            EvalRun.status == "running",
            EvalRun.hard_deadline.isnot(None),
            EvalRun.hard_deadline < now,
        ))).scalars().all()
        for run in {*lease_rows, *deadline_rows}:
            run.status = "timeout"
            run.finished_at = now
            reaped.append(run.id)
        # ③ scoring 超时（scoring 是「采集完成待评分」终态，评分应在时限内完成；
        #    超时 = scorer 崩溃/未接管/卡死 → 标 scoring_failed，保留采集数据，不误标执行失败）
        #    7.5d：存在活跃 judge 任务（pending/processing）→ 慢 judge，不误杀；
        #    DB 态即事实源，进程重启后同样生效，精确区分「慢 judge」vs「真卡死」
        scoring_rows = (await db.execute(select(EvalRun).where(
            EvalRun.status == "scoring",
            EvalRun.finished_at.isnot(None),
        ))).scalars().all()
        for run in scoring_rows:
            limit = (run.run_config or {}).get("scoring_timeout", 3600)
            if run.finished_at < now - timedelta(seconds=limit):
                active_task = (await db.execute(select(JudgeTask).where(
                    JudgeTask.run_id == run.id,
                    JudgeTask.status.in_(("pending", "processing")),
                ).limit(1))).scalars().first()
                if active_task is not None:
                    logger.info("run %s scoring 超时但有活跃 judge 任务（慢 judge），不回收", run.id)
                    continue
                run.status = "scoring_failed"
                run.finished_at = now
                scoring_failed.append((run.id, limit))
        # ④ scoring 无活跃 judge 任务 → score_run 兜底收敛
        #   worker _drain_once 在无任务可认领时早退不触发最终评分（worker.py）；此处兜底，
        #   防 run 永久卡 scoring。score_run 幂等（非 scoring 直接返回），安全。
        scoring_stuck: list[int] = []
        for run in scoring_rows:
            if run.status != "scoring":  # ③ 已标 scoring_failed 的跳过
                continue
            active_task = (await db.execute(select(JudgeTask).where(
                JudgeTask.run_id == run.id,
                JudgeTask.status.in_(("pending", "processing")),
            ).limit(1))).scalars().first()
            if active_task is None:
                scoring_stuck.append(run.id)
        if reaped or scoring_failed:
            await db.commit()
    for run_id in reaped:
        orchestrator.cancel_run(run_id)  # 停掉还在跑的用例（尽力而为）
        logger.warning("scanner 回收 run %s → timeout（租约/硬超时过期）", run_id)
        await score_run_salvage(run_id)  # 7.5a：超时 run 也出分（不丢已采集数据）
    for run_id, limit in scoring_failed:
        logger.warning("scanner 回收 run %s → scoring_failed（评分超时 %ss）", run_id, limit)
        await score_run_salvage(run_id)
    for run_id in scoring_stuck:
        logger.info("scanner 兜底：run %s scoring 无活跃 judge 任务 → score_run 收敛", run_id)
        await score_run(run_id)
    return len(reaped)


async def _cleanup_pass() -> dict:
    """⑦ 数据清理：保留 N 次分批删（排除 pinned run），系统审计留痕。

    手动触发走 POST /meta/cleanup（带用户审计）；本路径为 scanner 兜底（user_id=0 系统）。
    """
    from app.models.misc import AuditLog
    from app.runner.cleanup_rules import run_cleanup
    async with SessionLocal() as db:
        result = await run_cleanup(db)
        if result["purged"]:
            # target_id 语义是目标 id；无具体 run 的清理写 NULL。曾写 "-" 使 target_id
            # 列混入非数字值，MySQL 数值比较时把列 CAST 成 DOUBLE 报 1292（2026-08-20 修复）
            db.add(AuditLog(user_id=0, action="data_cleanup", target_type="eval_run",
                            target_id=None, detail={"purged": result["purged"],
                                                    "retain": result["retain"]}))
            await db.commit()
    return result


async def scanner_loop() -> None:
    """后台循环（main 启动时 create_task）。异常不退出循环。

    7.6 C4 多 worker 单例：每轮开头非阻塞 GET_LOCK（0s 超时）抢全局扫描锁，
    抢到才扫描——多 worker 各自起 scanner_loop 时只允许一个 worker 执行回收/验证。
    GET_LOCK/RELEASE_LOCK 是 MySQL 连接级锁，必须同一 session 连接内成对（业务 pass
    内部自开 session，与本 session 连接不同，不影响——锁只保护「扫描入口互斥」）。
    """
    logger.info("scanner 启动（interval=%ss）", _SCAN_INTERVAL)
    last_cleanup = 0.0
    while True:
        try:
            async with SessionLocal() as db:
                got = (await db.execute(
                    text("SELECT GET_LOCK('scanner_loop', 0)"))).scalar()
                if not got:
                    logger.debug("scanner 锁被其它 worker 持有，跳过本轮")
                    continue  # 先释放本 session 连接（锁随之释放），再回 sleep
                try:
                    await _reap_one_pass()
                    # 数据清理低频独立节流（清理失败不阻塞扫描，单独兜底）
                    now = time.monotonic()
                    if now - last_cleanup >= _CLEANUP_INTERVAL:
                        await _cleanup_pass()
                        last_cleanup = now
                finally:
                    await db.execute(text("SELECT RELEASE_LOCK('scanner_loop')"))
        except Exception:
            logger.exception("scanner 扫描异常")
        await asyncio.sleep(_SCAN_INTERVAL)
