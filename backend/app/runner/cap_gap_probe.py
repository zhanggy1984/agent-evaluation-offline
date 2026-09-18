"""R-8 cap_gap 探测态自愈扫描（详设 §5.6 谓词 / §5.8 节流；O-D.4）。

**为什么要有它**：`offline_cap_gap` 驳回行（agent/interface 未登记）的恢复入口，规格写作
「**每小时本地重跑自检 + 映射**」，而这条探测态**从未落码** —— 后果是映射补齐之后，那条已
acked 的行**没有任何恢复路径**：
  - 重处理谓词按 R-8 收窄为仅 `ack_status ∈ {none, pending}`（`pull_loop._needs_reprocess`）⇒
    已 acked 的行被挡在门外；
  - 人工 requeue 复用同一 `payload_id` ⇒ 拉回后撞同一谓词 ⇒ `skipped`，且 requeue 会清掉
    `invalidate_reason` ⇒ 反而把 online 侧 R2 例外的判据擦掉。
online 侧的对端接收面是**专门为这条路径写的**（`backflow/ack.py` 前置矩阵
`("invalidated", "stored_reason:offline_cap_gap")`，注释逐字「offline 重扫自愈回写」），
本模块补齐离线侧的驱动。

**节流语义（不许破坏）**：
  - 补齐 → 建 case + **单次** ack active（契约 R2 `invalidated(offline_cap_gap)→active`）；
  - 仍缺 → **静默等轮**：不改 `ack_status`、不发任何出站；
  - 全程**不重发 invalidated**（R-8 要修的就是映射持续缺期间的重复出站）。

自检判据与拉取路径**同源**（复用 `pull_loop._self_check`）：分开写必然分叉，后果是「拉取时
驳回、探测时放行」⇒ 把当初驳回的行按已放宽的判据建 case。

多 worker 互斥：与 `scanner_loop` / `pull_loop` 同法（每轮开头非阻塞 `GET_LOCK`，抢到才跑）。
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select, text

from app.core.db import SessionLocal
from app.models.error_backflow_inbox import ErrorBackflowInbox
from app.runner.pull_loop import (
    REJECT_CAP_GAP,
    _ack_active,
    _activate,
    _self_check,
)

logger = logging.getLogger(__name__)

_INTERVAL = 3600.0          # §5.8 明文「低频每小时」
_LOCK = "cap_gap_probe"

# §5.6 扫描谓词常量（R-8 探测态）—— 本次**首次落码**：
#   rejected ∧ reject_code == offline_cap_gap ∧ ack_status == acked
# 三个条件缺一不可：前两个定位「离线能力缺」的驳回行，第三个筛出「已闭环、进了探测态」的那些
# （未 acked 的走对账/重处理，不归本模块管）。
CAP_GAP_PROBE = ("rejected", REJECT_CAP_GAP, "acked")


async def probe_once() -> dict:
    """跑一轮探测：扫描探测态行，逐行本地重跑自检。返回本轮统计（验收脚本据此断言）。"""
    stat = {"scanned": 0, "activated": 0, "still_missing": 0}
    status, reject_code, ack_status = CAP_GAP_PROBE

    async with SessionLocal() as db:
        rows = (await db.execute(
            select(ErrorBackflowInbox).where(
                ErrorBackflowInbox.status == status,
                ErrorBackflowInbox.reject_code == reject_code,
                ErrorBackflowInbox.ack_status == ack_status,
            ).order_by(ErrorBackflowInbox.id)
        )).scalars().all()
        stat["scanned"] = len(rows)

        for row in rows:
            payload_id = row.payload_id
            envelope = row.envelope_json or {}
            verdict, detail, ctx = await _self_check(db, envelope)
            if verdict is not None:
                # 仍缺 → 静默等轮：**不改 ack_status、不发任何出站**（R-8 节流语义）
                stat["still_missing"] += 1
                logger.debug("cap_gap 探测：仍缺 payload_id=%s（%s：%s）",
                             payload_id, verdict, detail)
                continue

            agent, interface, words = ctx
            case = await _activate(db, envelope, payload_id, agent, interface, words)
            case_id = case.id          # commit 后属性过期，先取值（会话内同步 refresh 会炸）
            await db.commit()
            await _ack_active(payload_id, case_id)     # 铁律：commit 之后才 ack
            stat["activated"] += 1
            logger.info("cap_gap 探测：映射已补齐，自愈 payload_id=%s case_id=%s",
                        payload_id, case_id)

    return stat


async def cap_gap_probe_loop() -> None:
    """后台循环。异常不退出循环（与 scanner_loop / pull_loop 同约定）。"""
    from app.core.config import settings

    if not settings.backflow_enabled:
        # 自愈路径要发 ack active **出站**，与拉取同属回流面 ⇒ 开关关着时不得跑
        logger.info("cap_gap 探测未启用（BACKFLOW_ENABLED=false），跳过启动")
        return
    logger.info("cap_gap 探测态启动（interval=%ss）", _INTERVAL)
    while True:
        try:
            async with SessionLocal() as db:
                got = (await db.execute(text(f"SELECT GET_LOCK('{_LOCK}', 0)"))).scalar()
                if not got:
                    logger.debug("cap_gap 探测锁被其它 worker 持有，跳过本轮")
                    continue  # 先释放本 session 连接（锁随之释放），再回 sleep
                try:
                    stat = await probe_once()
                finally:
                    await db.execute(text(f"SELECT RELEASE_LOCK('{_LOCK}')"))
            if stat["scanned"]:
                logger.info(
                    "cap_gap 探测：扫描 %s 行，自愈 %s，仍缺 %s",
                    stat["scanned"], stat["activated"], stat["still_missing"],
                )
        except Exception:
            logger.exception("cap_gap 探测异常")
        await asyncio.sleep(_INTERVAL)
