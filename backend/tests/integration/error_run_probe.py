"""批 C1 真库验收：error_regression 判定主干四场景（§8.1 / §8.2 / §8.6 / §7.4）。

跑法（宿主，需 socat 转发 3307 → ai-eval-mysql:3306）：
    set DB_HOST=127.0.0.1 & set DB_PORT=3307 & set DB_PASSWORD=<...>
    .venv\\Scripts\\python.exe tests\\integration\\error_run_probe.py

**真**的部分：真库、真 `_load_run_cases`（§8.1 谓词与形态过滤）、真 `run_assertions`
（判定）、真 `_save_result` 落库、真 `_finish_error_regression` 收尾、真
`create_error_regression_run` 建单（§7.4）。
**桩**的部分：`execute_case`（HTTP 执行层）——见「本探针证不了什么」。

四场景共用 **1 个 case + 3 次 run**（case 集不变，只有被测回答变），故无需多 agent：
  1. 非空答、不含关键词 → pass + completed
  2. 非空答、**含**关键词  → fail + completed
  3. 技术失败             → na + partial_failed + error_case==0
  4. case 置 invalidated  → 实跑集空 → cancelled

⚠️ 场景 1/2 **必须用非空答**：`keyword_not_contains` 对空答恒 `hits=[]` ⇒ 判 PASS
（`assertions/ops/text.py:83-89` 已知缺陷，R-12 未修）。用空答构造会把 2 的预期从
fail 变成 pass，探针会「绿着错」。

**本探针证不了什么**（显式声明，勿外推）：
- 真 HTTP 打被测 agent（执行/传输层沿用既有验证面，本批未动）
- 并发槽池共享（§8.3 归 C4）与熔断独立 key（§8.4 归 C4，需 DDL）
- 出站推送（§10.1）——本探针**不连 online**，由**进程内替换 `fire_push`**保证（见文件尾）。
  原表述「归 C2」写于 C2 落地前，已成**假自陈**：`_finish_error_regression` 现在真调
  `fire_push`，不替换就会把 cluster=999001 的哨兵载荷推到 online。
"""
import asyncio
import sys
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import Settings
from app.models import Agent, AgentInterface, EvalResult, EvalRun, TestCase, TestSuite
from app.runner import orchestrator as orch_mod
from app.runner.executor import CaseOutcome
from app.runner.orchestrator import create_error_regression_run

KEYWORD = "抱歉，我暂时无法回答"
GOOD_ANSWER = "您好，这个问题这样处理：先点右上角设置，再选择导出。"  # 非空且不含关键词
BAD_ANSWER = f"{KEYWORD}，请稍后再试。"[:80]                        # 非空且含关键词
TERMINAL = {"completed", "partial_failed", "timeout", "cancelled", "scoring_failed"}

# 构造护栏（把「空答假绿」从「我记得」变成结构约束）：空答恒 hits=[] ⇒ 场景 1/2 会双双判
# pass，探针仍全绿却什么都没证。此处钉死四个前提，任一破则探针自己先炸。
assert GOOD_ANSWER and BAD_ANSWER, "答必须非空（空答 ⇒ keyword_not_contains 恒 PASS）"
assert KEYWORD in BAD_ANSWER, "场景 2 必须真含关键词"
assert KEYWORD not in GOOD_ANSWER, "场景 1 必须真不含关键词"

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


def _ok(answer: str) -> CaseOutcome:
    return CaseOutcome(unified={"answer": answer}, timing={}, status_code=200)


def _fail() -> CaseOutcome:
    return CaseOutcome(error_type="timeout", error_detail="probe inject", timing={},
                       status_code=504)


