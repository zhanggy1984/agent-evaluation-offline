"""批 C3 真库验收：信号 run 终态 → 自动建 error 回归 run（§5.2 / §5.3）。

跑法（宿主直连共享库 33061，env 从容器整体导出后覆写，见 memory `offline-real-db-probe-recipe`）：
    .venv\\Scripts\\python.exe tests\\integration\\auto_schedule_probe.py

**真**的部分：真库、真 manual run 全链（`_run` → 真 `_finish` → 真 `score_run` 达终态）、
真挂接点（`_finish` 末尾 `fire_auto_schedule`）、真 `maybe_auto_schedule`（agent 行锁 +
§5.2 判定表 + 活跃闸 + 真建单）、真 error run 执行与收尾。
**桩**的部分：`execute_case`（HTTP 执行层）、`_probe_before_run`（跑前探测，面向真实 agent
契约；不桩它 manual run 会在探测处早退、`_finish` 根本不执行）。

**本探针证不了什么**（显式声明，勿外推）：
- **并发双触发**：§5.2 的行锁串行是**代码审读**结论。本探针不制造争用、不取锁等待证据
  ⇒ 「两路信号并发只建一个」**未验收**（memory `concurrency-test-needs-contention-proof`：
  并发必须另取「争用真的发生」的证据，否则是假绿）。
- **§5.3 低频补偿对账 / v0.7 R-3 版本差集逐版补建**：本批不做（拆出去另立）。
- 真 HTTP 执行层；scanner 的 pending 租约回收（场景 5 的 pending 行不参与扫描）。
"""
import asyncio
import logging
import sys
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import Settings
from app.models import Agent, AgentInterface, EvalRun, TestCase, TestSuite
from app.runner import orchestrator as orch_mod
from app.runner.executor import CaseOutcome
from app.runner.orchestrator import create_error_regression_run, maybe_auto_schedule

KEYWORD = "抱歉，我暂时无法回答"
GOOD_ANSWER = "您好，这个问题这样处理：先点右上角设置，再选择导出。"  # 非空且不含关键词
TERMINAL = {"completed", "partial_failed", "timeout", "cancelled", "scoring_failed"}
JUDGEABLE = {"completed", "partial_failed"}  # §5.2「已有可判终态 → 不重建」的那一类

# 版本号带本次运行的时分秒：**探针必须可重复跑**——固定版本号时第二次跑会读到上一轮
# 留下的 error run 行（`_fetch_after_fire` 一见到行就返回），把「建了新的」误判成「没建」/「建了多条」。
STAMP = datetime.now().strftime("%H%M%S")
V1, V2, V4, V5 = (f"2026.09.14-c3v1-{STAMP}", f"2026.09.14-c3v2-{STAMP}",
                  f"2026.09.14-c3v4-{STAMP}", f"2026.09.14-c3v5-{STAMP}")
SA, SB, SC, SD = 810001, 810002, 810003, 810004  # 各场景的「信号 run id」

PASS_COUNT = 0
FAIL_COUNT = 0

assert GOOD_ANSWER and KEYWORD not in GOOD_ANSWER


def _check(name: str, got, want) -> None:
    global PASS_COUNT, FAIL_COUNT
    if got == want:
        PASS_COUNT += 1
        print(f"  PASS  {name}")
    else:
        FAIL_COUNT += 1
        print(f"  FAIL  {name}: got={got!r} want={want!r}")


holder: dict = {}


async def _fake_execute(adapter, client, case, timeout_s):
    """HTTP 执行层桩：行为由 holder['fn'] 决定（真 DB / 真判定 / 真收尾）。"""
    return holder["fn"]()


async def _fake_probe(*_a, **_kw) -> bool:
    """跑前探测桩：返回 True ⇒ manual run 继续执行（真探测要真实 agent 契约）。"""
    return True


orch_mod.execute_case = _fake_execute               # 模块全局替换，仅本脚本进程生效
orch_mod.orchestrator._probe_before_run = _fake_probe


