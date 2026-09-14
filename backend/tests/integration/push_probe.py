"""批 C2 真机验收：offline 真 HTTP 推送 online（§10.1 载荷 / 分片 / 幂等）。

三段式跑法（**顺序不可换**，原因见下），全部在 `ai-eval-backend` 容器内：

    python tests/integration/push_probe.py prepare          # 建 agent/suite/3 case，**不建 run**
    # → 拿 case_ids 去 online 侧种 3 个 claim 簇（c2_push_seed.py seed）
    python tests/integration/push_probe.py go --clusters a,b,c --version V
    # → online 侧取证（c2_push_seed.py verify --run-id R）

**为什么必须三段**：`link.case_id`（online 侧判定「本 case 过没过」的唯一锚）必须等于载荷里的
`case_id` = offline `TestCase.id`（自增，建前不可知）；而 offline 建 run 后**立刻执行**，载荷的
`trigger_signal_id`（= 簇 id）必须在执行前就写进 case 信封。故：先建 case 拿 id → 用它种簇拿
簇 id → 回写信封建 run。

**真**的部分：真库、真 `create_error_regression_run`、真 `_run_error` 判定、真
`_finish_error_regression` 收尾、真 `fire_push` 触发、真 `error_push` 组装与分片、真
`backflow_client.push_results` → **真 HTTP 打 online**。
**桩**的部分：`execute_case`（HTTP 执行层，回答由本脚本给定）——沿用 C1 探针同款。

**本探针证不了什么**（显式声明）：C3 触发路径（本探针直接调建单函数）、C4 并发槽池、
online 侧判定语义本身（那是 online 侧的验证面）、真 HTTP 打被测 agent。
"""
import argparse
import asyncio
import json
import sys
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import Settings
from app.models import Agent, AgentInterface, EvalRun, TestCase, TestSuite
from app.runner import error_push, orchestrator as orch_mod
from app.runner.executor import CaseOutcome
from app.runner.orchestrator import create_error_regression_run

KEYWORD = "抱歉，我暂时无法回答"
GOOD_ANSWER = "您好，这个问题这样处理：先点右上角设置，再选择导出。"
TERMINAL = {"completed", "partial_failed", "timeout", "cancelled", "scoring_failed"}
AGENT_NAME = "probe-c2-push"
N_CASES = 3
assert KEYWORD not in GOOD_ANSWER, "答必须真不含关键词，否则 pass 不成立"

PASS_COUNT = 0
FAIL_COUNT = 0

# 真推送记录的响应（本探针**只观测不改写**：recorder 调原始函数后追加，非替身）
CALLS: list[dict] = []


def _check(name, got, want) -> None:
    global PASS_COUNT, FAIL_COUNT
    if got == want:
        PASS_COUNT += 1
        print(f"  PASS  {name}")
    else:
        FAIL_COUNT += 1
        print(f"  FAIL  {name}: got={got!r} want={want!r}")