async def _ensure_chain(engine) -> tuple[int, int, int]:
    """幂等建 agent + interface + error suite + 1 条 error case，返回 (agent_id, suite_id, case_id)。"""
    async with AsyncSession(engine) as s:
        agent = (await s.scalars(select(Agent).where(
            Agent.name == "probe-c1-error"))).first()
        if agent is None:
            agent = Agent(name="probe-c1-error", base_url="http://mock.local",
                          adapter_type="config", adapter_config={}, enabled=True,
                          contract_version="1.0")
            s.add(agent)
            await s.flush()
        iface = (await s.scalars(select(AgentInterface).where(
            AgentInterface.agent_id == agent.id,
            AgentInterface.name == "probe-c1-iface"))).first()
        if iface is None:
            iface = AgentInterface(agent_id=agent.id, name="probe-c1-iface",
                                   path="/v1/chat", method="POST", contract_type="sse",
                                   contract_version="1.0", enabled=True)
            s.add(iface)
            await s.flush()
        suite = (await s.scalars(select(TestSuite).where(
            TestSuite.agent_id == agent.id,
            TestSuite.is_error_suite.is_(True)))).first()
        if suite is None:
            suite = TestSuite(agent_id=agent.id, name="probe-c1-error-suite",
                              is_error_suite=True)
            s.add(suite)
            await s.flush()
        case = (await s.scalars(select(TestCase).where(
            TestCase.suite_id == suite.id,
            TestCase.payload_id == "probe-c1-payload"))).first()
        if case is None:
            case = TestCase(
                suite_id=suite.id, interface_id=iface.id, name="backflow:probe-c1-payload",
                input_type="text", input="导入按钮点了没反应",
                case_type="regression_error", payload_id="probe-c1-payload",
                backflow_envelope={"source": {"cluster_id": 999001}},
                expected=None, metrics=None,
                assertions=[{"op": "keyword_not_contains",
                             "args": {"path": "answer", "keywords": [KEYWORD]}}],
                status="active", is_gold=False,
            )
            s.add(case)
            await s.flush()
        ids = (agent.id, suite.id, case.id)
        case.status = "active"  # 复位（上一轮场景 4 会置 invalidated）
        await s.commit()
        return ids


async def _run_once(engine, agent_id: int, suite_id: int) -> int:
    """建单并等它跑到终态，返回 run_id。"""
    run_id = await create_error_regression_run(
        agent_id=agent_id, suite_id=suite_id, version="2026.09.14-r1",
        signal_run_id=424242)
    for _ in range(60):
        await asyncio.sleep(0.5)
        async with AsyncSession(engine) as s:
            st = await s.scalar(select(EvalRun.status).where(EvalRun.id == run_id))
        if st in TERMINAL:
            return run_id
    raise TimeoutError(f"run {run_id} 60×0.5s 内未到终态（最后 status={st}）")


async def _read(engine, run_id: int) -> tuple[EvalRun, list[EvalResult]]:
    async with AsyncSession(engine) as s:
        run = await s.get(EvalRun, run_id)
        rs = (await s.scalars(select(EvalResult).where(EvalResult.run_id == run_id))).all()
        return run, list(rs)


async def _scenario(engine, agent_id, suite_id, case_id, name, behavior, *,
                    expect_pf, expect_status, expect_error_case, expect_leak=None,
                    expect_error_type_set=False, expect_ar=None):
    print(f"[场景] {name}")
    holder["fn"] = behavior
    run_id = await _run_once(engine, agent_id, suite_id)
    run, rs = await _read(engine, run_id)
    print(f"  run={run_id} status={run.status} pass={run.pass_case} "
          f"fail={run.fail_case} na={run.na_case} error={run.error_case} 行数={len(rs)}")
    _check(f"{name} · pass_fail", [r.pass_fail for r in rs], expect_pf)
    _check(f"{name} · run.status", run.status, expect_status)
    _check(f"{name} · error_case", run.error_case, expect_error_case)
    _check(f"{name} · total_case", run.total_case, len(expect_pf))
    if expect_leak is not None:
        # §8.6：error run 不评分 → agent_score 恒 NULL
        _check(f"{name} · agent_score", run.agent_score, expect_leak)
    if expect_error_type_set:
        # §8.2：na 行**必须带 error_type**（诊断信息不能丢）——只判 na 会漏掉
        # 「na 但原因空白」这种半截落库。
        _check(f"{name} · na 行 error_type 非空",
               [bool(r.error_type) for r in rs], [True] * len(expect_pf))
    if expect_ar is not None:
        # C-4：error 路径的**逐条断言明细**必须落库——原实现只落终值字符串 ⇒ 该列恒 NULL，
        # fail 的成因在结果行上与「空答 fail」同形、不可读。判形态不判「非 None」：
        # NULL 与空数组在 `or []` 下同形，只有逐条 pass 位能把两者分开。
        _check(f"{name} · assertion_results 明细",
               [None if r.assertion_results is None
                else [x.get("pass") for x in r.assertion_results] for r in rs],
               expect_ar)
    # 建单参数（§7.4）只查一次
    return run_id