async def _ensure_chain(engine) -> tuple[int, int, int, int, int]:
    """幂等建 agent + error suite/case + 普通 suite/case。

    返回 (agent_id, error_suite_id, error_case_id, normal_suite_id, normal_case_id)。
    普通 suite/case 是 **manual run 达终态的必要条件**：`_load_run_cases` 的普通分支要求
    `case_type IS NULL`，而 error case 恰是 case_type 非空 ⇒ 拿 error suite 造 manual run
    会落进「suite 无 active 用例 → completed」的早退路径，**根本不经过挂接点**。
    """
    async with AsyncSession(engine) as s:
        agent = (await s.scalars(select(Agent).where(
            Agent.name == "probe-c3-auto"))).first()
        if agent is None:
            agent = Agent(name="probe-c3-auto", base_url="http://mock.local",
                          adapter_type="config", adapter_config={}, enabled=True,
                          contract_version="1.0")
            s.add(agent)
            await s.flush()
        iface = (await s.scalars(select(AgentInterface).where(
            AgentInterface.agent_id == agent.id,
            AgentInterface.name == "probe-c3-iface"))).first()
        if iface is None:
            iface = AgentInterface(agent_id=agent.id, name="probe-c3-iface",
                                   path="/v1/chat", method="POST", contract_type="sse",
                                   contract_version="1.0", enabled=True)
            s.add(iface)
            await s.flush()
        esuite = (await s.scalars(select(TestSuite).where(
            TestSuite.agent_id == agent.id, TestSuite.is_error_suite.is_(True)))).first()
        if esuite is None:
            esuite = TestSuite(agent_id=agent.id, name="probe-c3-error-suite",
                               is_error_suite=True)
            s.add(esuite)
            await s.flush()
        ecase = (await s.scalars(select(TestCase).where(
            TestCase.suite_id == esuite.id, TestCase.payload_id == "probe-c3-payload"))).first()
        if ecase is None:
            ecase = TestCase(
                suite_id=esuite.id, interface_id=iface.id, name="backflow:probe-c3-payload",
                input_type="text", input="导出按钮点了没反应",
                case_type="regression_error", payload_id="probe-c3-payload",
                backflow_envelope={"source": {"cluster_id": 999101}},
                expected=None, metrics=None,
                assertions=[{"op": "keyword_not_contains",
                             "args": {"path": "answer", "keywords": [KEYWORD]}}],
                status="active", is_gold=False)
            s.add(ecase)
            await s.flush()
        nsuite = (await s.scalars(select(TestSuite).where(
            TestSuite.agent_id == agent.id,
            TestSuite.is_error_suite.is_(False)))).first()
        if nsuite is None:
            nsuite = TestSuite(agent_id=agent.id, name="probe-c3-normal-suite",
                               is_error_suite=False)
            s.add(nsuite)
            await s.flush()
        ncase = (await s.scalars(select(TestCase).where(
            TestCase.suite_id == nsuite.id, TestCase.name == "probe-c3-normal-case"))).first()
        if ncase is None:
            ncase = TestCase(
                suite_id=nsuite.id, interface_id=iface.id, name="probe-c3-normal-case",
                input_type="text", input="怎么导出数据",
                expected=None, metrics=None, assertions=None,
                status="active", is_gold=False)
            s.add(ncase)
            await s.flush()
        ecase.status = "active"        # 复位（场景 6 会临时清断言、不改状态）
        ecase.assertions = [{"op": "keyword_not_contains",
                             "args": {"path": "answer", "keywords": [KEYWORD]}}]
        # ⚠️ id 必须在 commit **之前**取：本探针自建的 AsyncSession 未关 expire_on_commit
        # ⇒ commit 后取属性会触发惰性重载，在 async 上下文外直接 MissingGreenlet。
        ids = (agent.id, esuite.id, ecase.id, nsuite.id, ncase.id)
        await s.commit()
        return ids


async def _create_manual_run(engine, agent_id: int, suite_id: int, case_id: int,
                             version: str) -> int:
    """直插一条 manual run（public create_run 会走鉴权/校验，探针不需要；本批测的是触发链）。"""
    async with AsyncSession(engine) as s:
        run = EvalRun(agent_id=agent_id, suite_id=suite_id, version=version,
                      trigger_type="manual", status="pending", generation=1,
                      run_config={}, case_ids=[case_id])
        s.add(run)
        await s.flush()
        rid = run.id            # commit 前取（同上：expire_on_commit 会失效化实例）
        await s.commit()
        return rid


