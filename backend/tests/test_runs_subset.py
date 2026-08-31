"""硬伤 #3 定向/子集重跑：case_ids 校验 + 加载 + rerun 继承 + salvage 对账单测。

mock DB 跑 runs.py 的 _validate_case_ids/rerun_run/_run_out 与 case_loader._load_run_cases
（不触真实 DB/网络，orchestrator.start_run/write_audit 打桩）。覆盖：
- _validate_case_ids：None/空=全量、去重保序、无效 case 400（拒绝静默剔除）
- rerun_run：body=None 继承源子集、body.case_ids=None 继承、非空定向改批、互斥 409
- _run_out：case_ids 透出 + redact 裁剪（留出集 owner 不泄露子集指纹）
- _load_run_cases：case_ids 非空附加 in_ 过滤、held_out 标志叠加
- score_run_salvage：对账只补子集内缺失 case（不回填子集外）
"""
import asyncio
from types import SimpleNamespace

import pytest

from app.api.runs import _run_out, _validate_case_ids, rerun_run
from app.core.errors import ApiError
from app.runner.case_loader import _load_run_cases


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None  # #4 _max_active_runs 用


class _FirstResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _FakeDB:
    """rerun_run/_validate_case_ids 的查询分派：get→src，TestCase→cases，active→list（含 status）。"""

    def __init__(self, src=None, cases=None, active=None):
        self._src, self._cases, self._active = src, cases or [], active or []
        self.added = []

    async def get(self, model, pk, with_for_update=False):
        return self._src

    async def execute(self, stmt):
        s = str(stmt).lower()
        if "test_case" in s:
            return _ScalarResult(self._cases)
        if "eval_run.id" in s:
            # #4 互斥计数改 .all()：返回列表（元素带 status，由调用方 SQL 条件过滤）
            return _ScalarResult(self._active)
        if "system_config" in s:
            return _ScalarResult([])  # #4 缺配置行 → _max_active_runs 兜底 1
        return _ScalarResult([])

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass


class _FakeSession:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *a):
        return False


class _FakeSalvageDB:
    """score_run_salvage 的查询分派：EvalResult→已采集，其余空（weights/targets/judge_task）。"""

    def __init__(self, run, results):
        self._run, self._results = run, results
        self.added = []

    async def get(self, model, pk, with_for_update=False):
        return self._run

    async def execute(self, stmt):
        s = str(stmt).lower()
        if "eval_result" in s:
            return _ScalarResult(self._results)
        return _ScalarResult([])

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass


def _user():
    return SimpleNamespace(role="admin", username="u1")


def _req():
    return SimpleNamespace()


async def _stub_audit(*a, **k):
    pass


def _patch_orchestrator(monkeypatch):
    async def _stub_start(*a):
        return None

    monkeypatch.setattr("app.api.runs.orchestrator.start_run", _stub_start)
    monkeypatch.setattr("app.api.runs.write_audit", _stub_audit)


# ---------------- _validate_case_ids ----------------
class TestValidateCaseIds:
    @pytest.mark.asyncio
    async def test_none_is_full(self):
        db = _FakeDB()
        assert await _validate_case_ids(db, 1, "manual", None) is None

    @pytest.mark.asyncio
    async def test_empty_is_full(self):
        db = _FakeDB()
        assert await _validate_case_ids(db, 1, "manual", []) is None

    @pytest.mark.asyncio
    async def test_dedup_keep_order(self):
        # 去重保序：重复 id 只留首个，顺序不变
        db = _FakeDB(cases=[SimpleNamespace(id=3), SimpleNamespace(id=1)])
        out = await _validate_case_ids(db, 1, "manual", [3, 1, 3])
        assert out == [3, 1]

    @pytest.mark.asyncio
    async def test_invalid_case_400(self):
        # 无效 case（不在 suite / 非 active / 留出集不匹配）→ 400（拒绝静默剔除）
        db = _FakeDB(cases=[SimpleNamespace(id=1)])
        with pytest.raises(ApiError) as ei:
            await _validate_case_ids(db, 1, "manual", [1, 99])
        assert ei.value.status_code == 400


# ---------------- rerun_run：继承 vs 定向 ----------------
class TestRerunRun:
    def _src(self, case_ids=None):
        return SimpleNamespace(id=1, agent_id=1, suite_id=2, version="1.0.0",
                               trigger_type="manual", run_config={}, case_ids=case_ids,
                               status="completed")  # #4：源须终态（执行中 rerun 400）

    @pytest.mark.asyncio
    async def test_rerun_inherits_source_subset(self, monkeypatch):
        # 子集 run 重跑：body=None → 继承源 case_ids（重跑同一批）
        db = _FakeDB(src=self._src(case_ids=[10, 20]))
        _patch_orchestrator(monkeypatch)
        await rerun_run(1, _req(), _user(), db)
        assert len(db.added) == 1
        assert db.added[0].case_ids == [10, 20]

    @pytest.mark.asyncio
    async def test_rerun_body_none_inherits(self, monkeypatch):
        # body.case_ids=None → 同样继承源子集
        db = _FakeDB(src=self._src(case_ids=[10, 20]))
        _patch_orchestrator(monkeypatch)
        await rerun_run(1, _req(), _user(), db, SimpleNamespace(case_ids=None))
        assert db.added[0].case_ids == [10, 20]

    @pytest.mark.asyncio
    async def test_rerun_redirect_batch(self, monkeypatch):
        # 定向改批：非空 body.case_ids → 校验后替换（去重保序）
        db = _FakeDB(src=self._src(case_ids=[10, 20]),
                     cases=[SimpleNamespace(id=5), SimpleNamespace(id=7)])
        _patch_orchestrator(monkeypatch)
        await rerun_run(1, _req(), _user(), db, SimpleNamespace(case_ids=[5, 5, 7]))
        assert db.added[0].case_ids == [5, 7]

    @pytest.mark.asyncio
    async def test_rerun_redirect_invalid_400(self, monkeypatch):
        db = _FakeDB(src=self._src(case_ids=None), cases=[SimpleNamespace(id=5)])
        _patch_orchestrator(monkeypatch)
        with pytest.raises(ApiError):
            await rerun_run(1, _req(), _user(), db, SimpleNamespace(case_ids=[5, 999]))

    @pytest.mark.asyncio
    async def test_rerun_mutex_409(self, monkeypatch):
        # 已有执行中 run → 409（缺配置行 → N=1，count=1 >= 1）
        db = _FakeDB(src=self._src(case_ids=None), active=[SimpleNamespace(id=99, status="running")])
        _patch_orchestrator(monkeypatch)
        with pytest.raises(ApiError) as ei:
            await rerun_run(1, _req(), _user(), db)
        assert ei.value.status_code == 409


