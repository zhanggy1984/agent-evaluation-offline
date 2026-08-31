"""V1 防 0-case 空转：create_run / rerun_run 全量触发时 suite 无匹配 active 用例 → 400。

mock DB 跑 runs.py 的 create_run/rerun_run（case_count=0 触发空转校验）。覆盖：
- create_run 全量 + suite 无 active 用例 → 400（空转提示）
- create_run 全量 + 有用例 → 正常建 run
- rerun_run 源全量 + suite 用例被禁用 → 400（与 create_run 同校验）
- _ensure_suite_has_cases 子集短路：case_ids 非空 → 不查 count（子集已有逐 id 校验）
"""
from types import SimpleNamespace

import pytest

from app.api.runs import _ensure_suite_has_cases, create_run, rerun_run
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
    """create_run/rerun_run 查询分派：test_case→V1 count（case_count=0 触发空转 400），
    eval_run.id→active 互斥（空→可建），system_config→兜底。"""

    def __init__(self, case_count=0, active=None):
        self._case_count = case_count
        self._active = active or []
        self._src = None
        self.added = []

    async def get(self, model, pk, with_for_update=False):
        name = model.__name__
        if name == "Agent":
            return SimpleNamespace(id=1, enabled=True)
        if name == "TestSuite":
            return SimpleNamespace(id=1, agent_id=1)
        if name == "EvalRun":
            return self._src
        return None

    async def execute(self, stmt):
        s = str(stmt).lower()
        if "test_case" in s:
            if "count(" in s:                 # V1 空转校验 count → scalar tuple
                return _ScalarResult([(self._case_count,)])
            return _ScalarResult([])          # _validate_case_ids 列表查询（子集路径不走本测试）
        if "eval_run.id" in s:
            return _ScalarResult(self._active)
        return _ScalarResult([])

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass


def _user():
    return SimpleNamespace(role="admin", username="u1")


def _req():
    return SimpleNamespace()


def _patch(monkeypatch):
    async def _stub(*a, **k):
        return None

    monkeypatch.setattr("app.api.runs.orchestrator.start_run", _stub)
    monkeypatch.setattr("app.api.runs.write_audit", _stub)


def _body(**kw):
    p = dict(agent_id=1, suite_id=1, version="1.2.3", trigger_type="manual", case_ids=None)
    p.update(kw)
    return SimpleNamespace(**p)


# ---------------- create_run：全量空转校验 ----------------
class TestCreateRunEmpty:
    @pytest.mark.asyncio
    async def test_full_empty_suite_400(self, monkeypatch):
        # suite 无匹配 active 用例 → 全量触发空转 → 400 提示（不再建 0-case run）
        db = _FakeDB(case_count=0)
        _patch(monkeypatch)
        with pytest.raises(ApiError) as ei:
            await create_run(_body(), _user(), db)
        assert ei.value.status_code == 400
        assert "active 用例" in ei.value.message

    @pytest.mark.asyncio
    async def test_full_has_cases_ok(self, monkeypatch):
        db = _FakeDB(case_count=3)
        _patch(monkeypatch)
        await create_run(_body(), _user(), db)
        assert len(db.added) == 1
        assert db.added[0].status == "pending"


# ---------------- rerun_run：源全量继承同校验 ----------------
class TestRerunEmpty:
    def _src(self):
        return SimpleNamespace(id=1, agent_id=1, suite_id=1, version="1.0.0",
                               trigger_type="manual", run_config={}, case_ids=None,
                               status="completed")

    @pytest.mark.asyncio
    async def test_rerun_full_empty_suite_400(self, monkeypatch):
        # 源 run 全量 + suite 用例随后被禁用 → 重跑同样空转 → 400
        db = _FakeDB(case_count=0)
        db._src = self._src()
        _patch(monkeypatch)
        with pytest.raises(ApiError) as ei:
            await rerun_run(1, _req(), _user(), db)
        assert ei.value.status_code == 400
        assert "active 用例" in ei.value.message

    @pytest.mark.asyncio
    async def test_rerun_full_has_cases_ok(self, monkeypatch):
        db = _FakeDB(case_count=2)
        db._src = self._src()
        _patch(monkeypatch)
        await rerun_run(1, _req(), _user(), db)
        assert len(db.added) == 1


# ---------------- 子集短路（逻辑单测，直接调 _ensure_suite_has_cases） ----------------
class TestSubsetSkipsEmptyCheck:
    @pytest.mark.asyncio
    async def test_subset_returns_without_count(self):
        # case_ids 非空 → 直接 return，不查 count（case_count=0 也不抛；子集由逐 id 校验保护）
        db = _FakeDB(case_count=0)
        await _ensure_suite_has_cases(db, 1, "manual", [5])   # 不抛即通过
