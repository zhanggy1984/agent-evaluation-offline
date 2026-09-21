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

from app.adapters.engine import check_input_wiring
from app.core import backflow_client
from app.core.db import SessionLocal
from app.core.error_payload import (
    resolve_agent,
    resolve_interface,
    sanitize_words,
    validate_envelope,
)
from app.core.probe import validate_probe_input
from app.models.agent import Agent
from app.models.case import TestCase, TestSuite
from app.models.error_backflow_inbox import ErrorBackflowInbox

logger = logging.getLogger(__name__)

_INTERVAL = 30.0
_LOCK = "backflow_pull_loop"

# 驳回原因码 —— **本仓内部码，只落 `inbox.reject_detail` 前缀，不落 `reject_code`**。
# 四个码各自对应一个不同的处置动作，粒度不压缩。
REJECT_CONTENT_GAP = "content_gap"
REJECT_VERSION_DRIFT = "version_drift"
REJECT_CAP_GAP = "offline_cap_gap"
REJECT_EMPTY_WORDS = "empty_words"
# 批 A：evidence.input 引用的样例文件在平台 uploads 内不存在。**必须映射到
# `offline_cap_gap` 而非 `online_content_gap`**——文件在**离线侧**的 uploads 里，
# online admin 补不了；若落 `online_content_gap`，`_needs_reprocess` 对
# `online_content_gap` 是「任意 ack_status 都重处理」⇒ 每轮重拉、每轮同一原因驳回，
# **无限循环**。落 `offline_cap_gap` 则只在 ack_status∈{none,pending} 复位，不循环。
REJECT_MISSING_SAMPLE = "missing_sample_file"

# 内部码 → online `ack.invalidate_reason` 值域（online 只有 3 个粗码，本仓内部 4 个细码）。
# online 侧校验是**严格相等**且原样落库，故只能发纯码；细粒度靠本仓 inbox.reject_detail 承载。
#
# **本表的值就是 `inbox.reject_code` 落库值**（详设 §3.3 列值域 / §5.5 verdict 语义逐字：
# `reject_code=online_content_gap`）。这两处必须同源——不是巧合：§5.9 requeue 重处理谓词
# （`rejected(online_content_gap)`）与 §5.8 `CAP_GAP_PROBE`（`reject_code=='offline_cap_gap'`）
# 都按**该列**筛选，存内部细码会让 requeue 谓词匹配不上（真机复核时发现，此前实现存的是内部码）。
#
# **三码折叠到一个 online_content_gap 是设计已定的**（详设 §5.5 verdict 语义：`content_gap`
# 与 `version_drift` **同 reject_code**，靠 reject_detail 区分），依据 = register R-7
# 2026-09-11 重估：`version_drift` 的两个触发条件在同一版本对内**无产出对象**（online 恒用
# `build_envelope` 写常量 SCHEMA_VERSION），细分码「无对象可区分」⇒ 该腿已作废，勿复活。
#   content_gap   → 载荷字段缺/畸形，admin 补齐后 requeue 可愈
#   empty_words   → 空词表源自 online `dict_config.fallback_utterance`，同属 requeue 可愈
#   version_drift → 同码，但 detail 注明「版本不识别，需 offline 升级/联调同步，requeue 不愈」
#   offline_cap_gap → 须离线侧补登记 agent/interface。**必须原样**：R2 自愈例外
#                     （invalidated→active）判的是该列 == "offline_cap_gap" 严格相等。
_ONLINE_REASON = {
    REJECT_CONTENT_GAP: "online_content_gap",
    REJECT_EMPTY_WORDS: "online_content_gap",
    REJECT_VERSION_DRIFT: "online_content_gap",
    REJECT_CAP_GAP: "offline_cap_gap",
    # 与 cap_gap 同粗码、靠 detail 前置的内部细码区分——沿用 content_gap/empty_words/
    # version_drift 三码共用 online_content_gap 的既有做法（见上方细码表注释）。
    REJECT_MISSING_SAMPLE: "offline_cap_gap",
}


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
            # 两条落库路径都要覆盖：激活（case_created）与驳回（rejected）。**漏掉驳回那条
            # 是实测缺陷**：驳回归档后 ack 永不重发，link 恒 assembled 且无限重拉。
            if existing.ack_status == "pending":
                if existing.status == "case_created":
                    await _ack_active(payload_id, existing.case_id)
                elif existing.status == "rejected" and existing.reject_code:
                    # 该列存的就是 online 粗码（见 `_reject`），故可直接透传，**不再过映射表**——
                    # 过了反而是 KeyError（表键是内部细码）。
                    await _ack_rejected(payload_id, existing.reject_code)
                return "skipped"

            if not _needs_reprocess(existing):
                return "skipped"

            # requeue 重处理（§6.5 判据第一条，此前漏实现）：admin 在 online requeue 后
            # assembled_ts 刷新，该 payload_id 再次落进增量窗口被拉回。此时**不得复用旧驳回
            # 结论**，否则「现场已修正」永远推不进来——真机实证：link 恒 assembled、每轮被
            # 重拉却每轮 skipped。复位后不 return，直接落回下面的自检流程。
            _reset_for_reprocess(existing, envelope)

        verdict, detail, ctx = await _self_check(db, envelope)
        if verdict is not None:
            return await _reject(db, envelope, payload_id, verdict, detail)
        agent, interface, words, input_value = ctx

        case = await _activate(db, envelope, payload_id, agent, interface, words, input_value)
        await db.commit()
        db.expunge(case)

    # ---- commit 之后才 ack（铁律）----
    await _ack_active(payload_id, case.id)
    return "activated"


