"""批 C4b 真库验收：§8.4 熔断域隔离（agent_circuit 加 domain 列 + 复合主键）。

跑法（宿主直连共享库 33061，env 从容器整体导出后覆写，见 memory `offline-real-db-probe-recipe`）：
    .venv\\Scripts\\python.exe tests\\integration\\circuit_domain_probe.py
需先对该库执行 `alembic upgrade head`（本探针第 1、2 组断言就是迁移结果本身）。

**真**的部分：真库（真 DDL 后的表结构与真行）、真 `circuit_repo` 读写、真 `CircuitBreaker`
状态机、真 error run 全链（真 `_run_error` / 真限流桶 / 真收尾），error 域计数由它自己写出来。
**桩**的部分：`execute_case`（HTTP 层，固定返回可重试技术失败，用来喂熔断计数）、
`_launch_error_run`（建单后的自动触发，本探针改为手工触发以便先改 run_config）。

**本探针证不了什么**（显式声明，勿外推）：
- §8.4 的「**独立阈值**（默认同 5，实施可配）」**未实现**（本次只隔离状态）：两域共用 run
  配置的 `breaker_failure_threshold`，本探针用的是各自域的阈值，故阈值的域间独立性未被验证。
- held_out 域：与 manual 同用默认域，未单独造场景。
- 存量行的**归属正确性**：旧计数本就无法归属，迁移一律归 manual 并清零，探针只核「清零 + 落
  manual」，不核「哪一行本来属于谁」（后者无判据）。
"""
import asyncio
import logging
import sys
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core import circuit_repo
from app.core.circuit_breaker import CircuitBreaker
from app.core.config import Settings
from app.models import Agent, AgentInterface, EvalRun, TestCase, TestSuite
from app.models.misc import AgentCircuit
from app.runner import orchestrator as orch_mod
from app.runner.executor import ERROR_HTTP, CaseOutcome
from app.runner.orchestrator import create_error_regression_run

KEYWORD = "抱歉，我暂时无法回答"
TERMINAL = {"completed", "partial_failed", "timeout", "cancelled", "scoring_failed"}
STAMP = datetime.now().strftime("%H%M%S")
VERSION = f"2026.09.14-c4b-{STAMP}"
DOMAIN_MANUAL, DOMAIN_ERROR = "manual", "error_regression"

PASS_COUNT = 0
FAIL_COUNT = 0
H: dict = {"launched": [], "manual_baseline": None}


def _check(name: str, got, want) -> None:
    global PASS_COUNT, FAIL_COUNT
    if got == want:
        PASS_COUNT += 1
        print(f"  PASS  {name}")
    else:
        FAIL_COUNT += 1
        print(f"  FAIL  {name}: got={got!r} want={want!r}")


# ---------------- 桩 ----------------

async def _fake_execute(adapter, client, case, timeout_s):
    """HTTP 层桩：固定返回**可重试**技术失败（喂熔断计数；不 retry 因 max_retries 已置 0）。"""
    return CaseOutcome(unified={}, timing={}, status_code=504, error_type=ERROR_HTTP,
                       error_detail="probe: 固定技术失败")


def _fake_launch(run_id: int, agent_id: int, overflow: list) -> None:
    """建单后的自动触发改为手工：本探针要先改 run_config（阈值）再起跑。"""
    H["launched"].append(run_id)


orch_mod.execute_case = _fake_execute
orch_mod._launch_error_run = _fake_launch


# ---------------- 真库夹具 ----------------

async def _ensure_chain(engine) -> tuple[int, int]:
    """幂等建 agent + interface + error suite + 1 条 error case，返回 (agent_id, suite_id)。"""
    async with AsyncSession(engine) as s:
        agent = (await s.scalars(select(Agent).where(Agent.name == "probe-c4b-domain"))).first()
        if agent is None:
            agent = Agent(name="probe-c4b-domain", base_url="http://mock.local",
                          adapter_type="config", adapter_config={}, enabled=True,
                          contract_version="1.0")
            s.add(agent)
            await s.flush()
        iface = (await s.scalars(select(AgentInterface).where(
            AgentInterface.agent_id == agent.id,
            AgentInterface.name == "probe-c4b-iface"))).first()
        if iface is None:
            iface = AgentInterface(agent_id=agent.id, name="probe-c4b-iface",
                                   path="/v1/chat", method="POST", contract_type="sse",
                                   contract_version="1.0", enabled=True)
            s.add(iface)
            await s.flush()
        suite = (await s.scalars(select(TestSuite).where(
            TestSuite.agent_id == agent.id, TestSuite.is_error_suite.is_(True)))).first()
        if suite is None:
            suite = TestSuite(agent_id=agent.id, name="probe-c4b-error-suite", is_error_suite=True)
            s.add(suite)
            await s.flush()
        case = (await s.scalars(select(TestCase).where(
            TestCase.suite_id == suite.id, TestCase.payload_id == "probe-c4b-payload"))).first()
        if case is None:
            case = TestCase(
                suite_id=suite.id, interface_id=iface.id, name="backflow:probe-c4b-payload",
                input_type="text", input="导出按钮点了没反应",
                case_type="regression_error", payload_id="probe-c4b-payload",
                backflow_envelope={"source": {"cluster_id": 999202}},
                expected=None, metrics=None,
                assertions=[{"op": "keyword_not_contains",
                             "args": {"path": "answer", "keywords": [KEYWORD]}}],
                status="active", is_gold=False)
            s.add(case)
            await s.flush()
        case.status = "active"
        # ⚠️ id 必须在 commit 前取（AsyncSession 默认 expire_on_commit=True，commit 后读属性
        # 会 MissingGreenlet）
        ids = (agent.id, suite.id, case.id)
        await s.commit()
        return ids[0], ids[1]


