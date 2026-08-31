"""#4 放开并发：同 agent 并发 run 上限 N + scoring 不占互斥槽 + rerun 源护栏。

mock DB 跑 runs.py 的 create_run/rerun_run/_max_active_runs（不触真实 DB/网络）。
_FakeDB 的 "eval_run.id" 分派按 _MUTEX_STATUS 过滤 active（模拟 SQL where status.in_），
SystemConfig 查询可注入（缺行兜底 1）。覆盖：
- N=1 回归：缺配置行兜底 1；1 个 running/pending → 409；0 个 → 可建
- N=2：1 个执行中可建、2 个 → 409
- scoring 不占互斥槽：N=1 + 1 个 scoring → 可建（判分期间可开新 run）
- 配置坏值兜底 1（不抛）
- rerun：源 run 执行中（pending/running）→ 400；终态源 → 正常重跑
"""
from types import SimpleNamespace

import pytest

from app.api.runs import _MUTEX_STATUS, create_run, rerun_run
from app.core.errors import ApiError


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._rows[0][0] if self._rows else None


class _FakeDB:
    """create_run/rerun_run 的查询分派：get→agent/suite/src，eval_run→按 _MUTEX_STATUS 过滤，
    system_config→注入配置（缺行兜底）；test_case→V1 空转校验的 count（默认 1=suite 有 active 用例）。"""

    def __init__(self, agent=None, suite=None, active=None, sys_cfg=None, case_count=1):
        self._agent, self._suite = agent, suite
        self._active = active or []            # [{id, status}]
        self._sys_cfg = dict(sys_cfg or [])    # key -> value（global scope）
        self._case_count = case_count          # V1：suite 匹配的 active 用例数（0 → 空转 400）
        self._src = None
        self.added = []

    async def get(self, model, pk, with_for_update=False):
        name = model.__name__
        if name == "Agent":
            return self._agent
        if name == "TestSuite":
            return self._suite
        if name == "EvalRun":
            return self._src
        return None

    async def execute(self, stmt):
        s = str(stmt).lower()
        if "test_case" in s:
            return _ScalarResult([(self._case_count,)])   # V1 空转校验 count
        if "eval_run.id" in s:
            # #4：互斥计数只算执行中（pending/running），scoring 不占槽
            rows = [a for a in self._active if a.status in _MUTEX_STATUS]
            return _ScalarResult(rows)
        if "system_config" in s:
            return _ScalarResult([SimpleNamespace(key=k, value=v)
                                  for k, v in self._sys_cfg.items()])
        return _ScalarResult([])

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass


def _user():
    return SimpleNamespace(role="admin", username="u1")


def _req():
    return SimpleNamespace()


def _agent():
    return SimpleNamespace(id=1, enabled=True)


def _suite():
    return SimpleNamespace(id=1, agent_id=1)


def _body(**kw):
    p = dict(agent_id=1, suite_id=1, version="1.2.3", trigger_type="manual", case_ids=None)
    p.update(kw)
    return SimpleNamespace(**p)


async def _create(db, monkeypatch):
    async def _stub_start(*a):
        return None

    monkeypatch.setattr("app.api.runs.orchestrator.start_run", _stub_start)
    await create_run(_body(), _user(), db)


def _patch_orchestrator(monkeypatch):
    async def _stub(*a, **k):
        return None

    monkeypatch.setattr("app.api.runs.orchestrator.start_run", _stub)
    monkeypatch.setattr("app.api.runs.write_audit", _stub)


