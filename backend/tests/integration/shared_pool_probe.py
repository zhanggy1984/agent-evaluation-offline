"""批 C4a 真库验收：§8.3 进程级共享 per-agent 槽池（manual 与 error run 不叠加超限）。

跑法（宿主直连共享库 33061，env 从容器整体导出后覆写，见 memory `offline-real-db-probe-recipe`）：
    .venv\\Scripts\\python.exe tests\\integration\\shared_pool_probe.py

**真**的部分：真库、真 `RunOrchestrator.start_run` 全链（真限流桶、真收尾）、**真 KeyedLimiter
实例**（计数点挂在它的 acquire/release 上，量的是「槽真的被占了几路」，不是「HTTP 睡了几路」）、
真 manual run 与真 error run 同 agent 并发。
**桩**的部分：`execute_case`（HTTP 层，只负责睡够时间让槽位被占住）、`_probe_before_run`（面向
真实 agent 契约，不桩则 manual run 在探测处早退）。

两个阶段（这就是本探针的**判别力证据**，缺了它等于没测）：
- 阶段 1（现码）：manual 占满 3 路 → error 的 case 排队 → 槽峰值 == 3。
- 阶段 2（**对照**）：把 limiter 的 acquire/release 换成改造前的实现（无共享层，逐字复刻
  `_bucket(run_id).acquire(agent_id)`）→ 同样的夹具槽峰值 == **4**。
  若阶段 2 也是 3，说明夹具压根没制造出跨桶叠加，阶段 1 的绿是假绿。

**本探针证不了什么**（显式声明，勿外推）：
- **多进程 worker**：共享池是进程内对象，workers>1 时失效（§8.3 自注，需 agent 行执行标记）。
  本探针只覆盖单进程。
- 运行期改 `per_agent_concurrency` 生效（§8.3 第 4 条：首次登记生效，改配置需重启）。
- 排队上界：唯一兜底是 run 级时长闸（`run_timeout`），本探针不构造长时间排队。
"""
import asyncio
import logging
import sys
import time
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import Settings
from app.models import Agent, AgentInterface, EvalRun, TestCase, TestSuite
from app.runner import orchestrator as orch_mod
from app.runner.executor import CaseOutcome
from app.runner.orchestrator import create_error_regression_run

KEYWORD = "抱歉，我暂时无法回答"
GOOD_ANSWER = "您好，这个问题这样处理：先点右上角设置，再选择导出。"
TERMINAL = {"completed", "partial_failed", "timeout", "cancelled", "scoring_failed"}
HOLD_MANUAL_S = 0.4      # manual case 占槽时长：够长才能观察到「error 在排队」
HOLD_ERROR_S = 0.2

# 版本号带时分秒：探针必须可重复跑，固定版本号会读到上一轮残留行（同 C3 探针的坑）
STAMP = datetime.now().strftime("%H%M%S")
V1, V2 = f"2026.09.14-c4a-p1-{STAMP}", f"2026.09.14-c4a-p2-{STAMP}"

PASS_COUNT = 0
FAIL_COUNT = 0
H: dict = {"slots": 0, "slot_peak": 0, "error_started": False, "error_case_ids": set()}


def _check(name: str, got, want) -> None:
    global PASS_COUNT, FAIL_COUNT
    if got == want:
        PASS_COUNT += 1
        print(f"  PASS  {name}")
    else:
        FAIL_COUNT += 1
        print(f"  FAIL  {name}: got={got!r} want={want!r}")


def _reset_counters() -> None:
    H.update({"slots": 0, "slot_peak": 0, "error_started": False})


# ---------------- 桩 ----------------

async def _fake_execute(adapter, client, case, timeout_s):
    """HTTP 执行层桩：占住槽位睡够时间；error case 额外记录「它真的起跑了」。"""
    err = case.id in H["error_case_ids"]
    if err:
        H["error_started"] = True
    await asyncio.sleep(HOLD_ERROR_S if err else HOLD_MANUAL_S)
    return CaseOutcome(unified={"answer": GOOD_ANSWER}, timing={}, status_code=200)


async def _fake_probe(*_a, **_kw) -> bool:
    return True


orch_mod.execute_case = _fake_execute               # 模块全局替换，仅本脚本进程生效
orch_mod.orchestrator._probe_before_run = _fake_probe


# ---------------- 限流槽计数（挂在真 limiter 实例上） ----------------