async def _set_run_config(engine, run_id: int, **patch) -> None:
    """改 run_config：阈值=1 / 不重试 / 短超时（让熔断一次失败即开，探针跑得快）。"""
    async with AsyncSession(engine) as s:
        run = await s.get(EvalRun, run_id)
        cfg = dict(run.run_config or {})
        cfg.update(patch)
        run.run_config = cfg
        await s.commit()


async def _await_terminal(engine, run_id: int, tries: int = 160) -> str | None:
    st = None
    for _ in range(tries):
        await asyncio.sleep(0.25)
        async with AsyncSession(engine) as s:
            st = await s.scalar(select(EvalRun.status).where(EvalRun.id == run_id))
        if st in TERMINAL:
            return st
    raise TimeoutError(f"run {run_id} 未到终态（最后 {st}）")


async def _rows(engine, agent_id: int) -> dict[str, tuple]:
    """读该 agent 的两域状态行：{domain: (state, failures, opened_at)}。"""
    async with AsyncSession(engine) as s:
        rows = (await s.execute(select(AgentCircuit).where(
            AgentCircuit.agent_id == agent_id))).scalars().all()
        return {r.domain: (r.state, r.failures, r.opened_at) for r in rows}


# ---------------- 断言组 ----------------

async def _check_ddl(engine) -> None:
    """① 迁移结果本身：domain 列形态 + 复合主键。"""
    async with AsyncSession(engine) as s:
        col = (await s.execute(text(
            "SELECT COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'agent_circuit' "
            "AND COLUMN_NAME = 'domain'"))).first()
        _check("1 · domain 列存在且为 VARCHAR(32) NOT NULL",
               (col[0].lower(), col[1]) if col else None, ("varchar(32)", "NO"))
        pk = (await s.execute(text(
            "SELECT COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'agent_circuit' "
            "AND CONSTRAINT_NAME = 'PRIMARY' ORDER BY ORDINAL_POSITION"))).scalars().all()
        _check("1 · 主键 == (agent_id, domain)", list(pk), ["agent_id", "domain"])


async def _check_legacy_rows(engine) -> None:
    """② 迁移对**存量行**的处置：归 manual 且计数/状态清零。

    ⚠️ 只核 `probe-*` 之外的 agent：探针自己的 agent 行会在每次跑动中被本探针改写，
    混进来就成了「拿自己的新数据验迁移」。迁移当时（本批首次执行）核到 7 行全部合规，
    那 7 行即存量证据；重跑时本组仍应绿，但判据已收窄为「非探针 agent 无遗留脏行」。
    """
    async with AsyncSession(engine) as s:
        rows = (await s.execute(text(
            "SELECT c.domain, c.state, c.failures, c.opened_at, c.probe_inflight "
            "FROM agent_circuit c JOIN agent a ON a.id = c.agent_id "
            "WHERE a.name NOT LIKE 'probe-%'"))).all()
    print(f"  存量行数={len(rows)}（0 行时本组空过，见文件头「证不了什么」）")
    bad = [r for r in rows if r[0] != DOMAIN_MANUAL or r[1] != "closed" or r[2] != 0
           or r[3] is not None or r[4] != 0]
    _check("2 · 存量行全部落 manual 且计数/状态清零", len(bad), 0)
    if bad:
        print(f"    违规行: {bad[:5]}")