async def _await_terminal(engine, run_id: int, label: str, tries: int = 60):
    """轮询到终态并返回 (status, 行对象快照字段)。"""
    st = None
    for _ in range(tries):
        await asyncio.sleep(0.5)
        async with AsyncSession(engine) as s:
            st = await s.scalar(select(EvalRun.status).where(EvalRun.id == run_id))
        if st in TERMINAL:
            break
    else:
        raise TimeoutError(f"{label} run {run_id} 未在 {tries}×0.5s 内到终态（最后 {st}）")
    return st


async def _error_runs(engine, agent_id: int, version: str) -> list[EvalRun]:
    async with AsyncSession(engine) as s:
        rows = (await s.scalars(select(EvalRun).where(
            EvalRun.agent_id == agent_id,
            EvalRun.trigger_type == "error_regression",
            EvalRun.version == version).order_by(EvalRun.id))).all()
        return list(rows)


async def _fetch_after_fire(engine, agent_id: int, version: str) -> list[EvalRun]:
    """等 fire-and-forget 的挂接任务落地（收尾后异步建单，不能立刻断言）。"""
    for _ in range(40):
        await asyncio.sleep(0.5)
        rows = await _error_runs(engine, agent_id, version)
        if rows:
            return rows
    return []


async def _set_status(engine, run_id: int, status: str) -> None:
    """外部置终态（等价于 API 取消 / scanner 标 timeout；本探针不跑 scanner）。"""
    async with AsyncSession(engine) as s:
        await s.execute(update(EvalRun).where(EvalRun.id == run_id).values(status=status))
        await s.commit()


