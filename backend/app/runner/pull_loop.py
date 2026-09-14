"""回流拉取循环（批 B #232）：拉取 → 结构自检 → 激活固化 → ack。

挂载点 = `main.py` startup 的 `create_task`（与 scanner/judge worker 并列），门控
`backflow_enabled`（**缺省关**——不在既有部署上擅自开跑后台循环）。

**激活铁律「先落库再 ack」**：本地事务 commit **之后**才发 ack。反过来的失败模式是
「online 认为已激活、offline 库里没有」，静默丢失且 requeue 不愈（P1:345）。

多 worker 互斥：与 `scanner_loop` 同法——每轮开头非阻塞 `GET_LOCK`（0s 超时），抢到才跑。
锁只保护「拉取入口互斥」，业务内部自开 session 不受影响。
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select, text

from app.core import backflow_client
from app.core.db import SessionLocal
from app.core.error_payload import (
    resolve_agent,
    resolve_interface,
    sanitize_words,
    validate_envelope,
)
from app.models.agent import Agent
from app.models.case import TestCase, TestSuite
from app.models.error_backflow_inbox import ErrorBackflowInbox

logger = logging.getLogger(__name__)

_INTERVAL = 30.0
_LOCK = "backflow_pull_loop"

# 驳回原因码（落 inbox.reject_code，供 online 侧与运维定位）
REJECT_CONTENT_GAP = "content_gap"
REJECT_VERSION_DRIFT = "version_drift"
REJECT_CAP_GAP = "offline_cap_gap"
REJECT_EMPTY_WORDS = "empty_words"


async def pull_loop() -> None:
    """后台循环。异常不退出循环（与 scanner_loop 同约定）。"""
    from app.core.config import settings

    if not settings.backflow_enabled:
        logger.info("回流拉取未启用（BACKFLOW_ENABLED=false），跳过启动")
        return
    logger.info("回流拉取启动（interval=%ss, base=%s）", _INTERVAL, settings.backflow_online_base)
    while True:
        try:
            async with SessionLocal() as db:
                got = (await db.execute(text(f"SELECT GET_LOCK('{_LOCK}', 0)"))).scalar()
                if not got:
                    logger.debug("回流锁被其它 worker 持有，跳过本轮")
                    continue  # 释放本 session 连接（锁随之释放）后再 sleep
                try:
                    await pull_once()
                finally:
                    await db.execute(text(f"SELECT RELEASE_LOCK('{_LOCK}')"))
        except Exception:
            logger.exception("回流拉取异常")
        await asyncio.sleep(_INTERVAL)


async def pull_once() -> dict:
    """跑一轮：拉一批、逐条处理。返回本轮统计（验收脚本据此断言）。"""
    data = await backflow_client.pull_payloads()
    payloads = data.get("payloads") or []
    stat = {"fetched": len(payloads), "activated": 0, "rejected": 0, "skipped": 0}
    for envelope in payloads:
        outcome = await _process_envelope(envelope)
        stat[outcome] = stat.get(outcome, 0) + 1
    return stat


async def _process_envelope(envelope: dict) -> str:
    """处理单条信封，返回 activated / rejected / skipped。

    **幂等键 = payload_id**：inbox 里已有该 payload_id 即整条跳过（连同 ack 一起）——
    重复拉取不得重复建 case，也不得重复 ack。
    """
    payload_id = str(envelope.get("payload_id") or "")
    if not payload_id:
        logger.warning("信封缺 payload_id，无法幂等处理，丢弃")
        return "rejected"

    async with SessionLocal() as db:
        existing = (await db.execute(
            select(ErrorBackflowInbox).where(ErrorBackflowInbox.payload_id == payload_id)
        )).scalars().first()
        if existing is not None:
            # 幂等：已 ack 过 → 整条跳过。但**已落库、未 ack**（上轮 ack 失败）必须重放——
            # 否则 online 的 link 永远停在 assembled，每轮都被重新拉到却永远 skipped。
            if existing.status == "case_created" and existing.ack_status == "pending":
                await _ack_active(payload_id, existing.case_id)
            return "skipped"

        ok, errors, verdict = validate_envelope(envelope)
        if not ok:
            return await _reject(db, envelope, payload_id, verdict, "; ".join(errors))

        src = envelope.get("source") or {}
        agent = await resolve_agent(db, src.get("agent") or "")
        if agent is None:
            return await _reject(
                db, envelope, payload_id, REJECT_CAP_GAP, f"agent 未登记：{src.get('agent')!r}"
            )
        interface = await resolve_interface(db, agent, "", src.get("interface") or "")
        if interface is None:
            return await _reject(
                db, envelope, payload_id, REJECT_CAP_GAP,
                f"interface 未登记：{src.get('interface')!r}",
            )

        words = sanitize_words((envelope.get("no_fallback_config") or {}).get("words"))
        if not words:
            # fail-closed：净化后为空 ⇒ 落一条空断言会恒 pass（兜底判定全进候选）
            return await _reject(db, envelope, payload_id, REJECT_EMPTY_WORDS, "净化后词表为空")

        case = await _activate(db, envelope, payload_id, agent, interface, words)
        await db.commit()
        db.expunge(case)

    # ---- commit 之后才 ack（铁律）----
    await _ack_active(payload_id, case.id)
    return "activated"


async def _ack_active(payload_id: str, case_id: int | None) -> None:
    """ack active → 回写 acked。**失败不抛**（与 `_reject` 的 ack 处理对齐）。

    case 已落库是既有事实，抛出去只会让 `pull_once` 整批中断；正确处置是留
    `ack_status=pending`，由 `_process_envelope` 的幂等分支在后续轮次重放。
    （`case_id` 的 int→str 序列化收口在 `backflow_client.ack`，此处传 int 即可。）
    """
    if case_id is None:
        return
    try:
        await backflow_client.ack(payload_id, "active", case_id=case_id)
    except backflow_client.BackflowClientError:
        logger.exception("激活 ack 失败，留 pending 待重放：payload_id=%s", payload_id)
        return
    async with SessionLocal() as db:
        await _mark(db, payload_id, status="active", ack_status="acked", case_id=case_id)
        await db.commit()


async def _activate(db, envelope: dict, payload_id: str, agent: Agent, interface, words: list[str]) -> TestCase:
    """upsert error suite → 建 error case → 记 inbox（同事务，commit 由调用方做）。"""
    suite = (await db.execute(
        select(TestSuite).where(
            TestSuite.agent_id == agent.id, TestSuite.is_error_suite.is_(True)
        )
    )).scalars().first()
    if suite is None:
        suite = TestSuite(agent_id=agent.id, name=f"{agent.name}-error-backflow", is_error_suite=True)
        db.add(suite)
        await db.flush()  # 取 suite.id

    case = TestCase(
        suite_id=suite.id,
        interface_id=interface.id,
        name=f"backflow:{payload_id}",
        input_type="text",
        input=(envelope.get("evidence") or {}).get("input"),
        case_type="regression_error",
        payload_id=payload_id,
        backflow_envelope=envelope,   # 原文留档：出站 trigger_signal_id 的唯一取值来源
        expected=None,                # D1 收窄：error case 无金标
        metrics=None,
        assertions=[{
            "op": "keyword_not_contains",
            "args": {"path": "answer", "keywords": words},
        }],
        status="active",
        is_gold=False,
    )
    db.add(case)
    await db.flush()

    db.add(ErrorBackflowInbox(
        payload_id=payload_id,
        schema_version=str(envelope.get("schema_version") or ""),
        case_type=str(envelope.get("case_type") or ""),
        envelope_json=envelope,
        status="case_created",
        case_id=case.id,
        ack_status="pending",
    ))
    return case


async def _reject(db, envelope: dict, payload_id: str, code: str, detail: str) -> str:
    """驳回：记 inbox → commit → ack invalidated（同样先落库再 ack）。"""
    db.add(ErrorBackflowInbox(
        payload_id=payload_id,
        schema_version=str(envelope.get("schema_version") or ""),
        case_type=str(envelope.get("case_type") or ""),
        envelope_json=envelope,
        status="rejected",
        reject_code=code,
        reject_detail=detail[:512],
        ack_status="pending",
    ))
    await db.commit()

    try:
        await backflow_client.ack(payload_id, "invalidated", reason=f"{code}: {detail}"[:200])
    except backflow_client.BackflowClientError:
        # ack 失败不回滚驳回（已落库是事实），留 ack_status=pending 由对账扫描重放
        logger.exception("驳回 ack 失败，留 pending 待重放：payload_id=%s", payload_id)
        return "rejected"

    async with SessionLocal() as db2:
        await _mark(db2, payload_id, status="rejected", ack_status="acked")
        await db2.commit()
    return "rejected"


async def _mark(db, payload_id: str, *, status: str, ack_status: str, case_id: int | None = None) -> None:
    row = (await db.execute(
        select(ErrorBackflowInbox).where(ErrorBackflowInbox.payload_id == payload_id)
    )).scalars().first()
    if row is None:
        return
    row.status = status
    row.ack_status = ack_status
    if case_id is not None:
        row.case_id = case_id