# S1（批 B）：**允许换用平台样例文件继续回归**的错误类型。
#
# 划界判据 = 「错误的成因是否可能由输入内容决定」。名单内 = 成因在基础设施层，
# 输入换不换都不改变它发生的概率 ⇒ 用同形状的样例回放是**有效的回归**。
#
# ⚠️ **只列有实测支撑的类型，不许凭推理扩表**：
#   llm_timeout     ← 簇 3861 同输入「4 fail → 2 pass」（2026-09-16，成因确不在输入）
#   llm_connection  ← 14 条簇全部用**原输入**跑通（S1 前无替换通道，输入==现场输入）
# 这两个覆盖了全库 18 条簇的 100%（实测）。
#
# **未纳入**（零发生，待观测后再议，不是「已排除」）：
#   llm_rate_limit / db_error / redis_error ← 成因同样在基础设施层，但**全库零簇**，
#                                             纳入与否当下效果等价 ⇒ 不预支。
#                                             将来真出现：加一项 + 加一条测试即可。
# **禁止纳入**（纳错的方向是**静默假绿**，不可见）：
#   llm_context_exceeded   ← 输入太长，换样例反而掩盖真问题
#   llm_interface_business / external_non_llm ← 业务/外部依赖语义失败，与输入强相关
#   llm_empty_response / llm_parse_error      ← 边界（多为瞬态但可能被内容触发），代价不对称
#   llm_other                                 ← **兜底值域**，纳入即静默假绿
TRANSIENT_ERROR_TYPES = frozenset({"llm_timeout", "llm_connection"})


