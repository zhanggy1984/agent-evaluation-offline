"""优化 #12：run 进度可观测（list_runs/get_run 进度字段 + run_results 单 case stage）。

mock DB 跑 runs.py 的 list_runs/get_run/run_results，验证：
- _estimate_remaining_sec：running 线性外推 / scoring judge 队列估算 / 不可估返回 None
- _result_stage：error / judging / judge_failed / completed 推断
- list_runs：活跃 run 带 done_case/judge_queue/estimate_remaining_sec，终态 run 三个字段 None
- get_run：单 run 进度字段
- run_results：每行 stage（judge_task 状态推断）
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.api.runs import (
    _case_stage, _estimate_remaining_sec, _result_stage, get_run, list_runs, run_results,
)

_NOW = datetime(2026, 8, 31, 12, 0, 0)


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
    """list_runs/get_run/run_results 的查询分派（按 stmt 内容分发聚合/明细）。"""

    def __init__(self, run=None, runs=None, done_counts=None, jt_counts=None,
                 jt_cases=None, rows=None, cases=None, ifaces=None,
                 done_total=0, judge_total=0):
        self._run = run
        self._runs = runs or []
        self._done_counts = done_counts or []     # [(run_id, done)] list_runs 聚合
        self._jt_counts = jt_counts or []         # [(run_id, judge_queue)] list_runs 聚合
        self._jt_cases = jt_cases or []           # [(case_id, judge_status)] run_results
        self._rows = rows or []                   # run_results 列子集行
        self._cases, self._ifaces = cases or [], ifaces or []
        self._done_total = done_total             # get_run count 标量
        self._judge_total = judge_total

    async def get(self, model, pk, with_for_update=False):
        name = model.__name__
        if name == "Agent":
            return SimpleNamespace(id=1, owner_id=999)  # 非当前 user → held-out 不隐藏
        if name == "EvalRun":
            return self._run
        return None

    async def execute(self, stmt):
        s = str(stmt).lower()
        # judge_task：count()（get_run 判分队列）与 group_by（list_runs 聚合）→ _jt_counts；
        # 明细查询（run_results 的 case→status）→ _jt_cases
        if "judge_task" in s:
            if "group by" in s:      # list_runs 聚合 → [(run_id, n)]
                return _ScalarResult(self._jt_counts)
            if "count(" in s:        # get_run scalar → (n,)
                return _ScalarResult([(self._judge_total,)])
            return _ScalarResult(self._jt_cases)   # run_results case→status
        # eval_result：GROUP BY 聚合（list_runs）→ [(run_id, n)]；count scalar（get_run）→ (n,)；
        # 列子集明细（run_results）→ _rows。注意 SQL 渲染是 "group by"（带空格）
        if "eval_result" in s:
            if "group by" in s:
                return _ScalarResult(self._done_counts)
            if "count(" in s:
                return _ScalarResult([(self._done_total,)])
            return _ScalarResult(self._rows)
        # 注意：select(EvalRun) 渲染含 agent_id 列，必须先判 eval_run 再判 agent，
        # 否则主查询被误分到 owner_map 分支（返回无 agent_id 的对象）。
        if "eval_run" in s:
            return _ScalarResult(self._runs)
        if "test_case" in s:
            return _ScalarResult(self._cases)
        if "agent_interface" in s:
            return _ScalarResult(self._ifaces)
        if "agent" in s:
            # owner_map 用 a.id / a.owner_id 属性访问（list_runs:248）→ 返回对象而非 tuple
            return _ScalarResult([SimpleNamespace(id=1, owner_id=999)])
        return _ScalarResult([])


def _user(role="admin"):
    return SimpleNamespace(id=998, role=role, username="u1")


def _run(rid, status, total=10, started=None, run_config=None, env_snapshot=None):
    return SimpleNamespace(
        id=rid, agent_id=1, suite_id=1, version="1.0", trigger_type="manual",
        status=status, generation=1,
        started_at=started if started is not None else _NOW - timedelta(seconds=200),
        finished_at=None, total_case=total, pass_case=None, fail_case=None,
        error_case=None, na_case=None, agent_score=None, judge_incomplete=None,
        ttft_p50=None, e2e_p50=None, case_ids=None, run_config=run_config,
        env_snapshot=env_snapshot)


def _row(cid, pass_fail="pass", score=80.0, usage=None):
    return SimpleNamespace(
        id=cid, case_id=cid, pass_fail=pass_fail, score_total=score,
        score_per_dimension=[], error_type=None, error_detail=None,
        answer_preview="answer", assertion_results=[], judge_results=[], usage=usage)


# ---- _estimate_remaining_sec ----

class TestEstimateRemaining:
    def test_running_linear_extrapolation(self):
        run = _run(1, "running", total=10, started=_NOW - timedelta(seconds=400))
        assert _estimate_remaining_sec(run, done=4, judge=0, now=_NOW) == 600  # 400×6/4

    def test_running_done_zero_none(self):
        run = _run(1, "running", total=10, started=_NOW - timedelta(seconds=400))
        assert _estimate_remaining_sec(run, done=0, judge=0, now=_NOW) is None

    def test_running_zero_total_none(self):
        run = _run(1, "running", total=0, started=_NOW - timedelta(seconds=400))
        assert _estimate_remaining_sec(run, done=0, judge=0, now=_NOW) is None

    def test_scoring_judge_queue_estimate(self):
        run = _run(1, "scoring", total=10,
                   run_config={"judge_call_timeout": 60, "judge_repeat": 3, "judge_concurrency": 2})
        assert _estimate_remaining_sec(run, done=10, judge=2, now=_NOW) == 180  # 2×60×3/2

    def test_scoring_no_queue_none(self):
        run = _run(1, "scoring", total=10)
        assert _estimate_remaining_sec(run, done=10, judge=0, now=_NOW) is None

    def test_scoring_defaults_fallback(self):
        run = _run(1, "scoring", total=10, run_config=None)  # 无快照 → 保守默认
        # judge=3 × call=120 × repeat=3 / conc=1 = 1080
        assert _estimate_remaining_sec(run, done=10, judge=3, now=_NOW) == 1080

    def test_pending_none(self):
        run = _run(1, "pending", total=10)
        assert _estimate_remaining_sec(run, done=0, judge=0, now=_NOW) is None

    def test_terminal_none(self):
        run = _run(1, "completed", total=10)
        assert _estimate_remaining_sec(run, done=10, judge=0, now=_NOW) is None


# ---- _result_stage ----

class TestResultStage:
    def test_error_wins(self):
        assert _result_stage("error", "pending") == "error"

    def test_judging_pending(self):
        assert _result_stage("pass", "pending") == "judging"

    def test_judging_processing(self):
        assert _result_stage("fail", "processing") == "judging"

    def test_judge_failed(self):
        assert _result_stage("pass", "failed") == "judge_failed"

    def test_completed_done(self):
        assert _result_stage("pass", "done") == "completed"

    def test_completed_no_task(self):
        assert _result_stage("na", None) == "completed"


# ---- _case_stage：多维度聚合（一个 case 多 JudgeTask，last-write-wins 会丢维度） ----

class TestCaseStage:
    def test_error_wins(self):
        assert _case_stage("error", ["processing", "done"]) == "error"

    def test_any_processing_judging(self):
        # 一维度 done 另一维度 processing → 整体仍在判分（防误标 completed）
        assert _case_stage("pass", ["done", "processing"]) == "judging"

    def test_any_pending_judging(self):
        assert _case_stage("pass", ["done", "pending"]) == "judging"

    def test_any_failed_judge_failed(self):
        assert _case_stage("pass", ["failed", "done"]) == "judge_failed"

    def test_all_done_completed(self):
        assert _case_stage("pass", ["done", "done"]) == "completed"

    def test_no_semantic_dim_completed(self):
        assert _case_stage("pass", None) == "completed"
        assert _case_stage("pass", []) == "completed"


# ---- list_runs ----

@pytest.mark.asyncio
class TestListRunsProgress:
    async def test_active_run_gets_progress_terminal_null(self):
        # running 外推依赖 utcnow：started 用相对真实时间（elapsed≈200s），断言近似 200
        started = datetime.utcnow() - timedelta(seconds=200)
        runs = [_run(1, "running", total=10, started=started),
                _run(2, "completed", total=10)]
        db = _FakeDB(runs=runs, done_counts=[(1, 5)], jt_counts=[(1, 2)])
        out = (await list_runs(limit=50, offset=0, user=_user(), db=db))["data"]

        r1 = next(x for x in out if x["id"] == 1)
        assert r1["done_case"] == 5
        assert r1["judge_queue"] == 2
        assert r1["estimate_remaining_sec"] == pytest.approx(200, abs=5)  # ~200×5/5

        r2 = next(x for x in out if x["id"] == 2)
        assert r2["done_case"] is None
        assert r2["judge_queue"] is None
        assert r2["estimate_remaining_sec"] is None

    async def test_scoring_run_judge_queue(self):
        run = _run(1, "scoring", total=10,
                   run_config={"judge_call_timeout": 60, "judge_repeat": 3, "judge_concurrency": 1})
        db = _FakeDB(runs=[run], done_counts=[(1, 10)], jt_counts=[(1, 2)])
        out = (await list_runs(limit=50, offset=0, user=_user(), db=db))["data"]
        assert out[0]["judge_queue"] == 2
        assert out[0]["estimate_remaining_sec"] == 360  # 2×60×3/1


# ---- get_run ----

@pytest.mark.asyncio
class TestGetRunProgress:
    async def test_get_run_progress(self):
        run = _run(1, "scoring", total=10,
                   run_config={"judge_call_timeout": 60, "judge_repeat": 3, "judge_concurrency": 2})
        db = _FakeDB(run=run, done_total=10, judge_total=2)
        out = (await get_run(1, user=_user(), db=db))["data"]
        assert out["done_case"] == 10
        assert out["judge_queue"] == 2
        assert out["estimate_remaining_sec"] == 180  # 2×60×3/2

    async def test_get_run_terminal_no_progress(self):
        run = _run(1, "completed", total=10)
        db = _FakeDB(run=run)
        out = (await get_run(1, user=_user(), db=db))["data"]
        assert out["done_case"] is None
        assert out["judge_queue"] is None

    async def test_knowledge_version_exposed(self):
        # P2-9：env_snapshot 回填的知识版本透出到 run 概要
        run = _run(1, "completed", total=10,
                   env_snapshot={"contract_version": "1.0", "knowledge_version": "20260824005200"})
        db = _FakeDB(run=run)
        out = (await get_run(1, user=_user(), db=db))["data"]
        assert out["knowledge_version"] == "20260824005200"

    async def test_knowledge_version_missing(self):
        run = _run(1, "completed", total=10, env_snapshot={"contract_version": "1.0"})
        db = _FakeDB(run=run)
        out = (await get_run(1, user=_user(), db=db))["data"]
        assert out["knowledge_version"] is None


# ---- run_results stage ----

@pytest.mark.asyncio
class TestRunResultsStage:
    async def test_stage_judging(self):
        run = _run(1, "scoring", total=2)
        cases = [SimpleNamespace(id=101, name="c1", interface_id=1, input_type="text")]
        ifaces = [SimpleNamespace(id=1, name="chat")]
        db = _FakeDB(run=run, rows=[_row(101)], jt_cases=[(101, "processing")],
                     cases=cases, ifaces=ifaces)
        out = (await run_results(1, user=_user(), db=db))["data"]
        assert out[0]["stage"] == "judging"

    async def test_stage_completed(self):
        run = _run(1, "completed", total=2)
        cases = [SimpleNamespace(id=101, name="c1", interface_id=1, input_type="text")]
        ifaces = [SimpleNamespace(id=1, name="chat")]
        db = _FakeDB(run=run, rows=[_row(101)], jt_cases=[(101, "done")],
                     cases=cases, ifaces=ifaces)
        out = (await run_results(1, user=_user(), db=db))["data"]
        assert out[0]["stage"] == "completed"

    async def test_stage_multi_dim_any_processing_judging(self):
        # 多语义维度 case：factuality done + reasoning_quality processing → 整体 judging
        run = _run(1, "scoring", total=2)
        cases = [SimpleNamespace(id=101, name="c1", interface_id=1, input_type="text")]
        ifaces = [SimpleNamespace(id=1, name="chat")]
        db = _FakeDB(run=run, rows=[_row(101)],
                     jt_cases=[(101, "done"), (101, "processing")],
                     cases=cases, ifaces=ifaces)
        out = (await run_results(1, user=_user(), db=db))["data"]
        assert out[0]["stage"] == "judging"

    async def test_stage_multi_dim_all_done_completed(self):
        # 多维度全部 done → completed
        run = _run(1, "completed", total=2)
        cases = [SimpleNamespace(id=101, name="c1", interface_id=1, input_type="text")]
        ifaces = [SimpleNamespace(id=1, name="chat")]
        db = _FakeDB(run=run, rows=[_row(101)],
                     jt_cases=[(101, "done"), (101, "done")],
                     cases=cases, ifaces=ifaces)
        out = (await run_results(1, user=_user(), db=db))["data"]
        assert out[0]["stage"] == "completed"

    async def test_stage_error(self):
        run = _run(1, "running", total=2)
        cases = [SimpleNamespace(id=101, name="c1", interface_id=1, input_type="text")]
        ifaces = [SimpleNamespace(id=1, name="chat")]
        db = _FakeDB(run=run, rows=[_row(101, pass_fail="error")], jt_cases=[],
                     cases=cases, ifaces=ifaces)
        out = (await run_results(1, user=_user(), db=db))["data"]
        assert out[0]["stage"] == "error"

    # ---- P2-9 缓存命中标识：usage.cached（gq 应用层问答缓存，真实流式无该键） ----

    async def test_cache_hit_true(self):
        run = _run(1, "completed", total=1)
        cases = [SimpleNamespace(id=101, name="c1", interface_id=1, input_type="text")]
        ifaces = [SimpleNamespace(id=1, name="chat")]
        db = _FakeDB(run=run, rows=[_row(101, usage=[{"cached": True, "total_tokens": 100}])],
                     cases=cases, ifaces=ifaces)
        out = (await run_results(1, user=_user(), db=db))["data"]
        assert out[0]["cache_hit"] is True

    async def test_cache_hit_false(self):
        run = _run(1, "completed", total=1)
        cases = [SimpleNamespace(id=101, name="c1", interface_id=1, input_type="text")]
        ifaces = [SimpleNamespace(id=1, name="chat")]
        db = _FakeDB(run=run, rows=[_row(101, usage=[{"total_tokens": 100}])],
                     cases=cases, ifaces=ifaces)
        out = (await run_results(1, user=_user(), db=db))["data"]
        assert out[0]["cache_hit"] is False

    async def test_cache_hit_no_usage(self):
        run = _run(1, "completed", total=1)
        cases = [SimpleNamespace(id=101, name="c1", interface_id=1, input_type="text")]
        ifaces = [SimpleNamespace(id=1, name="chat")]
        db = _FakeDB(run=run, rows=[_row(101)],
                     cases=cases, ifaces=ifaces)
        out = (await run_results(1, user=_user(), db=db))["data"]
        assert out[0]["cache_hit"] is False