def _install_slot_counter():
    """包住真 limiter 的 acquire/release，量**槽**的在途峰值。

    `_impl` 间接层是为阶段 2 留的：对照阶段只换 `_impl`，计数逻辑一字不改。
    """
    lim = orch_mod.orchestrator._limiter
    # 先取真实现（此刻 lim 上还只有 KeyedLimiter 自己的方法），再换计数壳——顺序反了会自递归
    real = {"acquire": lim.acquire, "release": lim.release}

    async def counted_acquire(run_id, agent_id):
        await impl["acquire"](run_id, agent_id)
        H["slots"] += 1
        H["slot_peak"] = max(H["slot_peak"], H["slots"])

    def counted_release(run_id, agent_id):
        impl["release"](run_id, agent_id)
        H["slots"] -= 1

    lim.acquire = counted_acquire
    lim.release = counted_release

    async def old_acquire(run_id, agent_id):
        # 改造前的实现（逐字复刻）：只有桶内 per-agent + global，桶间 per-agent 不共享
        await lim._bucket(run_id).acquire(agent_id)

    def old_release(run_id, agent_id):
        lim._bucket(run_id).release(agent_id)

    impl = dict(real)          # 计数壳当前转发到的实现（阶段 1 = 真共享池）
    return impl, old_acquire, old_release, real


# ---------------- 真库夹具 ----------------

async def _ensure_chain(engine) -> tuple[int, int, int, int, list[int]]:
    """幂等建 agent + error suite/case + 普通 suite + **5 条**普通 case。

    返回 (agent_id, error_suite_id, error_case_id, 普通 suite id, [普通 case id ×5])。
    5 条普通 case 是制造争用的前提：per_agent=3 时必须有 >3 条才能观察到排队。
    """
    async with AsyncSession(engine) as s:
        agent = (await s.scalars(select(Agent).where(Agent.name == "probe-c4a-pool"))).first()
        if agent is None:
            agent = Agent(name="probe-c4a-pool", base_url="http://mock.local",
                          adapter_type="config", adapter_config={}, enabled=True,
                          contract_version="1.0")
            s.add(agent)
            await s.flush()
        iface = (await s.scalars(select(AgentInterface).where(
            AgentInterface.agent_id == agent.id,
            AgentInterface.name == "probe-c4a-iface"))).first()
        if iface is None:
            iface = AgentInterface(agent_id=agent.id, name="probe-c4a-iface",
                                   path="/v1/chat", method="POST", contract_type="sse",
                                   contract_version="1.0", enabled=True)
            s.add(iface)
            await s.flush()
        esuite = (await s.scalars(select(TestSuite).where(
            TestSuite.agent_id == agent.id, TestSuite.is_error_suite.is_(True)))).first()
        if esuite is None:
            esuite = TestSuite(agent_id=agent.id, name="probe-c4a-error-suite", is_error_suite=True)
            s.add(esuite)
            await s.flush()
        ecase = (await s.scalars(select(TestCase).where(
            TestCase.suite_id == esuite.id, TestCase.payload_id == "probe-c4a-payload"))).first()
        if ecase is None:
            ecase = TestCase(
                suite_id=esuite.id, interface_id=iface.id, name="backflow:probe-c4a-payload",
                input_type="text", input="导出按钮点了没反应",
                case_type="regression_error", payload_id="probe-c4a-payload",
                backflow_envelope={"source": {"cluster_id": 999201}},
                expected=None, metrics=None,
                assertions=[{"op": "keyword_not_contains",
                             "args": {"path": "answer", "keywords": [KEYWORD]}}],
                status="active", is_gold=False)
            s.add(ecase)
            await s.flush()
        ecase.status = "active"
        ecase.assertions = [{"op": "keyword_not_contains",
                             "args": {"path": "answer", "keywords": [KEYWORD]}}]
        nsuite = (await s.scalars(select(TestSuite).where(
            TestSuite.agent_id == agent.id, TestSuite.is_error_suite.is_(False)))).first()
        if nsuite is None:
            nsuite = TestSuite(agent_id=agent.id, name="probe-c4a-normal-suite",
                               is_error_suite=False)
            s.add(nsuite)
            await s.flush()
        ncases = []
        for i in range(5):
            name = f"probe-c4a-normal-case-{i}"
            c = (await s.scalars(select(TestCase).where(
                TestCase.suite_id == nsuite.id, TestCase.name == name))).first()
            if c is None:
                c = TestCase(suite_id=nsuite.id, interface_id=iface.id, name=name,
                             input_type="text", input="怎么导出数据", expected=None,
                             metrics=None, assertions=None, status="active", is_gold=False)
                s.add(c)
                await s.flush()
            ncases.append(c.id)
        # ⚠️ id 必须在 commit 前取（本探针的 AsyncSession 未关 expire_on_commit，
        # commit 后读属性会触发惰性重载 → 在 async 上下文外 MissingGreenlet）
        ids = (agent.id, esuite.id, ecase.id, nsuite.id, ncases)
        await s.commit()
        return ids