async def _resolve_sample_file(db, agent: Agent) -> str | None:
    """取该 agent 的「平台自有且实存」的样例文件路径；取不到返回 None（fail-closed）。

    来源 = 该 agent **非 error suite** 中首条满足「`input_type == "file"` ∧ `input.file_path`
    非空 ∧ 该文件真在 uploads 内」的用例的 `file_path`（按 id 升序，确定性）。

    两条约束各有理由：

    1. **必须排除 error suite**：那些 case 的 `file_path` 正是**本次要替换掉的那条坏路径**
       （error case 的 input 就是从信封原样搬来的），纳入即循环论证——用坏路径替换坏路径。
    2. **必须逐个试到「存在」为止，不是取首条就完**：样例文件可能被人删掉。取首条不验，
       一旦它被删，替换后的路径依旧不存在 ⇒ run 期照样 `FileNotFoundError` ⇒ **又回到
       静默 `na`**，而装载期这次会「以为已经修好了」。逐个验则退化为 None → 干净驳回。

    ⚠️ 判据复用探测期同一条 `validate_probe_input`（逃逸白名单 + isfile）。
    """
    rows = (await db.execute(
        select(TestCase)
        .join(TestSuite, TestCase.suite_id == TestSuite.id)
        .where(
            TestSuite.agent_id == agent.id,
            TestSuite.is_error_suite.is_(False),
            TestCase.input_type == "file",
            TestCase.status == "active",
        )
        .order_by(TestCase.id)
    )).scalars().all()

    for case in rows:
        inp = case.input
        if not isinstance(inp, dict) or not inp.get("file_path"):
            continue
        if validate_probe_input(inp) is None:      # 逃逸 + 存在 一起过，才算可用
            return str(inp["file_path"])
    return None


async def _self_check(db, envelope: dict):
    """结构自检 + 能力解析（登记 / 词表 / 装载闸）。

    返回 `(verdict, detail, ctx)`：`verdict is None` = 通过，
    `ctx = (agent, interface, words, input_value)`；否则 `ctx is None`、`verdict` 为内部细码、
    `detail` 为可读原因（只落 `reject_detail`）。

    ⚠️ **`ctx` 里的 `input_value` 才是该建单用的输入**，**不是** `envelope.evidence.input`——
    S1 分流可能已把它换成平台样例（见下）。调用方**不得**自行重读信封：`evidence.input`
    被读两次（此处校验 / `_activate` 建单）而替换只发生在其中一处时，**替换会生效在错的地方**
    ——落库的仍是坏路径，且没有任何判据会红（`branch-outputs-fragmented-across-clauses` 同型）。
    故 `ctx` 携带它、`_activate` 消费它，是本设计的**关键约束而非风格偏好**。

    **两处消费方共用**（拉取路径 `_process_envelope` 与 R-8 探测态 `cap_gap_probe`）——
    判据必须**同源**：分开写必然分叉，而在探测态上分叉的后果是「拉取时驳回、探测时放行」，
    等于把当初驳回的行按已放宽的判据建 case（R-27 装载闸就是这么被漏掉的同型教训）。
    """
    ok, errors, verdict = validate_envelope(envelope)
    if not ok:
        return verdict, "; ".join(errors), None

    src = envelope.get("source") or {}
    agent = await resolve_agent(db, src.get("agent") or "")
    if agent is None:
        return REJECT_CAP_GAP, f"agent 未登记：{src.get('agent')!r}", None
    interface = await resolve_interface(db, agent, "", src.get("interface") or "")
    if interface is None:
        return REJECT_CAP_GAP, f"interface 未登记：{src.get('interface')!r}", None

    words = sanitize_words((envelope.get("no_fallback_config") or {}).get("words"))
    if not words:
        # fail-closed：净化后为空 ⇒ 落一条空断言会恒 pass（兜底判定全进候选）
        return REJECT_EMPTY_WORDS, "净化后词表为空", None

    # R-27 装载闸：evidence.input 的形状必须能喂进该 agent 的 adapter 模板。不可达时
    # 渲染侧会**静默**原样发出占位符（`render_template._sub`），被测 agent 回兜底话术 ⇒
    # 断言恒 pass ⇒ 假绿写入 online。故在此 fail-closed 驳回，与「词表净化后为空」同型论证。
    input_value = (envelope.get("evidence") or {}).get("input")
    problems = check_input_wiring(agent.adapter_config, input_value)
    if problems:
        return REJECT_CONTENT_GAP, "；".join(problems), None

    # 样例文件存在闸（批 A）：形状可达 ≠ 文件在。装载期不拦，坏路径会被**原样收下**，
    # 直到 run 期 `multipart_files` 打开文件才 FileNotFoundError ⇒ 该 case 记
    # `na` + `contract_error`，**而装载期一声不吭**（2026-09-21 实证：cc case 4085
    # 用的 /app/uploads/cc_gen_good.pdf 平台没有，静默卡死在 ④ 之前）。
    #
    # 顺序在形状闸**之后**：`file_path` 未被模板引用时它是无关字段，不该因它驳回；
    # 只有确认它会被渲染进请求（形状闸通过）才谈得上「引用不存在的文件」。
    #
    # 判据复用探测期同一条（`core/probe.py`）：逃逸白名单 + isfile + 可读消息。
    # 非文件型输入（无 `file_path` 键，如 cs/gq/sp 的 `{"content": ...}`）返回 None，
    # 完全不受此闸影响 —— 这道闸是 cc 一家的问题，不是四家的。
    file_err = validate_probe_input(input_value)
    if file_err:
        # S1（批 B）分流：瞬态类错误的成因与**输入内容**无关（实测依据见
        # TRANSIENT_ERROR_TYPES），故换平台样例文件继续回归是**有效回归**；
        # 内容相关类换样例只会掩盖真问题 ⇒ 一律干净驳回。
        if (src.get("error_type") or "") not in TRANSIENT_ERROR_TYPES:
            return REJECT_MISSING_SAMPLE, file_err, None
        sample = await _resolve_sample_file(db, agent)
        if sample is None:
            # fail-closed：取不到可用样例就不能「假装替换成功」——那会让坏路径原样落库，
            # 退化成批 A 之前那个静默 `na`。宁可干净驳回（可见、可补救）。
            return REJECT_MISSING_SAMPLE, f"{file_err}；且无可用样例文件可替换", None
        # `_substituted_from` = 显式降级凭据：落库的 input 与信封原文因此必然不同，
        # 差异本身就是「这条用的是替身输入」的证据（零新列，DB 与 API 都看得到）。
        input_value = {
            **input_value,
            "file_path": sample,
            "_substituted_from": input_value.get("file_path"),
        }
        logger.warning(
            "回放输入已降级为平台样例：payload_id=%s agent=%s error_type=%s %s -> %s",
            envelope.get("payload_id"), src.get("agent"), src.get("error_type"),
            input_value.get("_substituted_from"), sample,
        )

    return None, "", (agent, interface, words, input_value)


