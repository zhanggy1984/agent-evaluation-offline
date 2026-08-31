"""模块 B：run_results 内联（#5）+ 证据分级（D3）+ top 扣分原因聚合（#7）。

mock DB 跑 runs.py 的 run_results/run_failures/result_evidence（不触真实 DB）。
覆盖：
- run_results：answer 截断 500、失败断言过滤、judge_results 投影、列子集查询（answer_preview）
- 权限裁剪：viewer 见 basic（answer/断言）不见 judge_results（reason 证据级）；staff 全见
- run_failures top_reasons：按 dimension 聚合（count/avg_score/sample_reasons）、排序、viewer 空
- result_evidence：viewer 仅 basic（answer 截断，full 字段 None）；staff 全文
"""
from types import SimpleNamespace

import pytest

from app.api.runs import result_evidence, run_failures, run_results


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeDB:
    """三个详情接口的查询分派：get→run/result/agent；execute 按 stmt 内容分发。"""

    def __init__(self, run=None, rows=None, fail_rows=None, cases=None,
                 ifaces=None, targets=None, result=None):
        self._run = run
        self._rows = rows or []                    # run_results 列子集行（含 answer_preview）
        self._fail_rows = fail_rows                # run_failures fail 行（ORM 对象）
        self._cases, self._ifaces = cases or [], ifaces or []
        self._targets = targets or []
        self._result = result                      # result_evidence 用
        self.stmts = []

    async def get(self, model, pk, with_for_update=False):
        name = model.__name__
        if name == "Agent":
            return SimpleNamespace(id=1, owner_id=999)  # 非当前 user → held-out 不隐藏
        if name == "EvalRun":
            return self._run
        if name == "EvalResult":
            return self._result
        return None

    async def execute(self, stmt):
        self.stmts.append(str(stmt))
        s = str(stmt).lower()
        if "answer_preview" in s:   # run_results 列子集查询（DB 层截断 answer）
            return _ScalarResult(self._rows)
        if "test_case" in s:
            return _ScalarResult(self._cases)
        if "agent_interface" in s:
            return _ScalarResult(self._ifaces)
        if "baseline_target" in s:
            return _ScalarResult(self._targets)
        if "eval_result" in s:      # run_failures（fail 过滤）
            return _ScalarResult(self._fail_rows if self._fail_rows is not None else [])
        return _ScalarResult([])


def _user(role="admin"):
    return SimpleNamespace(id=998, role=role, username="u1")


def _run():
    return SimpleNamespace(id=1, agent_id=1, suite_id=1, trigger_type="manual")


def _case(cid, iface_id=1):
    return SimpleNamespace(id=cid, name=f"c{cid}", interface_id=iface_id, input_type="text")


def _iface():
    return SimpleNamespace(id=1, name="i1")


def _targets():
    # interface=1 上 factuality/reasoning_quality 目标 90（semantic 档位化：50//20=2 < 4 → 不达标）
    return [SimpleNamespace(interface_id=1, dimension_code="factuality", target_score=90.0),
            SimpleNamespace(interface_id=1, dimension_code="reasoning_quality", target_score=90.0)]


def _res_row(**kw):
    """run_results 列子集查询返回的 Row（含 answer_preview）。"""
    p = dict(id=1, case_id=10, pass_fail="fail", score_total=60.0,
             score_per_dimension=[{"code": "factuality", "value": 50.0, "na": False}],
             error_type=None, error_detail=None,
             answer_preview="x" * 600,
             assertion_results=[{"dimension": "c1", "pass": False, "actual": "a1"},
                                {"dimension": "c2", "pass": True, "actual": "a2"}],
             judge_results=[{"dimension": "factuality", "score": 50.0, "reason": "r1", "level": 3}])
    p.update(kw)
    return SimpleNamespace(**p)


def _fail_row(**kw):
    """run_failures 的 fail 用例（ORM 对象，score_per_dimension 带 na 标志）。"""
    p = dict(case_id=10, score_total=60.0,
             score_per_dimension=[{"code": "factuality", "value": 50.0, "na": False}],
             assertion_results=[{"dimension": "c1", "pass": False, "actual": "a1"}],
             judge_results=[{"dimension": "factuality", "score": 50.0, "reason": "r1"}])
    p.update(kw)
    return SimpleNamespace(**p)


# ---------------- run_results：内联 + 列子集 + 权限裁剪 ----------------
class TestRunResultsInline:
    @pytest.mark.asyncio
    async def test_inline_answer_assertions_judge(self):
        db = _FakeDB(run=_run(), rows=[_res_row()], cases=[_case(10)], ifaces=[_iface()])
        row = (await run_results(1, _user(), db))["data"][0]
        assert row["answer"] == "x" * 500 + "…"          # 截断 500 + 省略号（与断言 actual 同口径）
        assert [a["dimension"] for a in row["assertion_results"]] == ["c1"]  # 仅失败断言
        assert row["judge_results"] == [{"dimension": "factuality", "score": 50.0, "reason": "r1"}]

    @pytest.mark.asyncio
    async def test_column_subset_uses_answer_preview(self):
        # 列子集查询含 answer_preview（DB 层截断），避免整列 MEDIUMTEXT 出库
        db = _FakeDB(run=_run(), rows=[_res_row()], cases=[_case(10)], ifaces=[_iface()])
        await run_results(1, _user(), db)
        assert any("answer_preview" in s for s in db.stmts)

    @pytest.mark.asyncio
    async def test_viewer_basic_but_no_judge_reason(self):
        # D3 basic：viewer 可见 answer（截断）+ 断言明细；judge reason 证据级不返回
        db = _FakeDB(run=_run(), rows=[_res_row()], cases=[_case(10)], ifaces=[_iface()])
        row = (await run_results(1, _user(role="viewer"), db))["data"][0]
        assert row["answer"] == "x" * 500 + "…"
        assert len(row["assertion_results"]) == 1
        assert row["judge_results"] is None

    @pytest.mark.asyncio
    async def test_staff_sees_judge_reason(self):
        db = _FakeDB(run=_run(), rows=[_res_row()], cases=[_case(10)], ifaces=[_iface()])
        row = (await run_results(1, _user(), db))["data"][0]
        assert row["judge_results"][0]["reason"] == "r1"


