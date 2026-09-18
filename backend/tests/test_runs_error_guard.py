"""§7.5 项 2/3/4：error run 与 manual 通道隔离（api 层策略单测）。

**这份测试能证明什么、不能证明什么**（先说清楚，免得被 N/N 全绿骗到）：

- **能证明**：`api/runs.py` 发出的 SQL **文本里带上了** `trigger_type != 'error_regression'`
  排除条件；两份守卫（error suite / error run 拒绝）在分支上确实会抛 400。
- **不能证明**：「error run 真的不占名额」——**SQL 语义**（真 MySQL 怎么算）不在替身的能力范围内，
  由真库探针 `tests/integration/run_guard_probe.py` 负责。**别用这里的绿去替代那份探针。**

替身 `_FakeDB` 的一处刻意设计：`eval_run.id` 分支**照着自己收到的 SQL 文本决定要不要排除
error run**。这样「实现里漏了排除条件」会让替身也照旧计入 ⇒ 测试**真的会红**；若写成固定的
内存过滤，缺守卫的实现照样绿——那才是把自己假设测成结论。
"""
from types import SimpleNamespace

import pytest

from app.api.runs import _ERROR_TRIGGER, _MUTEX_STATUS, create_run, rerun_run
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
    """create_run/rerun_run 查询分派。`sql_seen` 记录收到的全部 SQL 文本（供策略断言）。"""

    def __init__(self, suite=None, active=None, src=None, src_suite=None, case_count=1):
        self._suite = suite
        self._active = active or []          # [{id, status, trigger_type}]
        self._src = src
        self._src_suite = src_suite
        self._case_count = case_count
        self.sql_seen = []
        self.added = []

    async def get(self, model, pk, with_for_update=False):
        name = model.__name__
        if name == "Agent":
            return SimpleNamespace(id=1, enabled=True)
        if name == "TestSuite":
            # rerun 侧按 src.suite_id 查；create 侧复用 self._suite
            return self._src_suite if self._src is not None else self._suite
        if name == "EvalRun":
            return self._src
        return None

    async def execute(self, stmt):
        s = str(stmt)
        self.sql_seen.append(s)
        low = s.lower()
        if "test_case" in low:
            return _ScalarResult([(self._case_count,)])
        if "eval_run.id" in low:
            rows = [a for a in self._active if a.status in _MUTEX_STATUS]
            # 照 SQL 文本决定是否排除——实现漏了排除条件，这里就不会排除（见模块 docstring）
            if "trigger_type !=" in low:
                rows = [r for r in rows if r.trigger_type != _ERROR_TRIGGER]
            return _ScalarResult(rows)
        return _ScalarResult([])

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass


def _body(**kw):
    p = {"agent_id": 1, "suite_id": 1, "version": "1.2.3", "trigger_type": "manual",
         "case_ids": None}
    p.update(kw)
    return SimpleNamespace(**p)


def _user():
    return SimpleNamespace(role="admin", username="u1")


def _row(rid, status, trigger_type="manual"):
    """active 计数行（替身按 status 过滤、按 SQL 文本决定是否排除 error）。"""
    return SimpleNamespace(id=rid, status=status, trigger_type=trigger_type)


def _patch(monkeypatch):
    async def _stub(*a, **k):
        return None

    monkeypatch.setattr("app.api.runs.orchestrator.start_run", _stub)
    monkeypatch.setattr("app.api.runs.write_audit", _stub)