async def _create_manual_run(engine, agent_id: int, suite_id: int, case_ids: list[int],
                             version: str) -> int:
    """直插 manual run：run_config 钉死 per_agent_concurrency=3（共享池容量来源）。"""
    async with AsyncSession(engine) as s:
        run = EvalRun(agent_id=agent_id, suite_id=suite_id, version=version,
                      trigger_type="manual", status="pending", generation=1,
                      run_config={"global_max_inflight": 16, "per_agent_concurrency": 3,
                                  "case_timeout": 30, "perf_repeat_count": 1, "max_retries": 0,
                                  "heartbeat_interval": 5, "lease_seconds": 90,
                                  "run_timeout": 300},
                      case_ids=case_ids)
        s.add(run)
        await s.flush()
        rid = run.id
        await s.commit()
        return rid


async def _await_terminal(engine, run_id: int, label: str, tries: int = 120) -> str | None:
    st = None
    for _ in range(tries):
        await asyncio.sleep(0.25)
        async with AsyncSession(engine) as s:
            st = await s.scalar(select(EvalRun.status).where(EvalRun.id == run_id))
        if st in TERMINAL:
            return st
    raise TimeoutError(f"{label} run {run_id} 未到终态（最后 {st}）")


async def _wait_slots(want: int, tries: int = 200) -> None:
    for _ in range(tries):
        if H["slots"] >= want:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"等待在途槽 {want} 超时（当前 {H['slots']}）")


async def _run_scenario(engine, agent_id: int, esuite: int, nsuite: int, ncase_ids: list[int],
                        version: str, label: str) -> dict:
    """跑一轮「manual 占满 → error 入场」，返回断言用的小结。

    先起 manual 并**等它真占满 3 路**再建 error run：否则 error 可能先抢到槽，
    峰值仍是 3 但「排队」根本没发生，断言会绿得毫无意义（争用必须真的发生）。
    """
    _reset_counters()
    manual_id = await _create_manual_run(engine, agent_id, nsuite, ncase_ids, version)
    print(f"[{label}] manual run={manual_id} 5 case / 槽上限 3")
    asyncio.create_task(orch_mod.orchestrator.start_run(manual_id))
    await _wait_slots(3)
    print("  manual 已占满 3 槽，此刻建 error run")
    err_id = await create_error_regression_run(agent_id=agent_id, suite_id=esuite,
                                               version=version, signal_run_id=manual_id)
    assert err_id, "error run 未建出（活跃闸/门禁拦截？）"
    await asyncio.sleep(HOLD_MANUAL_S * 0.3)      # 排队窗口内抽查
    queued_ok = (not H["error_started"]) and H["slots"] == 3
    print(f"  排队窗口内：error 起跑={H['error_started']} 在途槽={H['slots']}")
    st_m = await _await_terminal(engine, manual_id, "manual")
    st_e = await _await_terminal(engine, err_id, "error")
    return {"manual_id": manual_id, "err_id": err_id, "manual_status": st_m,
            "error_status": st_e, "peak": H["slot_peak"], "queued_ok": queued_ok}


async def main() -> None:
    engine = create_async_engine(Settings().sqlalchemy_url)   # 不打印（含口令）
    try:
        agent_id, esuite, ecase, nsuite, ncases = await _ensure_chain(engine)
        H["error_case_ids"] = {ecase}
        impl, old_acquire, old_release, real = _install_slot_counter()
        print(f"[setup] agent={agent_id} error_suite={esuite} error_case={ecase} "
              f"普通 suite={nsuite} 普通 case={ncases}")

        # ---- 阶段 1：现码（共享池）----
        r1 = await _run_scenario(engine, agent_id, esuite, nsuite, ncases, V1, "阶段 1 共享池")
        print(f"  终态 manual={r1['manual_status']} error={r1['error_status']}")
        _check("1 · manual run 达终态", r1["manual_status"], "completed")
        _check("1 · error run 达终态", r1["error_status"], "completed")
        _check("1 · 同 agent 槽峰值 == 3（不叠加超限）", r1["peak"], 3)
        _check("1 · error 在 manual 占满期间确实排队（未起跑）", r1["queued_ok"], True)

        # ---- 阶段 2：对照（改造前的无共享层语义）----
        impl["acquire"], impl["release"] = old_acquire, old_release
        try:
            r2 = await _run_scenario(engine, agent_id, esuite, nsuite, ncases, V2,
                                     "阶段 2 对照（旧语义）")
        finally:
            impl.update(real)      # 还原计数壳的转发目标（阶段 1 的实现）
        print(f"  对照终态 manual={r2['manual_status']} error={r2['error_status']}")
        _check("2 · 对照下 manual 仍达终态", r2["manual_status"], "completed")
        _check("2 · 对照下槽峰值 == 4（旧语义叠加出超限）", r2["peak"], 4)
        _check("2 · 对照下 error 没有排队（旧语义直接进）", r2["queued_ok"], False)

        print(f"\nPROBE_RESULT pass={PASS_COUNT} fail={FAIL_COUNT}")
        if FAIL_COUNT:
            sys.exit(1)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    print(f"[probe] start {datetime.now().isoformat(timespec='seconds')}")
    asyncio.run(main())