def _needs_reprocess(row: ErrorBackflowInbox) -> bool:
    """§6.5 判据「重处理 vs 幂等跳过」：rejected 行按驳回码分流。

    - `online_content_gap`（online 现场内容缺/畸形）：ack_status **任意**都重处理——admin
      requeue 刷新 assembled_ts 后该 payload 才被重拉，重复落进窗口即「内容已刷新」的信号
      （游标按 assembled_ts 推进）。此处**不比 assembled_ts**：文档即此语义，多一层时间解析
      判据就多一个「该重处理却被静默跳过」的失败点，比白跑一轮难发现得多。
    - `offline_cap_gap`（offline 能力缺）：R-8 修订（phase2 v0.7 §2.3 注④）收窄为仅
      ack_status∈{none,pending}（首次驳回 / 对账未闭环）复位；**已 acked 的 invalidated
      闭环行不复位、不重发 invalidated**——那是「等被测 agent 接入」的探测态（§5.8）。
    - `manual_invalidate` 及其它：不复位（人工判无效，重推归 online admin 语义）。
    """
    if row.status != "rejected":
        return False
    if row.reject_code == "online_content_gap":
        return True
    return row.reject_code == "offline_cap_gap" and row.ack_status in ("none", "pending")


def _reset_for_reprocess(row: ErrorBackflowInbox, envelope: dict) -> None:
    """复位待重处理：清驳回痕迹 + ack 复位 none + 覆盖留档信封。

    同 payload_id 的内容已刷新，留档必须跟着走——否则审计链上「该 payload 当时是什么」
    与实际重组装的载荷对不上（§6.5 明写「覆盖 inbox envelope_json 留档」）。
    只改字段不 commit：由调用方（`_activate` / `_reject`）的同事务收口。
    """
    row.status = "new"
    row.ack_status = "none"
    row.reject_code = None
    row.reject_detail = None
    row.case_id = None
    row.schema_version = str(envelope.get("schema_version") or "")
    row.case_type = str(envelope.get("case_type") or "")
    row.envelope_json = envelope