async def _ensure_cases(engine) -> tuple[int, int, list[int]]:
    """幂等建 agent + interface + error suite + 3 条 error case（信封簇 id 留 0 占位）。"""
    async with AsyncSession(engine) as s:
        agent = (await s.scalars(select(Agent).where(Agent.name == AGENT_NAME))).first()
        if agent is None:
            agent = Agent(name=AGENT_NAME, base_url="http://mock.local", adapter_type="config",
                          adapter_config={}, enabled=True, contract_version="1.0")
            s.add(agent)
            await s.flush()
        iface = (await s.scalars(select(AgentInterface).where(
                AgentInterface.agent_id == agent.id,
                AgentInterface.name == "probe-c2-iface"))).first()
        if iface is None:
            iface = AgentInterface(agent_id=agent.id, name="probe-c2-iface", path="/v1/chat",
                                   method="POST", contract_type="sse", contract_version="1.0",
                                   enabled=True)
            s.add(iface)
            await s.flush()
        suite = (await s.scalars(select(TestSuite).where(
            TestSuite.agent_id == agent.id, TestSuite.is_error_suite.is_(True)))).first()
        if suite is None:
            suite = TestSuite(agent_id=agent.id, name="probe-c2-error-suite", is_error_suite=True)
            s.add(suite)
            await s.flush()
        case_ids: list[int] = []
        for i in range(N_CASES):
            pid = f"probe-c2-payload-{i}"
            case = (await s.scalars(select(TestCase).where(
                TestCase.suite_id == suite.id, TestCase.payload_id == pid))).first()
            if case is None:
                case = TestCase(
                    suite_id=suite.id, interface_id=iface.id, name=f"backflow:{pid}",
                    input_type="text", input=f"批C2推送探针 #{i}",
                    case_type="regression_error", payload_id=pid,
                    # interface 只是落库完整；簇 id 由 go 段回写
                    backflow_envelope={"source": {"agent": AGENT_NAME, "interface": "POST /v1/chat",
                                                  "cluster_id": 0}},
                    expected=None, metrics=None,
                    assertions=[{"op": "keyword_not_contains",
                                 "args": {"path": "answer", "keywords": [KEYWORD]}}],
                    status="active", is_gold=False)
                s.add(case)
                await s.flush()
            case.status = "active"
            case_ids.append(case.id)
        # ⚠️ 必须在 commit **前**取标量：本脚本用的是裸 `AsyncSession(engine)`（默认
        # expire_on_commit=True，与 app 的 `SessionLocal` 相反）⇒ commit 后读属性会惰性
        # 刷新，而该访问发生在 greenlet 之外 ⇒ MissingGreenlet（本批实测踩到）。
        ids = (agent.id, suite.id, case_ids)
        await s.commit()
        return ids


async def _await_terminal(engine, run_id: int) -> str:
    for _ in range(120):
        await asyncio.sleep(0.5)
        async with AsyncSession(engine) as s:
            st = await s.scalar(select(EvalRun.status).where(EvalRun.id == run_id))
        if st in TERMINAL:
            return st
    raise TimeoutError(f"run {run_id} 未到终态（最后 {st}）")


