"""批 C7 真库验收：§7.5 项 2/3/4 —— error run 与 manual 通道隔离。

跑法（宿主直连共享库，env 从容器整体导出后覆写，见 memory `offline-real-db-probe-recipe`）：
    .venv\\Scripts\\python.exe tests\\integration\\run_guard_probe.py

**真的部分**：真库、真 `create_run`/`rerun_run` 处理函数全链（真 `agent_mutex` 行锁、真
`_max_active_runs` 读 system_config、真 SQL 计数语义、真写 run 行、真审计）。
**桩的部分**：`orchestrator.start_run`（不真跑执行）与 `write_audit`（不落审计行）——
本批验的是「建不建 / 拒不拒」，不是「跑得对不对」。

**这份探针是项 2 的唯一判据**：`tests/test_runs_error_guard.py` 的替身只按 SQL 文本模拟，
证明不了真 MySQL 的计数语义。每个场景用**独立 agent**（否则上一条 pending run 占槽会串场）。

**本探针证不了什么**（显式声明，勿外推）：
- **执行期并发安全**：建单闸放开后靠共享 per-agent 执行槽池兜底（phase2 §6.2 v0.4 / 批 C4a），
  **本批不复验**，本探针不覆盖；
- **前端表现**：400 走前端提示路径，未做浏览器 e2e；
- **存量数据是否已被项 3 影响**：只统计、不订正（见场景 9）。
"""
import asyncio
import logging
import sys
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import func, select

from app.api import runs as runs_api
from app.api.runs import RunCreate
from app.core.db import SessionLocal
from app.core.errors import ApiError
from app.models import Agent, AgentInterface, EvalRun, TestCase, TestSuite

STAMP = datetime.now().strftime("%H%M%S")

PASS_COUNT = 0
FAIL_COUNT = 0

USER = SimpleNamespace(id=1, role="admin", username="probe-c7")
REQ = SimpleNamespace()


def _check(name: str, got, want) -> None:
    global PASS_COUNT, FAIL_COUNT
    if got == want:
        PASS_COUNT += 1
        print(f"  PASS  {name}")
    else:
        FAIL_COUNT += 1
        print(f"  FAIL  {name}: got={got!r} want={want!r}")


_SEQ = [1000]


def _v(tag: str) -> str:
    """批 C7 当时的约束：必须合法 semver，否则 create_run 在 `_SEMVER` 校验处返 400 ⇒ 探针
    **根本走不到互斥计数**（那是断言不可达，不是实现红）。**批 C8 已把 `_SEMVER` 放宽为白名单
    字符集**，此约束消失；这里仍返回 semver 只是沿用既有夹具，不为本批引入新变量。"""
    _SEQ[0] += 1
    return f"1.0.{_SEQ[0]}"


async def _stub(*_a, **_kw):
    return None


# 探针内替换执行与审计（模块级）；被 create_run/rerun_run 直接引用
runs_api.orchestrator.start_run = _stub
runs_api.write_audit = _stub


async def _ensure_agent(engine, name: str) -> tuple[int, int, int]:
    """幂等建 agent + 普通 suite + 1 条 active 普通 case（case_type=NULL，过 V1 空转校验）。"""
    async with SessionLocal() as s:
        agent = (await s.scalars(select(Agent).where(Agent.name == name))).first()
        if agent is None:
            agent = Agent(name=name, base_url="http://mock.local", adapter_type="config",
                          adapter_config={}, enabled=True, contract_version="1.0")
            s.add(agent)
            await s.flush()
        iface = (await s.scalars(select(AgentInterface).where(
                AgentInterface.agent_id == agent.id,
                AgentInterface.name == "probe-c7-iface"))).first()
        if iface is None:
            iface = AgentInterface(agent_id=agent.id, name="probe-c7-iface", path="/v1/chat",
                                   method="POST", contract_type="sse", contract_version="1.0",
                                   enabled=True)
            s.add(iface)
            await s.flush()
        suite = (await s.scalars(select(TestSuite).where(
            TestSuite.agent_id == agent.id, TestSuite.name == f"{name}-suite"))).first()
        if suite is None:
            suite = TestSuite(agent_id=agent.id, name=f"{name}-suite", is_error_suite=False)
            s.add(suite)
            await s.flush()
        case = (await s.scalars(select(TestCase).where(
            TestCase.suite_id == suite.id, TestCase.name == f"{name}-case"))).first()
        if case is None:
            case = TestCase(suite_id=suite.id, interface_id=iface.id, name=f"{name}-case",
                            input_type="text", input="你好", expected={"answer": "你好"},
                            metrics=["accuracy"], case_type=None, is_gold=False, status="active")
            s.add(case)
            await s.flush()
        ids = (agent.id, suite.id, case.id)
        await s.commit()
        return ids