async def _inbox_put(db, payload_id: str, **fields) -> ErrorBackflowInbox:
    """inbox 行「有则更新、无则插入」（§5.5 step1 的 upsert 语义）。

    重处理路径下该行已存在，直接 `db.add` 会撞 `uk_inbox_payload`——这是「重处理漏实现」
    的连带缺口：补了判据不补这个，重处理一跑就 IntegrityError。先查后写：单实例 +
    `_LOCK` 拉取互斥下无并发写同一 payload 的窗口（与 online `put_config` 对全局键的
    论证同型）。
    """
    row = (await db.execute(
        select(ErrorBackflowInbox).where(ErrorBackflowInbox.payload_id == payload_id)
    )).scalars().first()
    if row is None:
        row = ErrorBackflowInbox(payload_id=payload_id)
        db.add(row)
    for key, value in fields.items():
        setattr(row, key, value)
    return row


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


async def _activate(db, envelope: dict, payload_id: str, agent: Agent, interface,
                    words: list[str], input_value) -> TestCase:
    """upsert error suite → 建 error case → 记 inbox（同事务，commit 由调用方做）。

    `input_value` 由 `_self_check` 的 `ctx` 传入，**不在此重读 `envelope.evidence.input`**：
    S1 分流可能已在自检里把它换成平台样例，重读会让替换失效（见 `_self_check` docstring 的
    警告）。`envelope` 仍原样留档进 `backflow_envelope`——**留档必须保持原文**，它是出站
    ⑤环 `trigger_signal_id` 的唯一取值来源，不得被替换污染。
    """
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
        input=input_value,
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

    await _inbox_put(
        db, payload_id,
        schema_version=str(envelope.get("schema_version") or ""),
        case_type=str(envelope.get("case_type") or ""),
        envelope_json=envelope,
        status="case_created",
        case_id=case.id,
        ack_status="pending",
    )
    return case


async def _reject(db, envelope: dict, payload_id: str, code: str, detail: str) -> str:
    """驳回：记 inbox → commit → ack invalidated（同样先落库再 ack）。

    `code` 是本仓内部细码；**落 `reject_code` 的是映射后的 online 粗码**（详设 §3.3 列值域），
    内部细码前置进 `reject_detail` 保住粒度（§5.5：「detail 注明」）。
    """
    await _inbox_put(
        db, payload_id,
        schema_version=str(envelope.get("schema_version") or ""),
        case_type=str(envelope.get("case_type") or ""),
        envelope_json=envelope,
        status="rejected",
        reject_code=_ONLINE_REASON[code],
        reject_detail=f"{code}: {detail}"[:512],
        ack_status="pending",
    )
    await db.commit()
    await _ack_rejected(payload_id, _ONLINE_REASON[code])
    return "rejected"


async def _ack_rejected(payload_id: str, online_reason: str) -> None:
    """发 invalidated ack 并回写 acked。**失败不抛**（留 pending，由幂等分支下轮重放）。

    入参是**已映射好的 online 粗码**（与 `reject_code` 列同值）：online 侧是严格相等校验，
    且该值会原样落进 `link.invalidate_reason`（R2 自愈例外的比较对象）。
    人读明细只留在本地 `reject_detail`，**不许拼进来**。
    """
    try:
        await backflow_client.ack(payload_id, "invalidated", reason=online_reason)
    except backflow_client.BackflowClientError:
        # 已落库是事实，不回滚；留 ack_status=pending 由 `_process_envelope` 幂等分支重放
        logger.exception("驳回 ack 失败，留 pending 待重放：payload_id=%s", payload_id)
        return

    async with SessionLocal() as db2:
        await _mark(db2, payload_id, status="rejected", ack_status="acked")
        await db2.commit()


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