async def main() -> None:
    engine = create_async_engine(Settings().sqlalchemy_url)  # 不打印（含口令）
    try:
        agent_id, esuite, ecase, nsuite, ncase = await _ensure_chain(engine)
        print(f"[setup] agent={agent_id} error_suite={esuite} error_case={ecase} "
              f"normal_suite={nsuite} normal_case={ncase}")
        holder["fn"] = lambda: CaseOutcome(unified={"answer": GOOD_ANSWER}, timing={},
                                           status_code=200)

        # --- 场景 1：真 manual run 达终态 → 挂接点自动建单 ---
        manual = await _create_manual_run(engine, agent_id, nsuite, ncase, V1)
        print(f"[场景 1] manual run={manual} version={V1} 跑真链路")
        await orch_mod.orchestrator.start_run(manual)
        st = await _await_terminal(engine, manual, "manual")
        print(f"  manual run 终态 = {st}")
        _check("1 · 信号 run 达终态", st in TERMINAL, True)
        rows = await _fetch_after_fire(engine, agent_id, V1)
        print(f"  自动建出 error run: {[(r.id, r.status, r.trigger_signal_id) for r in rows]}")
        _check("1 · 自动建出恰好 1 条 error run", len(rows), 1)
        if rows:
            _check("1 · consumed 锚 = 信号 run id", rows[0].trigger_signal_id, manual)
            _check("1 · 落在该 agent 的 error suite", rows[0].suite_id, esuite)
            _check("1 · case 集 = runnable error case", rows[0].case_ids, [ecase])
        if rows:
            await _await_terminal(engine, rows[0].id, "error")

        # --- 场景 2：同一信号重入（补偿对账复用同锚）→ 吸收态拦截 ---
        print("[场景 2] 同信号重入")
        got = await maybe_auto_schedule(agent_id, V1, manual)
        _check("2 · 返回值 None", got, None)
        _check("2 · 未新建", len(await _error_runs(engine, agent_id, V1)), 1)

        # --- 场景 3：新信号 + latest 已可判终态 → 不重复回归 ---
        print("[场景 3] 新信号 + 可判终态")
        latest = (await _error_runs(engine, agent_id, V1))[0]
        _check("3 · latest 处于可判终态", latest.status in JUDGEABLE, True)
        got = await maybe_auto_schedule(agent_id, V1, 810099)
        _check("3 · 返回值 None", got, None)
        _check("3 · 未新建", len(await _error_runs(engine, agent_id, V1)), 1)

        # --- 场景 4：坏终态 + 新信号 → 给一次重建机会 ---
        print("[场景 4] 坏终态 + 新信号")
        first = await create_error_regression_run(agent_id=agent_id, suite_id=esuite,
                                                  version=V2, signal_run_id=SA)
        await _await_terminal(engine, first, "error")
        await _set_status(engine, first, "cancelled")   # 外部置坏终态（等价 scanner/API）
        got = await maybe_auto_schedule(agent_id, V2, SB)
        print(f"  重建返回 run={got}（原 {first}）")
        _check("4 · 坏终态 + 新信号 → 建出新 run", got is not None and got != first, True)
        _check("4 · 新 run 的锚 = 新信号", await _scalar_signal(engine, got), SB)
        if got:
            await _await_terminal(engine, got, "error")
        _check("4 · 同 (agent,version) 共 2 条", len(await _error_runs(engine, agent_id, V2)), 2)

        # --- 场景 6（先于 5）：无 runnable case → 创建前门禁不建 ---
        print("[场景 6] 门禁：error case 断言清空")
        async with AsyncSession(engine) as s:
            await s.execute(update(TestCase).where(TestCase.id == ecase).values(assertions=None))
            await s.commit()
        got = await maybe_auto_schedule(agent_id, V4, SD)
        _check("6 · 返回值 None", got, None)
        _check("6 · 未建空 run（latest 保持 None）", len(await _error_runs(engine, agent_id, V4)), 0)
        async with AsyncSession(engine) as s:   # 复位断言，莫留给后续批次
            await s.execute(update(TestCase).where(TestCase.id == ecase).values(
                assertions=[{"op": "keyword_not_contains",
                             "args": {"path": "answer", "keywords": [KEYWORD]}}]))
            await s.commit()

        # --- 场景 5：活跃闸（该 agent 已有活跃 error run）→ 不建 ---
        print("[场景 5] 活跃闸：agent 已有 pending error run")
        async with AsyncSession(engine) as s:
            probe_pending = EvalRun(
                agent_id=agent_id, suite_id=esuite, version=V5,
                trigger_type="error_regression", status="pending", generation=1,
                pinned=True, run_config={}, case_ids=[ecase],
                trigger_signal_id=SC, lease_until=datetime.now() + timedelta(hours=1))
            s.add(probe_pending)
            await s.flush()
            pending_id = probe_pending.id      # commit 前取（同上）
            await s.commit()
        got = await maybe_auto_schedule(agent_id, V5, SD)
        _check("5 · 活跃闸拒建", got, None)
        _check("5 · 该版本无新行", len(await _error_runs(engine, agent_id, V5)), 1)
        # 收尾：**按插入时拿到的 id** 精确清掉本探针插的活跃行。⚠️ 不能用
        # `_error_runs(...)[0]`——同版本可能残留上一轮的行，`[0]` 命中的是旧行，
        # 真插的那条会留在 pending，而活跃闸是**跨版本**生效的 ⇒ 该 agent 此后所有
        # 版本的建单全被拦（本轮实测踩过）。
        await _set_status(engine, pending_id, "cancelled")

        print(f"\nPROBE_RESULT pass={PASS_COUNT} fail={FAIL_COUNT}")
        if FAIL_COUNT:
            sys.exit(1)
    finally:
        await engine.dispose()


async def _scalar_signal(engine, run_id: int | None) -> int | None:
    if run_id is None:
        return None
    async with AsyncSession(engine) as s:
        return await s.scalar(select(EvalRun.trigger_signal_id).where(EvalRun.id == run_id))


if __name__ == "__main__":
    # 打开 INFO：`maybe_auto_schedule` 的每条「不建」分支都带自己的日志（门禁/配额/判定表），
    # 否则探针只看到「没建出来」，不知道是哪道闸拦的。
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    print(f"[probe] start {datetime.now().isoformat(timespec='seconds')}")
    asyncio.run(main())
