"""error run 结果出站推送（§10.1 载荷 / §10.2 鉴权）。

触发点 = `_finish_error_regression` 收尾事务 **commit 之后**（§10.1 逐字「不在收尾事务内」）；
失败 fire-and-forget（超时 5s / 重试 3 次退避 1-2-4s / 全败记 error 后放弃），不阻塞 run 收尾；
响应**仅记日志**，不据回执做业务分支（§10.1）。

**按簇分片**（本批裁定，见 C2 方案「已知偏离 1」）：§10.1 的载荷是 run 级单数叙述，但
online `_find_current_link` 只取该 cluster 的**单个** pending link，而 error run 的 case 集 =
整个 per-agent error suite（**跨簇**）⇒ 只推一笔时 N-1 簇的 link 永远停在 pending。
online 幂等键 `uk_verify_run(link_id, run_id)` 是 **per-link** 的 ⇒ 同 run_id、不同 link 的
N 笔各自落行各自判定，不冲突。

⚠️ 序列化陷阱（online 实码确证，非文档转述）：`run_id` / `case_id` 在 online 是 `str`，而
offline `EvalRun.id` / `TestCase.id` 是 int ⇒ **必须 `str()`**；`trigger_signal_id` 反而是
`int`。两者方向相反，混任一个都是 422。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.core import backflow_client
from app.core.db import SessionLocal
from app.models import Agent, EvalResult, EvalRun, TestCase
from app.runner.cleanup_rules import _TERMINAL  # 终态集单一来源，不另抄一份

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0"
PUSH_MAX_RETRIES = 3                     # 首推之外的重试次数（§10.1「重试 3 次」）
PUSH_BACKOFFS = (1.0, 2.0, 4.0)          # 逐次重试前退避（§10.1 逐字 1s/2s/4s）

# 4xx 里唯一的例外（其余 4xx = 确定性拒绝）：请求超时 / 限流是服务端侧瞬时状态。按 HTTP
# 语义定，**无真机证据**——本项目未观测到 online 回过这两个码。
_RETRYABLE_STATUS = {408, 429}


def _is_retryable(exc: Exception) -> bool:
    """出站异常是否值得重试。

    判据按**码的类别**，不硬编码 401（规格 O-F.8 的判据是「被拒且不重试」，而 online 对
    错误 secret 究竟回 401 还是 403 属跨仓契约、本仓未取证）⇒ 未知不落在正确性路径上。

    - 无 `status_code`（网络层：超时/连接失败）⇒ 可重试，维持原语义；
    - 5xx ⇒ 可重试；`_RETRYABLE_STATUS` 内的 4xx ⇒ 可重试；
    - 其余 4xx（含鉴权 401/403、契约 400/422）⇒ 确定性拒绝，**不重试**。
    """
    code = getattr(exc, "status_code", None)
    if code is None:
        return True
    return code >= 500 or code in _RETRYABLE_STATUS


def _ver_key(value: str) -> tuple[int, ...]:
    """版本号点分 → 数值元组，仅判序用。

    ⚠️ **与 online `app/backflow/verify.py:_ver_key` 同构**（跨仓同构点，本批无法 import 对侧）。
    改任一侧必须同步另一侧，否则 online 的 `_ver_key(fv) > _ver_key(latest)` 发布水位守卫会
    与本仓算出的 `agent_latest_version` 判序不一致。
    """
    parts: list[int] = []
    for p in str(value).lstrip("vV").split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts)


async def _watermarks(db, agent_id: int, run: EvalRun) -> tuple[str, str | None]:
    """两水位字段（§10.1 硬约束：agent 级、**同一次查询产出**、不按 trigger_type 收窄）。

    - `agent_latest_version` = 该 agent **全部终态 run** 版本的按 `_ver_key` 最大值，**含本 run**。
      为什么必须含自身（推导，非猜测）：online 守卫是 `if _ver_key(fix_version) > _ver_key(latest):
      no_progress`（`verify.py:268`）——若不含本 run，推「版本 V 的 run」时 latest < V ⇒ 该簇
      **永远判不出终态**。
    - `prev_terminal_version` = **本 run 之前**（id 更小）该 agent 最近一个终态 run 的版本；
      无前序 → `None`（online 侧 null = 首次，不中断）。online 用它查缺行中断（`verify.py:311`），
      故必须是**时间线上紧邻的前一个**，不是最大值。
    """
    rows = (await db.execute(select(EvalRun.id, EvalRun.version).where(
        EvalRun.agent_id == agent_id,
        EvalRun.status.in_(_TERMINAL),
    ))).all()
    versions = [str(v) for _, v in rows if v]
    latest = max(versions, key=_ver_key) if versions else run.version
    prior = [v for rid, v in rows if rid < run.id and v]
    prev = None
    if prior:
        # 时间线最近 = id 最大（rows 未排序，按 id 取 max 而非依赖行序）
        prior_rows = [(rid, str(v)) for rid, v in rows if rid < run.id and v]
        prev = max(prior_rows, key=lambda p: p[0])[1]
    return latest, prev


def _cases_of_cluster(cases: list[TestCase], rows: dict[int, EvalResult]) -> dict[int, list[dict]]:
    """按 `backflow_envelope['source']['cluster_id']` 分片；无簇 id 的归入 `None` 桶。

    不静默丢：`None` 桶由调用方记 error 日志（该 case 判过但推不出去，等于 online 收不到）。
    """
    out: dict[int, list[dict]] = {}
    for case in cases:
        src = ((case.backflow_envelope or {}).get("source") or {})
        cid = src.get("cluster_id")
        r = rows.get(case.id)
        item = {
            "case_id": str(case.id),            # ⚠️ online 是 str
            "case_type": case.case_type,
            "pass_fail": r.pass_fail if r else "na",
        }
        # 显式降级凭据外发（批 54）：本次回放用的**不是现场输入**，而是平台样例文件，
        # 原引用在 `_substituted_from`（pull_loop 落库时写进 case.input）。
        # 为什么必须外发：替换后的 pass 与「原场景真修好了」在 online 侧**逐字同形**——
        # 用户看到一条 pass、无从知道它跑的是替身输入 ⇒ 假绿，且没有任何判据会红。
        # 只在为真时落键（与本函数 error_type/error_detail 同一写法），故旧载荷形状不变、
        # 旧行在 online 侧默认 False（语义 = 「当时没发生替换」，与事实一致）。
        if isinstance(case.input, dict) and case.input.get("_substituted_from"):
            item["input_substituted"] = True
        if r is not None:
            if r.error_type:
                item["error_type"] = r.error_type
            if r.error_detail:
                item["error_detail"] = r.error_detail
        out.setdefault(cid if isinstance(cid, int) else None, []).append(item)
    return out


def assemble_payload(*, run: EvalRun, agent_name: str, cluster_id: int,
                     cases: list[dict], latest: str, prev: str | None) -> dict:
    """单簇载荷组装（纯函数，便于单测）。字段口径逐条见 §10.1 / C2 方案。"""
    finished = run.finished_at or datetime.now(timezone.utc).replace(tzinfo=None)
    return {
        "schema_version": SCHEMA_VERSION,
        "agent": agent_name,                 # online 侧标识 = 信封 source.agent
        "agent_version": str(run.version),
        "run_id": str(run.id),               # ⚠️ online 是 str
        "run_status": run.status,
        "agent_latest_version": str(latest),
        "prev_terminal_version": str(prev) if prev else None,   # 必填、值可 null
        "trigger_signal_id": cluster_id,     # ⚠️ int（online 侧类型与 run_id 相反）
        "finished_ts": finished.isoformat(),  # naive UTC，不带时区后缀
        "cases": cases,
    }


def self_check(body: dict) -> list[str]:
    """出站前自检（§10.1 三条；不满足 ⇒ 整单被 online 拒）。返回问题列表，空 = 通过。"""
    problems: list[str] = []
    ids = [c.get("case_id") for c in body.get("cases") or []]
    if len(ids) != len(set(ids)):
        problems.append("cases[].case_id 不唯一（online ERR_PULL_0002 整单拒）")
    if body.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version 必须 {SCHEMA_VERSION!r}")
    ts = body.get("finished_ts")
    try:
        datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        problems.append(f"finished_ts 非合法 ISO8601：{ts!r}")
    return problems


async def _push_one(body: dict) -> dict | None:
    """单笔推送 + 重试（首推 + 3 次重试，退避 1/2/4s）。全败返回 None（调用方记日志）。

    **重试只对「可能自愈」的失败生效**（`_is_retryable`）：确定性拒绝（鉴权类 4xx）重试只是
    白等 7 秒退避，且把真实故障淹没在「重试后放弃」的措辞里。
    """
    for attempt in range(PUSH_MAX_RETRIES + 1):
        try:
            resp = await backflow_client.push_results(body)
            logger.debug("结果推送成功 cluster=%s run=%s resp=%s",
                         body.get("trigger_signal_id"), body.get("run_id"), resp)
            return resp
        except Exception as exc:  # noqa: BLE001 — fire-and-forget：任何异常都不许冒泡打断收尾
            if not _is_retryable(exc):
                logger.error("结果推送被确定性拒绝，不重试：cluster=%s run=%s status=%s err=%s",
                             body.get("trigger_signal_id"), body.get("run_id"),
                             getattr(exc, "status_code", None), exc)
                return None
            if attempt >= PUSH_MAX_RETRIES:
                logger.error("结果推送三次重试后放弃：cluster=%s run=%s err=%s",
                             body.get("trigger_signal_id"), body.get("run_id"), exc)
                return None
            await asyncio.sleep(PUSH_BACKOFFS[attempt])
    return None


async def push_run_results(run_id: int) -> None:
    """推送一条 error run 的全部簇。逐簇独立成败（一簇失败不影响其余簇）。"""
    async with SessionLocal() as db:
        run = await db.get(EvalRun, run_id)
        if run is None:
            logger.error("结果推送跳过：run %s 不存在", run_id)
            return
        agent = await db.get(Agent, run.agent_id)
        rows = (await db.execute(select(EvalResult).where(
            EvalResult.run_id == run_id))).scalars().all()
        case_ids = [r.case_id for r in rows] or [0]
        cases = (await db.execute(select(TestCase).where(
            TestCase.id.in_(case_ids)))).scalars().all()
        latest, prev = await _watermarks(db, run.agent_id, run)
        by_case = {r.case_id: r for r in rows}
        buckets = _cases_of_cluster(list(cases), by_case)
        agent_name = ((next(iter(cases)).backflow_envelope or {}).get("source") or {}).get("agent") \
            if cases else None
    if not cases:
        # §10.1 说四终态均推送，但空集 run（走 `_run` 的 `not cases` 早返回、不经收尾）本就到不了
        # 这里；真到这里也没有 case ⇒ 取不到 cluster_id，payload 无从组装。
        logger.warning("结果推送跳过：run %s 无结果行（无簇 id 可填触发关联）", run_id)
        return
    orphan = buckets.pop(None, None)
    if orphan:
        logger.error("结果推送丢弃 %d 条无 cluster_id 的 case（run=%s）——online 收不到这些行",
                     len(orphan), run_id)
    for cluster_id, items in buckets.items():
        body = assemble_payload(run=run, agent_name=agent_name, cluster_id=cluster_id,
                                cases=items, latest=latest, prev=prev)
        problems = self_check(body)
        if problems:
            logger.error("结果推送自检不过、丢弃：cluster=%s run=%s problems=%s",
                         cluster_id, run_id, problems)
            continue
        await _push_one(body)


def fire_push(run_id: int) -> None:
    """fire-and-forget 触发（§10.1 收尾 commit 之后）。异常在任务内消化，不冒泡到收尾。"""
    try:
        task = asyncio.get_running_loop().create_task(push_run_results(run_id))
    except RuntimeError:  # 无运行中 loop（同步上下文调用）——记日志不抛
        logger.error("结果推送无法调度：无运行中的事件循环（run=%s）", run_id)
        return
    # 持引用防 GC（CPython 对无引用 task 的既有陷阱）；异常已在 push_run_results 内消化，
    # 此处兜底防未预期异常静默。
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


_TASKS: set = set()
