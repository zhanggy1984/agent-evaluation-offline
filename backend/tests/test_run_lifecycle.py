"""批次 A A1/B3：_run 生命周期守卫单测（mock DB + fake 外部依赖，宿主可跑）。

覆盖：
- A1 取消后（DB 已 cancelled + 原子 UPDATE rowcount 0）_run return 不改状态（不复活）
- A1 scanner timeout 后（DB 已 timeout + rowcount 0）_run return
- A1 取消标志已置时 _run 在 set_run_limits 之前 return，不建桶不泄漏（防 H1）
- A1 正常 pending 流程回归（rowcount 1 → 取消检查过 → set_run_limits 建桶）
- B3 probe 失败 return 前 drop_run_limits + 清取消标志 + 清心跳（不经 _finish 的退出路径）
"""
import asyncio

import pytest

import app.core.circuit_repo as circuit_repo
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
    total_case = 1
    started_at = None
    finished_at = None
    lease_until = None
    hard_deadline = None
    env_snapshot = None
    case_ids = None  # #3 子集：_load_run_cases 访问 run.case_ids，mock 需有该字段（None=全量）
    run_config = {"global_max_inflight": 16, "per_agent_concurrency": 3,
                  "case_timeout": 120, "perf_repeat_count": 5, "max_retries": 1,
                  "breaker_failure_threshold": 5, "breaker_open_duration": 30,
                  "heartbeat_interval": 30, "lease_seconds": 90}


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


class _Cfg:
    """SystemConfig 假行：仅暴露 value（P0-3 测试用）。"""

    def __init__(self, value):
        self.value = value


class _ScalarResult:
    def __init__(self, rows, rowcount=1):
        self._rows = rows
        self.rowcount = rowcount

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeDB:
    """_run 查询分派：EvalRun/Agent/TestSuite 走 get；update(EvalRun) 按 _update_rowcount 返回；
    test_case 走 execute 返回 cases；其余 select 空。"""

    def __init__(self, run, agent, suite, cases, update_rowcount=1):
        self._run, self._agent, self._suite, self._cases = run, agent, suite, cases
        self._update_rowcount = update_rowcount

    async def get(self, model, pk, with_for_update=False):
        name = getattr(model, "__name__", str(model))
        if name == "EvalRun":
            return self._run
        if name == "Agent":
            return self._agent
        if name == "TestSuite":
            return self._suite
        return None

    async def execute(self, stmt):
        s = str(stmt).lower()
        if "test_case" in s:
            return _ScalarResult(self._cases)
        if "update eval_run" in s:          # A1 原子条件更新（WHERE status='pending'）
            return _ScalarResult([], rowcount=self._update_rowcount)
        return _ScalarResult([])             # active_runs / EvalResult 等

    def add(self, obj):
        pass

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


def _patch_externals(monkeypatch, fake_db, orch, *, probe=True):
    """mock _run 的外部依赖（DB/HTTP/执行细节），保留生命周期守卫逻辑真实。"""
    monkeypatch.setattr("app.runner.orchestrator.SessionLocal", lambda: _FakeSession(fake_db))
    monkeypatch.setattr("app.runner.orchestrator.build_agent_client", lambda **kw: _FakeClient())
    monkeypatch.setattr(orch, "_decrypt_auth", lambda agent: {})

    async def _probe(*a, **k):
        return probe

    monkeypatch.setattr(orch, "_probe_before_run", _probe)

    async def _no_save(*a, **k):
        pass

    monkeypatch.setattr(orch, "_save_result", _no_save)
    monkeypatch.setattr(orch, "_finish", _no_save)      # 收尾对账不测，避免干扰断言

    async def _no_run_one(*a, **k):
        pass

    monkeypatch.setattr(orch, "_run_one", _no_run_one)

    async def _fake_heartbeat(*a, **k):
        await asyncio.sleep(3600)  # 由 start_run 的 finally 取消

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


