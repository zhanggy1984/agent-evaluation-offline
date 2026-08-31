"""7.8 前置④ per-run 桶接线单测：start_run 按 run_config 建桶 → case 并发受 per_agent 约束 → _finish drop。

真实走 start_run 的限流接线路径（mock 掉 DB/HTTP/执行细节，acquire/release/drop 保留真实），
宿主可跑。锁定的回归点：
1. start_run 从 run.run_config 读 global_max_inflight/per_agent_concurrency 建 per-run 桶
2. per_agent_concurrency=1 时同 run 多 case 串行（acquire 被信号量挡）；=3 时并行放行
3. _finish 对账后 drop_run_limits 清理该 run 桶，无残留
"""
import asyncio

import pytest

import app.core.circuit_repo as circuit_repo
from app.runner.executor import CaseOutcome
from app.runner.orchestrator import RunOrchestrator

pytestmark = pytest.mark.asyncio

RUN_ID = 1


class _Run:
    id = RUN_ID
    agent_id = 1
    suite_id = 1
    trigger_type = "manual"
    status = "pending"
    generation = 1
    total_case = 3
    started_at = None
    finished_at = None
    lease_until = None
    hard_deadline = None
    env_snapshot = None
    case_ids = None  # #3 子集：_load_run_cases 访问 run.case_ids，mock 需有该字段（None=全量）


class _Agent:
    id = 1
    enabled = True
    contract_version = "1.0"
    adapter_config = {"timeout": 30}
    auth_config = {}


class _Suite:
    id = 1
    agent_id = 1


class _Case:
    def __init__(self, cid: int):
        self.id = cid
        self.suite_id = 1
        self.interface_id = 1
        self.metrics = {"completeness": {"enabled": True}}


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeDB:
    """start_run 用到的查询分派：EvalRun/Agent/TestSuite 走 get，TestCase 走 execute，其余空。"""

    def __init__(self, run, agent, suite, cases):
        self._run, self._agent, self._suite, self._cases = run, agent, suite, cases

    async def get(self, model, pk, with_for_update=False):
        # P2-D6：_finish/_fail_run 改用锁定读（FOR UPDATE），mock 忽略该参数（单测不验证锁）
        name = getattr(model, "__name__", str(model))
        if name == "EvalRun":
            return self._run
        if name == "Agent":
            return self._agent
        if name == "TestSuite":
            return self._suite
        return None

    async def execute(self, stmt):
        if "test_case" in str(stmt).lower():
            return _ScalarResult(self._cases)
        return _ScalarResult([])  # active_runs / EvalResult 等

    def add(self, obj):
        pass  # P2-D6：_finish 对账在锁内事务直插 EvalResult，单测不落库

    async def commit(self):
        pass


class _FakeSession:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *a):
        return False


class _FakeClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _patch_externals(monkeypatch, fake_db, orch):
    """mock start_run 的外部依赖（DB/HTTP/熔断/执行细节），保留限流接线真实。"""
    monkeypatch.setattr("app.runner.orchestrator.SessionLocal", lambda: _FakeSession(fake_db))
    monkeypatch.setattr("app.runner.orchestrator.build_agent_client", lambda: _FakeClient())
    monkeypatch.setattr(orch, "_decrypt_auth", lambda agent: {})
    monkeypatch.setattr(orch, "_probe_before_run", _no_probe)
    monkeypatch.setattr(orch, "_save_result", _no_save)
    monkeypatch.setattr(orch, "_heartbeat", _fake_heartbeat)

    async def _fake_load(db, agent_id, factory):
        return factory

    async def _fake_save(*a, **k):
        pass

    monkeypatch.setattr(circuit_repo, "load", _fake_load)
    monkeypatch.setattr(circuit_repo, "save", _fake_save)

    async def _no_score(*a, **k):
        pass

    monkeypatch.setattr("app.runner.orchestrator.score_run", _no_score)


async def _no_probe(*a, **k):
    return True


async def _no_save(*a, **k):
    pass


async def _fake_heartbeat(*a, **k):
    await asyncio.sleep(3600)  # 由 start_run 的 finally 取消


async def _run_start_run(monkeypatch, per_agent: int, n_cases: int = 3):
    """构造 run.run_config=per_agent_concurrency → 走 start_run，返回 (orch, peak_concurrency)。"""
    run = _Run()
    run.run_config = {"global_max_inflight": 16, "per_agent_concurrency": per_agent}
    agent, suite = _Agent(), _Suite()
    cases = [_Case(i) for i in range(1, n_cases + 1)]
    fake_db = _FakeDB(run, agent, suite, cases)
    orch = RunOrchestrator()
    _patch_externals(monkeypatch, fake_db, orch)

    state = {"active": 0, "peak": 0}
    lock = asyncio.Lock()

    async def _fake_execute_with_retry(*a, **k):
        async with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
            assert run.id in orch._limiter._buckets        # 执行期间桶必须已建好
            assert orch._run_limits[run.id] == (16, per_agent)
        await asyncio.sleep(0.05)
        async with lock:
            state["active"] -= 1
        return CaseOutcome(unified={"answer": "ok"}, timing={}, status_code=200)

    monkeypatch.setattr(orch, "_execute_with_retry", _fake_execute_with_retry)
    await asyncio.wait_for(orch.start_run(RUN_ID), timeout=10)
    return orch, state["peak"]


async def test_start_run_bucket_from_config_per_agent_1_serial(monkeypatch):
    """run_config per_agent_concurrency=1：建桶、3 case 串行（peak=1）、_finish 后 drop 无残留。"""
    orch, peak = await _run_start_run(monkeypatch, per_agent=1, n_cases=3)
    assert peak == 1  # per_agent=1 → 同 run case 严格串行
    assert RUN_ID not in orch._limiter._buckets  # _finish 已 drop
    assert RUN_ID not in orch._run_limits


async def test_start_run_bucket_from_config_per_agent_3_parallel(monkeypatch):
    """run_config per_agent_concurrency=3：3 case 全部并行（peak=3），限制来自 config 非硬编码。"""
    orch, peak = await _run_start_run(monkeypatch, per_agent=3, n_cases=3)
    assert peak == 3  # per_agent=3 ≥ 3 case → 全并行
    assert RUN_ID not in orch._limiter._buckets
    assert RUN_ID not in orch._run_limits


async def test_two_runs_different_limits_do_not_override(monkeypatch):
    """per-run 桶隔离（回归 last-write-wins）：run1 占满全局不挡 run2 独立桶。"""
    run1, run2 = _Run(), _Run()
    run2.id = 2
    run1.run_config = {"global_max_inflight": 1, "per_agent_concurrency": 1}
    run2.run_config = {"global_max_inflight": 16, "per_agent_concurrency": 3}
    fake_db = _FakeDB(run1, _Agent(), _Suite(), [_Case(1), _Case(2), _Case(3)])
    orch = RunOrchestrator()
    _patch_externals(monkeypatch, fake_db, orch)
    # 不完整 start_run，直接验证 orchestrator 层 set_run_limits 转发正确（limiter 层隔离由 test_limiter 覆盖）
    orch.set_run_limits(run1.id, 1, 1)
    orch.set_run_limits(run2.id, 16, 3)
    await orch._limiter.acquire(run1.id, "a")  # run1 全局占满
    await asyncio.wait_for(orch._limiter.acquire(run2.id, "b"), 0.1)  # run2 独立桶不受影响
    orch.drop_run_limits(run1.id)
    orch.drop_run_limits(run2.id)