# ---------------- create_run：互斥计数（N 可配） ----------------
class TestCreateRunMutexN:
    @pytest.mark.asyncio
    async def test_no_config_defaults_to_1(self, monkeypatch):
        # 缺配置行（未 seed 库/集成 sqlite）→ 兜底 N=1：1 个 running → 409
        db = _FakeDB(agent=_agent(), suite=_suite(),
                     active=[SimpleNamespace(id=10, status="running")])
        with pytest.raises(ApiError) as ei:
            await _create(db, monkeypatch)
        assert ei.value.status_code == 409

    @pytest.mark.asyncio
    async def test_n1_no_active_ok(self, monkeypatch):
        db = _FakeDB(agent=_agent(), suite=_suite())
        await _create(db, monkeypatch)
        assert len(db.added) == 1
        assert db.added[0].status == "pending"

    @pytest.mark.asyncio
    async def test_n1_pending_also_409(self, monkeypatch):
        # pending（未接管）同样占互斥槽
        db = _FakeDB(agent=_agent(), suite=_suite(),
                     active=[SimpleNamespace(id=10, status="pending")])
        with pytest.raises(ApiError) as ei:
            await _create(db, monkeypatch)
        assert ei.value.status_code == 409

    @pytest.mark.asyncio
    async def test_n2_one_active_ok(self, monkeypatch):
        # N=2 + 1 个 running → 可建（count=1 < 2）
        db = _FakeDB(agent=_agent(), suite=_suite(),
                     active=[SimpleNamespace(id=10, status="running")],
                     sys_cfg=[("max_active_runs_per_agent", 2)])
        await _create(db, monkeypatch)
        assert len(db.added) == 1

    @pytest.mark.asyncio
    async def test_n2_two_active_409(self, monkeypatch):
        db = _FakeDB(agent=_agent(), suite=_suite(),
                     active=[SimpleNamespace(id=10, status="running"),
                             SimpleNamespace(id=11, status="running")],
                     sys_cfg=[("max_active_runs_per_agent", 2)])
        with pytest.raises(ApiError) as ei:
            await _create(db, monkeypatch)
        assert ei.value.status_code == 409

    @pytest.mark.asyncio
    async def test_invalid_config_value_defaults(self, monkeypatch):
        # 配置坏值 → 兜底 1（不抛、不阻塞创建判断）
        db = _FakeDB(agent=_agent(), suite=_suite(),
                     active=[SimpleNamespace(id=10, status="running")],
                     sys_cfg=[("max_active_runs_per_agent", "abc")])
        with pytest.raises(ApiError) as ei:
            await _create(db, monkeypatch)
        assert ei.value.status_code == 409


# ---------------- scoring 不占互斥槽 ----------------
class TestScoringDoesNotOccupySlot:
    @pytest.mark.asyncio
    async def test_scoring_not_counted_n1(self, monkeypatch):
        # N=1 + 1 个 scoring run → 可建（判分期间同 agent 可开新 run）
        db = _FakeDB(agent=_agent(), suite=_suite(),
                     active=[SimpleNamespace(id=10, status="scoring")])
        await _create(db, monkeypatch)
        assert len(db.added) == 1

    @pytest.mark.asyncio
    async def test_scoring_not_counted_n2_with_running(self, monkeypatch):
        # N=2 + 1 running + 1 scoring → count=1 < 2 可建（scoring 不叠加计数）
        db = _FakeDB(agent=_agent(), suite=_suite(),
                     active=[SimpleNamespace(id=10, status="running"),
                             SimpleNamespace(id=11, status="scoring")],
                     sys_cfg=[("max_active_runs_per_agent", 2)])
        await _create(db, monkeypatch)
        assert len(db.added) == 1


# ---------------- rerun：源执行中护栏 ----------------
class TestRerunSourceGuard:
    def _src(self, status="completed"):
        return SimpleNamespace(id=1, agent_id=1, suite_id=2, version="1.0.0",
                               trigger_type="manual", run_config={}, case_ids=None,
                               status=status)

    @pytest.mark.asyncio
    async def test_rerun_source_running_400(self, monkeypatch):
        # #4 护栏：源 run 执行中不可并行重跑（N>1 放行计数下的直连绕过兜底）
        db = _FakeDB(agent=_agent(), suite=_suite())
        db._src = self._src(status="running")
        _patch_orchestrator(monkeypatch)
        with pytest.raises(ApiError) as ei:
            await rerun_run(1, _req(), _user(), db)
        assert ei.value.status_code == 400

    @pytest.mark.asyncio
    async def test_rerun_source_pending_400(self, monkeypatch):
        db = _FakeDB(agent=_agent(), suite=_suite())
        db._src = self._src(status="pending")
        _patch_orchestrator(monkeypatch)
        with pytest.raises(ApiError) as ei:
            await rerun_run(1, _req(), _user(), db)
        assert ei.value.status_code == 400

    @pytest.mark.asyncio
    async def test_rerun_source_completed_ok(self, monkeypatch):
        # 终态源正常重跑（scoring 源不拦——不占执行槽）
        db = _FakeDB(agent=_agent(), suite=_suite())
        db._src = self._src(status="completed")
        _patch_orchestrator(monkeypatch)
        await rerun_run(1, _req(), _user(), db)
        assert len(db.added) == 1

    @pytest.mark.asyncio
    async def test_rerun_source_scoring_allowed(self, monkeypatch):
        # scoring 源允许重跑（复用配置再跑，不占执行槽）
        db = _FakeDB(agent=_agent(), suite=_suite())
        db._src = self._src(status="scoring")
        _patch_orchestrator(monkeypatch)
        await rerun_run(1, _req(), _user(), db)
        assert len(db.added) == 1