async def main() -> None:
    global holder
    app_db = Settings()
    engine = create_async_engine(app_db.sqlalchemy_url)  # 不打印（含口令）
    try:
        agent_id, suite_id, case_id = await _ensure_chain(engine)
        print(f"[setup] agent={agent_id} suite={suite_id} case={case_id}")

        # --- 建单参数（§7.4）先查一次 ---
        holder["fn"] = lambda: _ok(GOOD_ANSWER)
        probe_run = await _run_once(engine, agent_id, suite_id)
        run0, _ = await _read(engine, probe_run)
        print("[§7.4 建单参数]")
        _check("pinned=True", run0.pinned, True)
        _check("trigger_type", run0.trigger_type, "error_regression")
        _check("trigger_signal_id=信号 run id", run0.trigger_signal_id, 424242)
        _check("case_ids 圈定复现集", run0.case_ids, [case_id])
        _check("case_timeout 专用值", (run0.run_config or {}).get("case_timeout"), 600)
        _check("max_retries 专用值", (run0.run_config or {}).get("max_retries"), 2)
        # 显式时长闸：不设它就会走估算器、被 600×2 抬爆封顶（实测 13740s）⇒ 逐 run WARNING，
        # 且「cap 限时长」的错觉留在库里。7200 = §7.4 自己的预算示例 H_run=2h。
        _check("run_timeout 显式", (run0.run_config or {}).get("run_timeout"), 7200)

        # --- 四场景 ---
        await _scenario(engine, agent_id, suite_id, case_id, "1 非空答不含关键词",
                        lambda: _ok(GOOD_ANSWER), expect_pf=["pass"],
                        expect_status="completed", expect_error_case=0, expect_leak=None,
                        expect_ar=[[True]])
        await _scenario(engine, agent_id, suite_id, case_id, "2 非空答含关键词",
                        lambda: _ok(BAD_ANSWER), expect_pf=["fail"],
                        expect_status="completed", expect_error_case=0,
                        expect_ar=[[False]])       # C-4 核心：fail 的成因落进结果行
        await _scenario(engine, agent_id, suite_id, case_id, "3 技术失败",
                        lambda: _fail(), expect_pf=["na"],
                        expect_status="partial_failed", expect_error_case=0,
                        expect_error_type_set=True,
                        expect_ar=[None])          # 技术失败没跑断言 ⇒ 无明细可落

        # --- 场景 4：实跑集为空 → cancelled ---
        async with AsyncSession(engine) as s:
            c = await s.get(TestCase, case_id)
            c.status = "invalidated"
            await s.commit()
        print("[场景] 4 实跑集为空（case 置 invalidated）")
        holder["fn"] = lambda: _ok(GOOD_ANSWER)
        run_id = await _run_once(engine, agent_id, suite_id)
        run, rs = await _read(engine, run_id)
        print(f"  run={run_id} status={run.status} 行数={len(rs)}")
        _check("4 · run.status", run.status, "cancelled")
        _check("4 · 无结果行", len(rs), 0)

        # 复位，莫留 invalidated 状态给后续批次
        async with AsyncSession(engine) as s:
            c = await s.get(TestCase, case_id)
            c.status = "active"
            await s.commit()

        print(f"\nPROBE_RESULT pass={PASS_COUNT} fail={FAIL_COUNT}")
        if FAIL_COUNT:
            sys.exit(1)
    finally:
        await engine.dispose()


holder: dict = {}


async def _fake_execute(adapter, client, case, timeout_s):
    """HTTP 执行层桩：行为由 holder['fn'] 决定（真 DB / 真判定 / 真收尾）。"""
    return holder["fn"]()


def _noop_fire_push(run_id: int) -> None:
    """替换出站推送。本探针的验证面在落库与判定，不在 §10.1 出站；且它会真推一笔
    哨兵簇（cluster=999001）到 online。**必须替换**——`_finish_error_regression` 现真调
    `fire_push`（原 docstring「归 C2、不连 online」已成假自陈）。"""
    return None


orch_mod.execute_case = _fake_execute  # 探针进程内替换，仅本脚本生效
orch_mod.fire_push = _noop_fire_push   # 同上：不连 online

if __name__ == "__main__":
    print(f"[probe] start {datetime.now().isoformat(timespec='seconds')}")
    asyncio.run(main())
