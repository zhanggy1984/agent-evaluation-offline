"""批 C5 真库验收：R-3 版本差集对账（§7.3 / phase2 §5.3 v0.7）。

跑法（宿主直连共享库，env 从容器整体导出后覆写，见 memory `offline-real-db-probe-recipe`）：
    .venv\\Scripts\\python.exe tests\\integration\\reconcile_probe.py

**真的部分**：真库、真 `reconcile_once()` 全链（真扫描谓词 → 真差集 SQL → 真
`maybe_auto_schedule`：agent 行锁 + 空集门禁 + §5.2 skip 表 + 活跃闸 + 真建单）、真
`GET_LOCK` 语义。
**桩的部分**：`execute_case`（HTTP 执行层）—— 建出的 error run 会 fire-and-forget 执行，
不桩它会向 mock.local 真发请求；本批验的是「建不建」，不是「跑得对不对」。

**本探针证不了什么**（显式声明，勿外推）：
- **跨进程真多实例单飞**：只验 `GET_LOCK` 语义，未起两个真 worker 进程；
- **真实洪峰量级下的收敛时间**：3 版 + 手动逐轮推进，与真实发版节奏不同（容量型）；
- **§6.3 backfill**（本批不做）；**§5.7/§5.8 自愈扫描**（不在本批）。

每个场景用**独立 agent**（避免同一 agent 的差集互相干扰，也避免活跃闸跨场景串扰）。
"""
import asyncio
import logging
import sys
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import Settings
from app.models import Agent, AgentInterface, EvalRun, TestCase, TestSuite
from app.runner import orchestrator as orch_mod
from app.runner import reconcile_loop as rl
from app.runner.executor import CaseOutcome

KEYWORD = "抱歉，我暂时无法回答"
GOOD_ANSWER = "您好，这个问题这样处理：先点右上角设置，再选择导出。"
LOCK = "backflow_reconcile_loop"
# 版本号带时分秒：探针必须可重复跑（固定版本号会读到上轮残留行，把「没建」误判成「建了」）
STAMP = datetime.now().strftime("%H%M%S")

PASS_COUNT = 0
FAIL_COUNT = 0


def _check(name: str, got, want) -> None:
    global PASS_COUNT, FAIL_COUNT
    if got == want:
        PASS_COUNT += 1
        print(f"  PASS  {name}")
    else:
        FAIL_COUNT += 1
        print(f"  FAIL  {name}: got={got!r} want={want!r}")


def _v(tag: str) -> str:
    return f"2026.09.14-c5{tag}-{STAMP}"


async def _fake_execute(adapter, client, case, timeout_s):
    """HTTP 执行层桩：建出的 error run 立即得一个通过答案，快速收敛（不影响本批判据）。"""
    return CaseOutcome(unified={"answer": GOOD_ANSWER}, timing={}, status_code=200)


async def _fake_probe(*_a, **_kw) -> bool:
    return True


orch_mod.execute_case = _fake_execute
orch_mod.orchestrator._probe_before_run = _fake_probe


async def _ensure_agent(engine, name: str) -> tuple[int, int, int]:
    """幂等建 agent + error suite + 1 条 runnable error case，返回 (agent_id, suite_id, case_id)。"""
    async with AsyncSession(engine) as s:
        agent = (await s.scalars(select(Agent).where(Agent.name == name))).first()
        if agent is None:
            agent = Agent(name=name, base_url="http://mock.local", adapter_type="config",
                          adapter_config={}, enabled=True, contract_version="1.0")
            s.add(agent)
            await s.flush()
        iface = (await s.scalars(select(AgentInterface).where(
                AgentInterface.agent_id == agent.id,
                AgentInterface.name == "probe-c5-iface"))).first()
        if iface is None:
            iface = AgentInterface(agent_id=agent.id, name="probe-c5-iface", path="/v1/chat",
                                   method="POST", contract_type="sse", contract_version="1.0",
                                   enabled=True)
            s.add(iface)
            await s.flush()
        suite = (await s.scalars(select(TestSuite).where(
            TestSuite.agent_id == agent.id, TestSuite.is_error_suite.is_(True)))).first()
        if suite is None:
            suite = TestSuite(agent_id=agent.id, name=f"{name}-error-suite", is_error_suite=True)
            s.add(suite)
            await s.flush()
        case = (await s.scalars(select(TestCase).where(
            TestCase.suite_id == suite.id, TestCase.payload_id == f"{name}-payload"))).first()
        if case is None:
            case = TestCase(suite_id=suite.id, interface_id=iface.id, name=f"backflow:{name}",
                            input_type="text", input="导出按钮点了没反应",
                            case_type="regression_error", payload_id=f"{name}-payload",
                            backflow_envelope={"source": {"cluster_id": 999201}}, expected=None,
                            metrics=None,
                            assertions=[{"op": "keyword_not_contains",
                                         "args": {"path": "answer", "keywords": [KEYWORD]}}],
                            status="active", is_gold=False)
            s.add(case)
            await s.flush()
        ids = (agent.id, suite.id, case.id)   # commit 前取（expire_on_commit 会失效化实例）
        await s.commit()
        return ids