# ---------------- run_failures：top 扣分原因聚合 + 权限 ----------------
class TestTopReasons:
    @pytest.mark.asyncio
    async def test_aggregate_by_dimension(self):
        rows = [
            _fail_row(case_id=10,
                      score_per_dimension=[{"code": "factuality", "value": 50.0, "na": False}],
                      judge_results=[{"dimension": "factuality", "score": 50.0, "reason": "r1"}]),
            _fail_row(case_id=11,
                      score_per_dimension=[{"code": "factuality", "value": 60.0, "na": False}],
                      judge_results=[{"dimension": "factuality", "score": 60.0, "reason": "r2"}]),
            _fail_row(case_id=12,
                      score_per_dimension=[{"code": "reasoning_quality", "value": 40.0, "na": False}],
                      judge_results=[{"dimension": "reasoning_quality", "score": 40.0, "reason": "r3"}]),
        ]
        db = _FakeDB(run=_run(), fail_rows=rows,
                     cases=[_case(10), _case(11), _case(12)], ifaces=[_iface()], targets=_targets())
        data = (await run_failures(1, _user(), db))["data"]
        assert len(data["items"]) == 3
        top = data["top_reasons"]
        assert len(top) == 2
        # 按 count 降序：factuality(count2) 在前
        assert top[0]["dimension"] == "factuality"
        assert top[0]["count"] == 2
        assert top[0]["avg_score"] == 55.0
        assert top[0]["sample_reasons"] == ["r1", "r2"]      # 去重保序
        assert top[1]["dimension"] == "reasoning_quality"
        assert top[1]["count"] == 1
        assert top[1]["sample_reasons"] == ["r3"]

    @pytest.mark.asyncio
    async def test_sample_reasons_dedup(self):
        # 同 dimension 多条相同 reason → sample_reasons 去重
        rows = [
            _fail_row(case_id=10,
                      judge_results=[{"dimension": "factuality", "score": 50.0, "reason": "r1"}]),
            _fail_row(case_id=11,
                      judge_results=[{"dimension": "factuality", "score": 60.0, "reason": "r1"}]),
            _fail_row(case_id=12,
                      judge_results=[{"dimension": "factuality", "score": 40.0, "reason": "r1"}]),
        ]
        db = _FakeDB(run=_run(), fail_rows=rows,
                     cases=[_case(10), _case(11), _case(12)], ifaces=[_iface()], targets=_targets())
        top = (await run_failures(1, _user(), db))["data"]["top_reasons"]
        assert top[0]["count"] == 3
        assert top[0]["sample_reasons"] == ["r1"]
        assert top[0]["avg_score"] == 50.0

    @pytest.mark.asyncio
    async def test_viewer_no_judge_no_top(self):
        # viewer：top_reasons 空、judge_failures 空；断言明细（basic）仍可见
        db = _FakeDB(run=_run(), fail_rows=[_fail_row()], cases=[_case(10)],
                     ifaces=[_iface()], targets=_targets())
        data = (await run_failures(1, _user(role="viewer"), db))["data"]
        assert data["top_reasons"] == []
        item = data["items"][0]
        assert item["judge_failures"] == []
        assert len(item["assertion_failures"]) == 1

    @pytest.mark.asyncio
    async def test_empty_fail_returns_struct(self):
        db = _FakeDB(run=_run(), fail_rows=[])
        assert (await run_failures(1, _user(), db))["data"] == {"items": [], "top_reasons": []}


# ---------------- result_evidence：basic/full 分级 ----------------
class TestResultEvidence:
    def _evi(self, **kw):
        p = dict(id=5, case_id=10, run_id=1, answer="A" * 600, reasoning="R" * 10,
                 tool_calls=[{"t": 1}], usage={"prompt_tokens": 1}, timing={"x": 1},
                 assertion_results=[{"dimension": "c1", "pass": False}],
                 judge_results=[{"dimension": "factuality", "reason": "r1"}],
                 error_type=None, error_detail=None)
        p.update(kw)
        return SimpleNamespace(**p)

    @pytest.mark.asyncio
    async def test_viewer_basic_only(self):
        # D3：viewer 可见 answer（截断）+ 断言；full 字段全部 None（不再 403，basic 放开）
        db = _FakeDB(run=_run(), result=self._evi())
        out = (await result_evidence(1, 5, _user(role="viewer"), db))["data"]
        assert out["answer"] == "A" * 500 + "…"
        assert out["reasoning"] is None
        assert out["tool_calls"] is None
        assert out["usage"] is None
        assert out["timing"] is None
        assert out["judge_results"] is None
        assert out["assertion_results"] == [{"dimension": "c1", "pass": False}]

    @pytest.mark.asyncio
    async def test_staff_full(self):
        db = _FakeDB(run=_run(), result=self._evi())
        out = (await result_evidence(1, 5, _user(), db))["data"]
        assert out["answer"] == "A" * 600          # staff 全文
        assert out["reasoning"] == "R" * 10
        assert out["tool_calls"] == [{"t": 1}]
        assert out["usage"] == {"prompt_tokens": 1}
        assert out["judge_results"] == [{"dimension": "factuality", "reason": "r1"}]