# ---------------- 项 2：活跃计数排除 error run ----------------
class TestActiveCountExcludesErrorRun:
    @pytest.mark.asyncio
    async def test_create_ignores_error_run_occupying_slot(self, monkeypatch):
        """error run 停 pending（占着 §5.2 洪峰配额）时，manual 建单**不被 409 误挡**。"""
        _patch(monkeypatch)
        db = _FakeDB(suite=SimpleNamespace(id=1, agent_id=1, is_error_suite=False),
                     active=[_row(10, "pending", _ERROR_TRIGGER)])
        await create_run(_body(), _user(), db)
        assert len(db.added) == 1

    @pytest.mark.asyncio
    async def test_create_run_sql_carries_trigger_exclusion(self, monkeypatch):
        """策略断言：计数 SQL 文本里必须出现 trigger_type 的不等条件（防实现被改回去）。"""
        _patch(monkeypatch)
        db = _FakeDB(suite=SimpleNamespace(id=1, agent_id=1, is_error_suite=False),
                     active=[_row(10, "pending", _ERROR_TRIGGER)])
        await create_run(_body(), _user(), db)
        count_sql = [s for s in db.sql_seen if "eval_run.id" in s.lower()]
        assert count_sql, "未捕获到互斥计数 SQL"
        assert "trigger_type !=" in count_sql[0].lower()

    @pytest.mark.asyncio
    async def test_manual_run_still_409_contrast(self, monkeypatch):
        """**对照项**：同一现场换成 manual run 占槽 ⇒ 仍须 409（否则说明计数路径没被走到）。"""
        _patch(monkeypatch)
        db = _FakeDB(suite=SimpleNamespace(id=1, agent_id=1, is_error_suite=False),
                     active=[_row(10, "pending", "manual")])
        with pytest.raises(ApiError) as ei:
            await create_run(_body(), _user(), db)
        assert ei.value.status_code == 409

    @pytest.mark.asyncio
    async def test_rerun_ignores_error_run_and_emits_exclusion(self, monkeypatch):
        """rerun 侧同两项：不挡 + SQL 带排除。"""
        _patch(monkeypatch)
        src = SimpleNamespace(id=5, agent_id=1, suite_id=1, trigger_type="manual",
                              status="completed", case_ids=None, run_config={}, version="1.2.3")
        db = _FakeDB(src=src, src_suite=SimpleNamespace(id=1, agent_id=1, is_error_suite=False),
                     active=[_row(10, "running", _ERROR_TRIGGER)])
        await rerun_run(5, SimpleNamespace(), _user(), db)
        assert len(db.added) == 1
        count_sql = [s for s in db.sql_seen if "eval_run.id" in s.lower()]
        assert count_sql and "trigger_type !=" in count_sql[0].lower()


# ---------------- 项 3：error suite 拒绝 ----------------
class TestErrorSuiteRejected:
    @pytest.mark.asyncio
    async def test_create_run_on_error_suite_400(self, monkeypatch):
        _patch(monkeypatch)
        db = _FakeDB(suite=SimpleNamespace(id=1, agent_id=1, is_error_suite=True))
        with pytest.raises(ApiError) as ei:
            await create_run(_body(), _user(), db)
        assert ei.value.status_code == 400
        assert db.added == []          # 拒绝在建 run 之前，不产生空转 run

    @pytest.mark.asyncio
    async def test_rerun_run_on_error_suite_400(self, monkeypatch):
        """项 3 的 rerun 半边：error suite 下的 manual run（项 3 落地前的存量形态）也不可重跑。"""
        _patch(monkeypatch)
        src = SimpleNamespace(id=5, agent_id=1, suite_id=9, trigger_type="manual",
                              status="completed", case_ids=None, run_config={}, version="1.2.3")
        db = _FakeDB(src=src, src_suite=SimpleNamespace(id=9, agent_id=1, is_error_suite=True))
        with pytest.raises(ApiError) as ei:
            await rerun_run(5, SimpleNamespace(), _user(), db)
        assert ei.value.status_code == 400


# ---------------- 项 4：rerun 拒绝 error run ----------------
class TestRerunRejectsErrorRun:
    @pytest.mark.asyncio
    async def test_rerun_error_regression_400(self, monkeypatch):
        """error run 由内部创建器直插 DB、API 可见；不拦则 rerun 会再产出一条 error run。"""
        _patch(monkeypatch)
        src = SimpleNamespace(id=7, agent_id=1, suite_id=9, trigger_type=_ERROR_TRIGGER,
                              status="completed", case_ids=None, run_config={}, version="1.2.3")
        db = _FakeDB(src=src, src_suite=SimpleNamespace(id=9, agent_id=1, is_error_suite=True))
        with pytest.raises(ApiError) as ei:
            await rerun_run(7, SimpleNamespace(), _user(), db)
        assert ei.value.status_code == 400
        assert "error_regression" in ei.value.message    # 报的是项 4 而非项 3（顺序断言）
        assert db.added == []