async def _add_run(engine, agent_id: int, suite_id: int, version: str, trigger: str,
                   status: str, signal_id: int | None = None) -> int:
    """直插一条 run（本批验的是对账读口径 + 建单，不经 API/执行链）。"""
    async with AsyncSession(engine) as s:
        run = EvalRun(agent_id=agent_id, suite_id=suite_id, version=version,
                      trigger_type=trigger, status=status, generation=1, run_config={},
                      case_ids=[], trigger_signal_id=signal_id)
        s.add(run)
        await s.flush()
        rid = run.id
        await s.commit()
        return rid


async def _set_status(engine, run_id: int, status: str) -> None:
    async with AsyncSession(engine) as s:
        run = await s.get(EvalRun, run_id)
        run.status = status
        await s.commit()


async def _error_run_versions(engine, agent_id: int) -> set:
    async with AsyncSession(engine) as s:
        rows = (await s.execute(select(EvalRun.version).where(
            EvalRun.agent_id == agent_id,
            EvalRun.trigger_type == "error_regression"))).scalars().all()
    return set(rows)


async def _last_error_run(engine, agent_id: int, version: str):
    async with AsyncSession(engine) as s:
        return (await s.scalars(select(EvalRun).where(
            EvalRun.agent_id == agent_id, EvalRun.trigger_type == "error_regression",
            EvalRun.version == version).order_by(EvalRun.id.desc()))).first()


async def _settle(seconds: float = 2.0) -> None:
    """等 fire-and-forget 的执行任务跑完（避免下轮读到期中态）。"""
    await asyncio.sleep(seconds)


async def scenario_1(engine) -> None:
    """§12.1 #5：缺版补建 + 幂等（manual 与 held_out 都作信号）。"""
    print("\n[场景 1] 缺版补建 + 幂等（§12.1 #5）")
    agent, suite, _ = await _ensure_agent(engine, "probe-c5-s1")
    va, vb, vc = _v("s1a"), _v("s1b"), _v("s1c")
    ra = await _add_run(engine, agent, suite, va, "manual", "completed")
    rb = await _add_run(engine, agent, suite, vb, "held_out", "completed")
    rc = await _add_run(engine, agent, suite, vc, "manual", "partial_failed")
    # va/vb 已有 error run（v c 没有）——注意 vb 的 error run 故意用 **cancelled** 坏终态：
    # 它仍应视为「有 run」，证明 have_versions 不按状态过滤
    await _add_run(engine, agent, suite, va, "error_regression", "completed", signal_id=ra)
    await _add_run(engine, agent, suite, vb, "error_regression", "cancelled", signal_id=rb)

    before = await _error_run_versions(engine, agent)
    built = await rl.reconcile_once()
    await _settle()
    after = await _error_run_versions(engine, agent)
    _check("1 · 只补出缺的那 1 版（va/vb 不算缺）", after - before, {vc})
    new_run = await _last_error_run(engine, agent, vc)
    _check("1 · 锚 = 该版终态信号 run id", new_run.trigger_signal_id if new_run else None, rc)
    _check("1 · 返回补建列表长度", len(built), 1)

    built2 = await rl.reconcile_once()
    await _settle()
    after2 = await _error_run_versions(engine, agent)
    _check("1 · 再跑一轮无新建（幂等：差集已闭合）", after2, after)
    _check("1 · 第二轮返回空", built2, [])