def _make(run_status="pending", update_rowcount=1):
    run = _Run()
    run.status = run_status
    db = _FakeDB(run, _Agent(), _Suite(), [_Case(1)], update_rowcount=update_rowcount)
    orch = RunOrchestrator()
    return run, db, orch


async def test_cancelled_run_not_resurrected(monkeypatch):
    """A1：DB 已 cancelled（API 取消失效落库）→ 原子 UPDATE rowcount 0 → _run return，不覆盖状态。"""
    run, db, orch = _make(run_status="cancelled", update_rowcount=0)
    _patch_externals(monkeypatch, db, orch)
    await orch._run(RUN_ID)
    assert run.status == "cancelled"          # 未被改回 running
    assert RUN_ID not in orch._run_limits     # set_run_limits 之前的退出路径，不建桶


async def test_scanner_timeout_run_not_resurrected(monkeypatch):
    """A1：scanner 已按租约标 timeout → 同样 rowcount 0 → 不拉回执行。"""
    run, db, orch = _make(run_status="timeout", update_rowcount=0)
    _patch_externals(monkeypatch, db, orch)
    await orch._run(RUN_ID)
    assert run.status == "timeout"


async def test_cancel_flag_early_exit_no_bucket(monkeypatch):
    """A1：取消标志已置（cancel_run 竞态：条件 UPDATE 成功后取消落库）→ set_run_limits 前 return，不建桶。"""
    run, db, orch = _make()
    _patch_externals(monkeypatch, db, orch)
    orch._cancel[RUN_ID] = True
    await orch._run(RUN_ID)
    assert RUN_ID not in orch._run_limits     # 防 H1 桶泄漏
    assert RUN_ID not in orch._limiter._buckets


async def test_normal_pending_flow_sets_bucket(monkeypatch):
    """A1 回归：pending → 条件 UPDATE 成功（rowcount 1）→ 取消检查过 → set_run_limits 建桶。"""
    run, db, orch = _make()
    _patch_externals(monkeypatch, db, orch)
    await orch._run(RUN_ID)
    assert orch._run_limits[RUN_ID] == (16, 3)   # global_max_inflight / per_agent_concurrency
    assert RUN_ID in orch._limiter._buckets


async def test_run_client_carries_allowlist_cidrs(monkeypatch):
    """P0-3：_run 构建出站 client 透传注册期自定义 CIDR 白名单（base_url_allowlist）。

    曾只 scaffold 探测传 cidrs（scaffold.py:63-65），运行路径漏传 → 自定义 CIDR 内
    agent 执行时被 _resolve 拒绝（SSRF: 不在出站白名单）导致整 run 失败。
    """
    run, db, orch = _make()
    _patch_externals(monkeypatch, db, orch)

    async def _get_with_cfg(model, pk, with_for_update=False):
        if getattr(model, "__name__", "") == "SystemConfig" and pk == "base_url_allowlist":
            return _Cfg(["11.0.0.0/8"])
        return await _orig_get(model, pk, with_for_update)

    _orig_get = db.get
    db.get = _get_with_cfg

    calls = {}

    def _recording_client(**kw):
        calls.update(kw)
        return _FakeClient()

    # 在 _patch_externals 之后覆盖，保证记录 stub 生效（monkeypatch 后写覆盖前写）
    monkeypatch.setattr("app.runner.orchestrator.build_agent_client", _recording_client)
    await orch._run(RUN_ID)
    assert calls.get("extra_cidrs") == ["11.0.0.0/8"]


async def test_probe_fail_drops_bucket(monkeypatch):
    """B3：probe 失败 → return 前 drop_run_limits + 清取消标志 + 清心跳（不经 _finish 的退出路径）。"""
    run, db, orch = _make()
    _patch_externals(monkeypatch, db, orch, probe=False)
    orch._run_limits[RUN_ID] = (16, 3)  # 预置：probe 失败前 set_run_limits 已建桶
    await orch._run(RUN_ID)
    assert RUN_ID not in orch._run_limits
    assert RUN_ID not in orch._limiter._buckets
    assert RUN_ID not in orch._cancel
    assert RUN_ID not in orch._heartbeats