async def _ensure_error_suite(engine, agent_id: int, name: str) -> int:
    """只有 suite、**不放 error case**：本批只需「suite 属 error suite」这一事实，
    建 case 会让常驻 reconcile_loop 把它当扫描对象（无谓耦合）。"""
    async with SessionLocal() as s:
        suite = (await s.scalars(select(TestSuite).where(
            TestSuite.agent_id == agent_id, TestSuite.name == name))).first()
        if suite is None:
            suite = TestSuite(agent_id=agent_id, name=name, is_error_suite=True)
            s.add(suite)
            await s.flush()
        sid = suite.id
        await s.commit()
        return sid


async def _add_run(engine, agent_id: int, suite_id: int, version: str, trigger: str,
                   status: str) -> int:
    async with SessionLocal() as s:
        run = EvalRun(agent_id=agent_id, suite_id=suite_id, version=version,
                      trigger_type=trigger, status=status, generation=1, run_config={},
                      case_ids=[])
        s.add(run)
        await s.flush()
        rid = run.id
        await s.commit()
        return rid


async def _create(engine, agent_id: int, suite_id: int, version: str):
    """真调 create_run，返回 ('ok', run_id) 或 ('err', status_code, message)。"""
    body = RunCreate(agent_id=agent_id, suite_id=suite_id, version=version,
                     trigger_type="manual", case_ids=None)
    async with SessionLocal() as s:
        try:
            await runs_api.create_run(body, USER, s)
        except ApiError as e:
            return ("err", e.status_code, e.message)
    async with SessionLocal() as s:
        rid = (await s.scalars(select(EvalRun.id).where(
            EvalRun.agent_id == agent_id, EvalRun.version == version))).first()
    return ("ok", rid)


async def _rerun(engine, run_id: int):
    async with SessionLocal() as s:
        try:
            await runs_api.rerun_run(run_id, REQ, USER, s)
        except ApiError as e:
            return ("err", e.status_code, e.message)
    return ("ok", None)


async def _run_count(engine, agent_id: int) -> int:
    async with SessionLocal() as s:
        return (await s.execute(select(func.count()).select_from(EvalRun).where(
            EvalRun.agent_id == agent_id))).scalar() or 0


async def scenario_count(engine) -> None:
    """项 2：error run 占槽不挡 manual；对照 = manual run 占槽仍 409。"""
    print("\n[场景 1-4] 项 2：活跃计数排除 error run（含对照）")
    a, suite, _ = await _ensure_agent(engine, f"probe-c7-count-{STAMP}")

    # 对照：manual run 占槽 ⇒ 必 409（若这条不红，说明下面的「通过」是没测到计数路径）
    await _add_run(engine, a, suite, _v("ctrl"), "manual", "pending")
    got = await _create(engine, a, suite, _v("ctrl2"))
    _check("1 · 对照：manual run 占槽 → 409", got[0] == "err" and got[1], 409)

    # 场景 2/3/4：各用独立 agent，避免互相占槽
    for tag, status, label in (("pend", "pending", "pending"),
                               ("run", "running", "running"),
                               ("term", "timeout", "终态 timeout")):
        b, s2, _ = await _ensure_agent(engine, f"probe-c7-{tag}-{STAMP}")
        await _add_run(engine, b, s2, _v(tag), "error_regression", status)
        res = await _create(engine, b, s2, _v(tag + "m"))
        _check(f"2 · error run({label}) 占槽 → manual 建单不被挡", res[0], "ok")
        if res[0] == "ok":
            async with SessionLocal() as s:
                new = await s.get(EvalRun, res[1])
                _check(f"2 · 建出的 run 仍是 manual（{label}）", new.trigger_type if new else None,
                       "manual")


async def scenario_error_suite(engine) -> None:
    """项 3：error suite 拒建 manual run（400），且不产生空转 run。"""
    print("\n[场景 5] 项 3：error suite 拒建 manual run")
    a, _, _ = await _ensure_agent(engine, f"probe-c7-esuite-{STAMP}")
    esuite = await _ensure_error_suite(engine, a, f"probe-c7-errsuite-{STAMP}")
    before = await _run_count(engine, a)
    res = await _create(engine, a, esuite, _v("es"))
    _check("5 · error suite + manual 建单 → 400", res[0] == "err" and res[1], 400)
    _check("5 · 拒绝发生在建 run 之前（无空转 run）", await _run_count(engine, a), before)