async def scenario_2(engine) -> None:
    """phase2 §11.2 场景 17 洪峰：活跃闸拒建 → 逐轮每轮至多补 1 版，按到达序。"""
    print("\n[场景 2] 洪峰丢版本洞：v1 占槽 → v2/v3 逐轮补建")
    agent, suite, _ = await _ensure_agent(engine, "probe-c5-s2")
    v1, v2, v3 = _v("s2v1"), _v("s2v2"), _v("s2v3")
    r1 = await _add_run(engine, agent, suite, v1, "manual", "completed")
    # v1 的 error run 停在 pending（占掉 §5.2 活跃闸的唯一配额）
    e1 = await _add_run(engine, agent, suite, v1, "error_regression", "pending", signal_id=r1)
    await _add_run(engine, agent, suite, v2, "manual", "completed")   # 锚早于 v3
    await _add_run(engine, agent, suite, v3, "manual", "completed")

    built = await rl.reconcile_once()
    await _settle()
    _check("2 · 活跃闸满时一轮不补建", built, [])

    await _set_status(engine, e1, "completed")      # 放开配额
    b1 = await rl.reconcile_once()
    await _settle()
    _check("2 · 放开后首轮补建 1 个（至多 1）", len(b1), 1)
    _check("2 · 首轮补的是到达序更早的 v2", await _error_run_versions(engine, agent) >= {v2}, True)
    _check("2 · 首轮尚未补 v3", v3 in await _error_run_versions(engine, agent), False)

    b2 = await rl.reconcile_once()
    await _settle()
    _check("2 · 次轮补出 v3", v3 in await _error_run_versions(engine, agent), True)
    _check("2 · 次轮新建数", len(b2), 1)

    b3 = await rl.reconcile_once()
    await _settle()
    _check("2 · 第三轮收敛（无缺版）", b3, [])


async def scenario_3(engine) -> None:
    """自指防护：只有 error run、无 manual/held_out 信号的版本不作差集来源。"""
    print("\n[场景 3] 自指防护：无信号版本的 error run 不被再补建")
    agent, suite, _ = await _ensure_agent(engine, "probe-c5-s3")
    v = _v("s3")
    # 只有 error run，没有任何 manual/held_out run
    await _add_run(engine, agent, suite, v, "error_regression", "timeout")
    before = await _error_run_versions(engine, agent)
    built = await rl.reconcile_once()
    await _settle()
    _check("3 · 无信号版本不产差集（不建）", built, [])
    _check("3 · 版本集合未变", await _error_run_versions(engine, agent), before)


async def scenario_4(engine) -> None:
    """终态过滤：pending/running 的 manual run 不是「已到终态」⇒ 不进信号集。"""
    print("\n[场景 4] 未终态信号不入差集")
    agent, suite, _ = await _ensure_agent(engine, "probe-c5-s4")
    v = _v("s4")
    await _add_run(engine, agent, suite, v, "manual", "running")
    _check("4 · running 的 manual run 不产差集", await rl.reconcile_once(), [])
    await _settle()


async def scenario_5(engine) -> None:
    """单例语义：直接测 `GET_LOCK` 争用（不靠「跑两遍都绿」）。"""
    print("\n[场景 5] GET_LOCK 单例（真争用）")
    a = create_async_engine(Settings().sqlalchemy_url)
    b = create_async_engine(Settings().sqlalchemy_url)
    try:
        async with a.connect() as ca, b.connect() as cb:
            got_a = (await ca.execute(text(f"SELECT GET_LOCK('{LOCK}', 0)"))).scalar()
            got_b = (await cb.execute(text(f"SELECT GET_LOCK('{LOCK}', 0)"))).scalar()
            _check("5 · 第一个连接取到锁", got_a, 1)
            _check("5 · 第二个连接取不到（单例）", got_b, 0)
            await ca.execute(text(f"SELECT RELEASE_LOCK('{LOCK}')"))
            got_b2 = (await cb.execute(text(f"SELECT GET_LOCK('{LOCK}', 0)"))).scalar()
            _check("5 · 释放后第二个连接可取到", got_b2, 1)
            await cb.execute(text(f"SELECT RELEASE_LOCK('{LOCK}')"))
    finally:
        await a.dispose()
        await b.dispose()


async def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    engine = create_async_engine(Settings().sqlalchemy_url)   # 不打印（含口令）
    try:
        await scenario_1(engine)
        await scenario_2(engine)
        await scenario_3(engine)
        await scenario_4(engine)
        await scenario_5(engine)
    finally:
        await engine.dispose()
    print(f"\n===== {PASS_COUNT} passed / {FAIL_COUNT} failed =====")
    sys.exit(1 if FAIL_COUNT else 0)


if __name__ == "__main__":
    asyncio.run(main())