async def _repo_isolation(engine, agent_id: int) -> None:
    """③ 仓库层隔离：一域的连败只落在自己那一行。

    ⚠️ 计数口径：`CircuitBreaker._open()` 会把 failures 清零（open 态下 failures 恒 0，
    见 `circuit_breaker.py:97-100`），故 open 的判据是 **state + opened_at**，不是 failures。
    """
    async with AsyncSession(engine) as s:
        # 幂等重跑：两域先归零，否则上一轮留下的 open 态会让本轮读到的起点分叉
        for dom in (DOMAIN_MANUAL, DOMAIN_ERROR):
            await circuit_repo.save(s, agent_id, CircuitBreaker(5, 30), domain=dom)
        # 默认域：不传 domain 即 manual（既有调用点 = manual 链的兼容入口）
        bm = await circuit_repo.load(s, agent_id, CircuitBreaker(3, 30))
        for _ in range(3):
            bm.record_failure()
        await circuit_repo.save(s, agent_id, bm)
        # 反向：error 域在同一 agent 上以 5 为阈只失败 1 次 → 应停在 closed/failures=1
        be = await circuit_repo.load(s, agent_id, CircuitBreaker(5, 30), domain=DOMAIN_ERROR)
        be.record_failure()
        await circuit_repo.save(s, agent_id, be, domain=DOMAIN_ERROR)
    rows = await _rows(engine, agent_id)
    _check("3 · 不传 domain 的新行落在 manual 域", sorted(rows.keys()),
           sorted([DOMAIN_MANUAL, DOMAIN_ERROR]))
    _check("3 · manual 域连败 3 次即 open（opened_at 已置）",
           (rows.get(DOMAIN_MANUAL, (None, None, None))[0],
            rows.get(DOMAIN_MANUAL, (None, None, None))[2] is not None), ("open", True))
    _check("3 · error 域只累计自己的 1 次失败（未被 manual 的 3 次牵连）",
           rows.get(DOMAIN_ERROR), ("closed", 1, None))


async def _e2e_error_domain(engine, agent_id: int, suite_id: int) -> None:
    """④ 端到端接线：真 error run 的失败写进 error 域行，manual 行纹丝不动。

    ⚠️ 起跑前把 error 域**显式重置为 closed/0**：否则它带着上一组留下的 open 态，
    `try_acquire` 会把 case 直接拦成 `circuit_open`，run 压根不碰熔断计数——
    那时「行还是 open」纯属上一组的残留，绿得毫无判别力。
    """
    async with AsyncSession(engine) as s:
        await circuit_repo.save(s, agent_id, CircuitBreaker(1, 30), domain=DOMAIN_ERROR)
    before = await _rows(engine, agent_id)

    rid = await create_error_regression_run(agent_id=agent_id, suite_id=suite_id,
                                            version=VERSION, signal_run_id=0)
    assert rid, "error run 未建出"
    _check("4 · 自动触发被桩拦截（本探针手工起跑）", H["launched"], [rid])
    # 阈值=1：本 run 只有 1 条 case，一次失败即可把该域推成 open
    await _set_run_config(engine, rid, breaker_failure_threshold=1, max_retries=0,
                          case_timeout=30)
    await orch_mod.orchestrator.start_run(rid)
    st = await _await_terminal(engine, rid)
    print(f"  error run={rid} 终态={st}（全 na：固定技术失败 + 该域熔断开）")
    _check("4 · error run 达终态", st, "partial_failed")

    after = await _rows(engine, agent_id)
    _check("4 · error 域被真 run 从 closed/0 推到 open（非残留）",
           (before.get(DOMAIN_ERROR), after.get(DOMAIN_ERROR, (None, None, None))[0],
            after.get(DOMAIN_ERROR, (None, None, None))[2] is not None),
           (("closed", 0, None), "open", True))
    _check("4 · manual 域行与 run 前逐字段一致（未被牵连）",
           after.get(DOMAIN_MANUAL), before.get(DOMAIN_MANUAL))
    print(f"    manual 行前后相同 = {after.get(DOMAIN_MANUAL)}")


async def main() -> None:
    engine = create_async_engine(Settings().sqlalchemy_url)   # 不打印（含口令）
    try:
        await _check_ddl(engine)
        await _check_legacy_rows(engine)
        agent_id, suite_id = await _ensure_chain(engine)
        print(f"[setup] agent={agent_id} error_suite={suite_id}")
        await _repo_isolation(engine, agent_id)
        await _e2e_error_domain(engine, agent_id, suite_id)

        print(f"\nPROBE_RESULT pass={PASS_COUNT} fail={FAIL_COUNT}")
        if FAIL_COUNT:
            sys.exit(1)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    print(f"[probe] start {datetime.now().isoformat(timespec='seconds')}")
    asyncio.run(main())