async def scenario_rerun_guard(engine) -> None:
    """项 4 + 项 3（rerun 半边）+ 对照：正常 manual 终态 run 仍可 rerun。"""
    print("\n[场景 6-8] 项 3/4：rerun 通道守卫（含对照）")
    a, suite, _ = await _ensure_agent(engine, f"probe-c7-rerun-{STAMP}")

    # 项 4：rerun 一条 error run（模拟内部创建器直插的行）
    err_run = await _add_run(engine, a, suite, _v("e"), "error_regression", "completed")
    res = await _rerun(engine, err_run)
    _check("6 · rerun error run → 400", res[0] == "err" and res[1], 400)
    _check("6 · 未产生新的 error run", await _run_count(engine, a), 1)

    # 项 3（rerun 半边）：error suite 下的 manual run（项 3 落地前的存量形态）
    esuite = await _ensure_error_suite(engine, a, f"probe-c7-errsuite-r-{STAMP}")
    legacy = await _add_run(engine, a, esuite, _v("legacy"), "manual", "completed")
    res = await _rerun(engine, legacy)
    _check("7 · rerun error suite 下的 manual run → 400", res[0] == "err" and res[1], 400)

    # 对照：普通终态 manual run 仍可 rerun（证明不是「一律拒 rerun」）
    ok_run = await _add_run(engine, a, suite, _v("ok"), "manual", "completed")
    res = await _rerun(engine, ok_run)
    _check("8 · 对照：普通 manual 终态 run 可 rerun", res[0], "ok")


async def scenario_stocktake(engine) -> None:
    """场景 9（**只统计、不作判据**）：error suite 下的 manual/held_out run 存量，
    回答「项 3 的行为收紧会影响多少现存数据」。"""
    print("\n[场景 9] 存量统计（非判据，仅供决策）")
    base = (select(func.count()).select_from(EvalRun).join(
        TestSuite, TestSuite.id == EvalRun.suite_id).where(
        TestSuite.is_error_suite.is_(True),
        EvalRun.trigger_type.in_(("manual", "held_out"))))
    async with SessionLocal() as s:
        total = (await s.execute(base)).scalar() or 0
        # 排掉**所有** probe-* 探针 agent（不只本批）：C5 探针把 agent 的 suite 建成了 error
        # suite 又在其上直插信号 run，第一次跑本探针时报出的「历史存量 21 条」其实全是它。
        # 不减掉就会把探针残留误报成「项 3 会影响的生产数据」。
        probe_agents = (await s.scalars(select(Agent.id).where(
            Agent.name.like("probe-%")))).all()
        own = 0
        if probe_agents:
            own = (await s.execute(base.where(EvalRun.agent_id.in_(probe_agents)))).scalar() or 0
    print(f"  INFO  全库 error suite 下的 manual/held_out run 存量 = {total} 条"
          f"（其中本探针自造 {own} 条 ⇒ 历史存量 {total - own} 条）")
    async with SessionLocal() as s:
        rows = (await s.execute(select(Agent.name, EvalRun.trigger_type, EvalRun.status,
                                       func.count())
                                .join(Agent, Agent.id == EvalRun.agent_id)
                                .join(TestSuite, TestSuite.id == EvalRun.suite_id)
                                .where(TestSuite.is_error_suite.is_(True),
                                       EvalRun.trigger_type.in_(("manual", "held_out")))
                                .group_by(Agent.name, EvalRun.trigger_type, EvalRun.status)
                                .order_by(func.count().desc()).limit(10))).all()
    for name, trig, st, c in rows:
        print(f"  INFO    {name} | {trig} | {st} | {c} 条")


async def main() -> None:
    logging.basicConfig(level=logging.ERROR)   # 探针自身打 409/400 日志，压掉噪声
    # 会话走 app 自己的 SessionLocal（expire_on_commit=False）：自建 AsyncSession 默认
    # expire_on_commit=True，handler 内 commit 后取属性会触发惰性刷新 → MissingGreenlet。
    await scenario_count(None)
    await scenario_error_suite(None)
    await scenario_rerun_guard(None)
    await scenario_stocktake(None)
    print(f"\n===== {PASS_COUNT} passed / {FAIL_COUNT} failed =====")
    sys.exit(1 if FAIL_COUNT else 0)


if __name__ == "__main__":
    asyncio.run(main())