def _install_recorder() -> None:
    """**观测**用：包一层调原始函数，记录每次出站的簇 id 与响应。非替身、不改变行为。"""
    orig = error_push.backflow_client.push_results

    async def rec(body):
        resp = await orig(body)
        CALLS.append({"cluster": body.get("trigger_signal_id"), "run_id": body.get("run_id"),
                      "resp": resp, "at": datetime.now().isoformat(timespec="seconds")})
        return resp

    error_push.backflow_client.push_results = rec


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["prepare", "go"])
    ap.add_argument("--clusters")
    ap.add_argument("--version", default="2026.09.14-c2r1")
    a = ap.parse_args()
    engine = create_async_engine(Settings().sqlalchemy_url)  # URL 含口令，不打印
    try:
        agent_id, suite_id, case_ids = await _ensure_cases(engine)
        if a.mode == "prepare":
            print(f"PREPARE_OK agent={agent_id} suite={suite_id} "
                  f"case_ids={','.join(str(c) for c in case_ids)}")
            return

        clusters = [int(x) for x in a.clusters.split(",")]
        assert len(clusters) == len(case_ids) == N_CASES, "簇数必须等于 case 数"
        async with AsyncSession(engine) as s:
            for cid, cluster_id in zip(case_ids, clusters):
                c = await s.get(TestCase, cid)
                env = dict(c.backflow_envelope or {})
                src = dict(env.get("source") or {})
                src["cluster_id"] = cluster_id
                env["source"] = src
                c.backflow_envelope = env
            await s.commit()
        print(f"[go] 信封簇 id 已回写：{dict(zip(case_ids, clusters))}")

        _install_recorder()  # 必须在建 run 之前（fire_push 在收尾后立刻发）
        holder["fn"] = lambda: CaseOutcome(unified={"answer": GOOD_ANSWER}, timing={},
                                           status_code=200)
        run_id = await create_error_regression_run(agent_id=agent_id, suite_id=suite_id,
                                                   version=a.version, signal_run_id=424243)
        status = await _await_terminal(engine, run_id)
        async with AsyncSession(engine) as s:
            run = await s.get(EvalRun, run_id)
            print(f"[go] run={run_id} version={run.version} status={status} "
                  f"pass={run.pass_case} fail={run.fail_case} na={run.na_case}")
        print("[go] 等 fire-and-forget 推送到位 …")
        for _ in range(40):
            await asyncio.sleep(0.5)
            if len(CALLS) >= N_CASES:
                break
        first = list(CALLS)
        _check("首推覆盖全部簇（分片生效）", sorted(c["cluster"] for c in first), sorted(clusters))
        for c in first:
            r = c["resp"]
            print(f"  首推 cluster={c['cluster']} accepted={r.get('accepted')} "
                  f"duplicated={r.get('duplicated')} run_record_id={r.get('run_record_id')} "
                  f"links_advanced={r.get('links_advanced')} dropped={r.get('cases_dropped')}")
            _check(f"首推 cluster={c['cluster']} accepted", r.get("accepted"), True)
            _check(f"首推 cluster={c['cluster']} 非重放", r.get("duplicated"), False)

        # 幂等重推（§10.1：响应只记日志；本段只取证，不据回执做业务分支）。
        # 重推**仍然分两支走两条不同路径**——link 仍 pending 的簇命中 `_find_current_link`；
        # 首推已判出终态的簇取不到现行 link、走 orphan。但**两支的幂等结论必须一致**：
        # online 的幂等键已改为 `(cluster_id, run_id)`（= 载荷 `trigger_signal_id` + run），
        # 它**不随 link 生命周期变** ⇒ 两支都该命中首推那行、duplicated=true、同一 run_record_id。
        #
        # ⚠️ 本段在 2026-09-14 被**重述过**：改键之前，已判出簇重推会退化成哨兵 (0, run_id)，
        # 首条 orphan 落**新行**、duplicated=false，且后到的簇会命中**别的簇**的行（真机实测
        # 3849/3850 两簇互串）。当时的断言写的是那个病灶态（「未复用原 run 行」）。
        # 键改完后该断言必红——**是断言随契约变、不是把红改绿**：判别性证据 = 同一 run 的
        # 每个簇在 online 库里**恰好一行**，且三簇重推各命中自己的行（见 c2_push_seed verify）。
        advanced = {c["cluster"] for c in first if c["resp"].get("links_advanced")}
        stayed = {c["cluster"] for c in first} - advanced
        assert advanced and stayed, f"两条重推路径各需至少一簇：advanced={advanced} stayed={stayed}"
        print(f"[go] 首推已离开 pending 的簇={sorted(advanced)}；仍 pending 的簇={sorted(stayed)}")
        del CALLS[:]
        await error_push.push_run_results(run_id)
        second = list(CALLS)
        _check("重推同样覆盖全部簇", sorted(c["cluster"] for c in second), sorted(clusters))
        rec_by_cluster = {c["cluster"]: c["resp"] for c in first}
        for c in second:
            r, cid = c["resp"], c["cluster"]
            path = "orphan" if cid in advanced else "现行 link"
            print(f"  重推 cluster={cid}（走 {path}）duplicated={r.get('duplicated')} "
                  f"run_record_id={r.get('run_record_id')} links_advanced={r.get('links_advanced')}")
            _check(f"重推 cluster={cid} 无新迁移", r.get("links_advanced"), [])
            _check(f"重推 cluster={cid} duplicated", r.get("duplicated"), True)
            _check(f"重推 cluster={cid} 命中本簇首推的 run 行",
                   r.get("run_record_id"), rec_by_cluster[cid].get("run_record_id"))

        print(json.dumps({"run_id": run_id, "version": a.version, "clusters": clusters,
                          "case_ids": case_ids}, ensure_ascii=False))
        print(f"\nPROBE_RESULT pass={PASS_COUNT} fail={FAIL_COUNT}")
        if FAIL_COUNT:
            sys.exit(1)
    finally:
        await engine.dispose()


holder: dict = {}


async def _fake_execute(adapter, client, case, timeout_s):
    return holder["fn"]()


orch_mod.execute_case = _fake_execute  # 探针进程内替换，仅本脚本生效

if __name__ == "__main__":
    print(f"[probe] start {datetime.now().isoformat(timespec='seconds')}")
    asyncio.run(main())