# ---------------- _run_out：case_ids 透出 + redact ----------------
class TestRunOut:
    def _run(self, **kw):
        p = dict(id=1, agent_id=1, suite_id=1, version="1.0.0", trigger_type="manual",
                 status="completed", generation=1, started_at=None, finished_at=None,
                 total_case=2, pass_case=1, fail_case=0, error_case=0, na_case=1,
                 agent_score=80.0, judge_incomplete=False, ttft_p50=None, e2e_p50=None,
                 case_ids=[10, 20])
        p.update(kw)
        return SimpleNamespace(**p)

    def test_case_ids_passthrough(self):
        out = _run_out(self._run())
        assert out["case_ids"] == [10, 20]

    def test_full_run_case_ids_none(self):
        out = _run_out(self._run(case_ids=None))
        assert out["case_ids"] is None

    def test_redact_hides_case_ids(self):
        # redact（留出集 owner）：case_ids 裁剪——不泄露子集内容指纹；状态型元数据保留
        out = _run_out(self._run(), redact=True)
        assert out["case_ids"] is None
        assert out["agent_score"] is None
        assert out["id"] == 1


# ---------------- _load_run_cases：#3 子集过滤 ----------------
class TestLoadRunCases:
    class _CaptureDB:
        """捕获 stmt 并返回空结果（断言 SQL 条件）。"""

        def __init__(self):
            self.stmt = None

        async def execute(self, stmt):
            self.stmt = stmt
            return _ScalarResult([])

    @pytest.mark.asyncio
    async def test_full_run_no_in_filter(self):
        db = self._CaptureDB()
        run = SimpleNamespace(suite_id=1, trigger_type="manual", case_ids=None)
        await _load_run_cases(db, run)
        assert "test_case.id IN" not in str(db.stmt)

    @pytest.mark.asyncio
    async def test_subset_attaches_in_filter(self):
        db = self._CaptureDB()
        run = SimpleNamespace(suite_id=1, trigger_type="manual", case_ids=[1, 2])
        await _load_run_cases(db, run)
        assert "test_case.id IN" in str(db.stmt)

    @pytest.mark.asyncio
    async def test_held_out_flag_always(self):
        # held_out run → is_held_out 过滤叠加（无论是否有子集）
        db = self._CaptureDB()
        run = SimpleNamespace(suite_id=1, trigger_type="held_out", case_ids=None)
        await _load_run_cases(db, run)
        assert "is_held_out" in str(db.stmt).lower()

    @pytest.mark.asyncio
    async def test_manual_excludes_held_out(self):
        db = self._CaptureDB()
        run = SimpleNamespace(suite_id=1, trigger_type="manual", case_ids=None)
        await _load_run_cases(db, run)
        assert "is_held_out" in str(db.stmt).lower()


# ---------------- score_run_salvage：#3 对账只补子集内 ----------------
class TestSalvageSubsetReconcile:
    @pytest.mark.asyncio
    async def test_reconcile_only_subset_cases(self, monkeypatch):
        from app.runner import scorer

        run = SimpleNamespace(id=1, agent_id=1, suite_id=1, trigger_type="manual",
                              status="timeout", agent_score=None, total_case=3,
                              case_ids=[1, 2])  # 子集 run：只执行 case1/2，case3 不在本 run
        existing = SimpleNamespace(case_id=1)  # case1 已采集
        fake_db = _FakeSalvageDB(run, results=[existing])
        # score_run_salvage 延迟 import SessionLocal（函数内 from app.core.db import）
        monkeypatch.setattr("app.core.db.SessionLocal", lambda: _FakeSession(fake_db))

        async def _stub_version(db, case):
            return 100

        async def _stub_score(*a, **k):
            return True

        monkeypatch.setattr(scorer, "_ensure_salvage_version", _stub_version)
        monkeypatch.setattr(scorer, "_score_executed_results", _stub_score)

        async def _fake_load(db, run):
            # 子集 run 只加载子集内 case（对账据此只补子集内缺失）
            return [SimpleNamespace(id=1), SimpleNamespace(id=2)]

        monkeypatch.setattr("app.runner.case_loader._load_run_cases", _fake_load)

        await scorer.score_run_salvage(1)

        # 对账只回填子集内缺失的 case2，绝不补子集外的 case3
        assert [o.case_id for o in fake_db.added] == [2]
        assert len(fake_db.added) == 1
