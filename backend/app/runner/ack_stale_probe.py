"""T-5.5 / G3 批 1：inbox ack 积压告警（2026-09-16 用户拍板立项）。

**判据**：`ack_status <> 'acked' AND updated_at < NOW() - 10 分钟`。
`none` / `pending` / `blocked` 三者语义**都是「回写没成功」**，故**不排除任何值** ——
排除 `blocked` 会让最需人介入的状态静默（`blocked` 是规格 `phase1.md:369` 的
「404 连通性告警」落点，当前实现缺失，但判据不能因此把它漏掉）。

**阈值 10 分钟的依据**：pull 周期 30s（`pull_loop._INTERVAL`）× 20 轮。ack 失败按契约
「不改状态、留下轮重放」（`test_ack_failure_leaves_pending_without_raising` 固化的既有
行为），故阈值必须容得下连续多轮失败，否则 online 一次重启就误报 —— 误报会吃掉告警
可信度（`chronic-noise-defeats-gate` 的老路）。正常 ack 是**秒级**（`_ack_active` 内
同步 HTTP），10 分钟对上线观察窗足够快。
失败路径不写库（`except BackflowClientError` 只 log+return）⇒ `updated_at` 不会被失败
重试刷新，判据不会自伤。

**只在告警集合变化时发**：循环 60s 一轮，积压不消失就每轮发一条会刷屏。按 `payload_id`
集合比较：新增发一条、清空发「已恢复」、不变只 debug。**这是状态比较，不是去重平台。**

多 worker 互斥：与 `scanner_loop` / `pull_loop` / `cap_gap_probe_loop` 同法
（每轮开头非阻塞 `GET_LOCK`，抢到才跑）。
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select, text

from app.core.alert import notify
from app.core.db import SessionLocal
from app.models.error_backflow_inbox import ErrorBackflowInbox

logger = logging.getLogger(__name__)

_INTERVAL = 60.0        # 同 reconcile_loop
_STALE_MINUTES = 10     # 见模块 docstring：pull 周期 30s × 20 轮（T-5.5 拍板为模块常量）
_LOCK = "ack_stale_probe"

_ALERT_TITLE = "回流 inbox ack 积压"
# 明细里最多列几个 payload_id（防单条告警体过大）
_MAX_LISTED = 10


async def probe_once(last_ids: set[str]) -> set[str]:
    """扫一轮，返回本轮告警集合。

    `last_ids` = 上一轮集合，决定这一轮发不发：新增/清空才发，不变只 debug。
    """
    stmt = (
        select(ErrorBackflowInbox.payload_id)
        .where(
            ErrorBackflowInbox.ack_status != "acked",
            ErrorBackflowInbox.updated_at
            < text(f"NOW() - INTERVAL {_STALE_MINUTES} MINUTE"),
        )
        .order_by(ErrorBackflowInbox.id)
    )
    async with SessionLocal() as db:
        current = {str(pid) for pid in (await db.execute(stmt)).scalars().all()}

    if current == last_ids:
        logger.debug("ack 积压探测：集合未变（%s 条），不重发", len(current))
        return current

    if current:
        added = sorted(current - last_ids)
        await notify(
            "warning",
            _ALERT_TITLE,
            f"共 {len(current)} 条 ack 未回写且超 {_STALE_MINUTES} 分钟；"
            f"新增 {len(added)} 条：{', '.join(added[:_MAX_LISTED])}",
        )
    else:
        # 集合清空 = 恢复。只在「上一轮确实告过」时才走到这里（两边都空时上面已 early return）
        await notify(
            "warning",
            _ALERT_TITLE,
            f"已恢复：积压清零（上一轮 {len(last_ids)} 条已全部回写成功）",
        )
    return current


async def ack_stale_probe_loop() -> None:
    """后台循环。异常不退出循环（与 scanner_loop / pull_loop 同约定）。"""
    from app.core.config import settings

    if not settings.backflow_enabled:
        # 无回流则 inbox 不写入，判据恒空——跑它只会刷「未启用」日志
        logger.info("ack 积压探测未启用（BACKFLOW_ENABLED=false），跳过启动")
        return
    logger.info(
        "ack 积压探测启动（interval=%ss, 阈值=%smin）", _INTERVAL, _STALE_MINUTES
    )
    last_ids: set[str] = set()
    while True:
        try:
            async with SessionLocal() as db:
                got = (await db.execute(text(f"SELECT GET_LOCK('{_LOCK}', 0)"))).scalar()
                if not got:
                    # **不 continue**：continue 会跳过下方 sleep，变成对 MySQL 的热轮询
                    # （cap_gap_probe_loop 用的是 continue，此处刻意不同）
                    logger.debug("ack 积压探测锁被其它 worker 持有，跳过本轮")
                else:
                    try:
                        last_ids = await probe_once(last_ids)
                    finally:
                        await db.execute(text(f"SELECT RELEASE_LOCK('{_LOCK}')"))
        except Exception:
            logger.exception("ack 积压探测异常")
        await asyncio.sleep(_INTERVAL)
