"""批 C1：error_regression 判定主干（详设 §8.1 / §8.2 / §8.6 / §7.4）。

宿主可跑（不触真实 DB/网络；SessionLocal 与 _load_run_cases 打桩）。覆盖：
- `_load_run_cases` error 分支：case_type 非空谓词 + 普通分支 case_type IS NULL 互斥
  + 断言形态（keyword_not_contains）不符者剔除
- `_error_verdict`：全通过 → pass / 任一不通过 → fail / 无断言 → fail（不制造假绿）
- `_error_precheck_failures`：D8 三条
- `_execute_with_retry(verdict_fn=...)`：终值直传 + 技术失败落 na（不是 error）
- `_finish_error_regression` 四态：completed / partial_failed / cancelled（取消）/ cancelled（空集）
  + `error_case` 恒 0 + `agent_score` 恒 NULL

⚠️ 本文件**不覆盖**：真执行路径（见 tests/integration 的 error_run_probe）、并发槽池（§8.3 的
真库证据在 shared_pool_probe）、熔断域隔离的**行为**（§8.4 的真库证据在 circuit_domain_probe；
此处只有接线形状）。
"""
from types import SimpleNamespace

import pytest

from app.core import circuit_repo
from app.runner import orchestrator as orch_mod
from app.runner.case_loader import _is_error_case, _load_run_cases
from app.runner.executor import CaseOutcome
from app.runner.orchestrator import (
    ERROR_CASE_TIMEOUT_S,
    ERROR_CIRCUIT_DOMAIN,
    ERROR_SKIP_REASON,
    CANCELLED,
    COMPLETED,
    PARTIAL_FAILED,
    RunOrchestrator,
    _decide_schedule,
    _error_precheck_failures,
    _error_verdict,
)


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _CaptureDB:
    """捕获 stmt 并返回预置行（断言 SQL 条件 + Python 侧形态过滤）。"""

    def __init__(self, rows=None):
        self.stmt = None
        self._rows = rows or []

    async def execute(self, stmt):
        self.stmt = stmt
        return _ScalarResult(self._rows)


def _case(cid=1, assertions=None, case_type="regression_error"):
    return SimpleNamespace(id=cid, assertions=assertions, case_type=case_type)


_OK_ASSERTION = {"op": "keyword_not_contains", "args": {"path": "answer", "keywords": ["x"]}}


# ---------------- §8.1 加载分支 ----------------
class TestLoadRunCasesErrorBranch:
    @pytest.mark.asyncio
    async def test_error_branch_uses_case_type_not_null(self):
        db = _CaptureDB()
        await _load_run_cases(db, SimpleNamespace(suite_id=1, trigger_type="error_regression",
                                                  case_ids=None))
        sql = str(db.stmt)
        assert "test_case.case_type IS NOT NULL" in sql

    @pytest.mark.asyncio
    async def test_error_branch_does_not_filter_held_out_by_trigger(self):
        # error 侧恒 is_held_out=False 字面量，**不是** is_held_out == (trigger=='held_out')
        # （后者对 error run 恒 False，恰好等价，但语义来源不同——此处钉死写法防回归）
        db = _CaptureDB()
        await _load_run_cases(db, SimpleNamespace(suite_id=1, trigger_type="error_regression",
                                                  case_ids=None))
        assert "test_case.is_held_out IS false" in str(db.stmt)

    @pytest.mark.asyncio
    async def test_normal_branch_excludes_error_cases(self):
        """§8.1：普通 run 不捞回流 case，否则 suite 内 error case 会被当普通 case 跑。"""
        db = _CaptureDB()
        await _load_run_cases(db, SimpleNamespace(suite_id=1, trigger_type="manual",
                                                  case_ids=None))
        assert "test_case.case_type IS NULL" in str(db.stmt)

    @pytest.mark.asyncio
    async def test_error_branch_drops_non_conforming_assertions(self):
        """形态不符（非 keyword_not_contains）的 case 在 Python 侧被剔除。"""
        good = _case(1, [_OK_ASSERTION])
        bad_op = _case(2, [{"op": "keyword_contains", "args": {}}])
        no_assert = _case(3, None)
        db = _CaptureDB([good, bad_op, no_assert])
        got = await _load_run_cases(db, SimpleNamespace(suite_id=1,
                                                        trigger_type="error_regression",
                                                        case_ids=None))
        assert [c.id for c in got] == [1]

    @pytest.mark.asyncio
    async def test_case_ids_subset_still_applies(self):
        db = _CaptureDB()
        await _load_run_cases(db, SimpleNamespace(suite_id=1, trigger_type="error_regression",
                                                  case_ids=[7]))
        assert "test_case.id IN" in str(db.stmt)


class TestIsErrorCase:
    def test_single_keyword_not_contains_passes(self):
        assert _is_error_case(_case(1, [_OK_ASSERTION]))

    def test_all_must_be_keyword_not_contains(self):
        assert not _is_error_case(_case(1, [_OK_ASSERTION, {"op": "other"}]))

    def test_empty_or_none_fails(self):
        assert not _is_error_case(_case(1, []))
        assert not _is_error_case(_case(1, None))


# ---------------- §8.2 判定终值 ----------------
class TestErrorVerdict:
    def _outcome(self, answer):
        return CaseOutcome(unified={"answer": answer})

    def test_no_keyword_hit_is_pass(self):
        assert _error_verdict(self._outcome("这是一段正常回答"), _case(1, [_OK_ASSERTION])) == "pass"

    def test_keyword_hit_is_fail(self):
        assert _error_verdict(self._outcome("抱歉我无法回答 x"), _case(1, [_OK_ASSERTION])) == "fail"

    def test_empty_assertions_is_fail_not_pass(self):
        """无判据不算过——防「无断言 ⇒ 真空通过」。"""
        assert _error_verdict(self._outcome("任意"), _case(1, [])) == "fail"


class TestErrorPrecheck:
    def _run(self, pinned=True, case_ids=(1,)):
        return SimpleNamespace(pinned=pinned, case_ids=list(case_ids), suite_id=5)

    def test_all_ok(self):
        assert _error_precheck_failures(self._run(), True) == []

    def test_not_pinned(self):
        assert "pinned" in _error_precheck_failures(self._run(pinned=False), True)[0]

    def test_empty_case_ids(self):
        assert "case_ids" in _error_precheck_failures(self._run(case_ids=[]), True)[0]

    def test_not_error_suite(self):
        rs = _error_precheck_failures(self._run(), False)
        assert len(rs) == 1 and "error suite" in rs[0]


# ---------------- _execute_with_retry 的 verdict_fn 通路 ----------------
class _Agent:
    id = 1
    adapter_config = {}
    base_url = "http://mock.local"


class _RetryCase:
    id = 1
    interface_id = 1
    metrics = None  # 无性能维度 → attempts=1，不重复采样
    assertions = [_OK_ASSERTION]


def _make_interface():
    from app.models import AgentInterface
    return AgentInterface(agent_id=1, name="it-c1", path="/v1/chat", method="POST",
                          contract_type="sse", contract_version="1.0", enabled=True)


class TestExecuteWithRetryErrorMode:
    async def _run(self, monkeypatch, outcome):
        saved: list[dict] = []

        async def fake_execute(adapter, client, case, timeout_s):
            return outcome

        async def fake_get_interface(interface_id):
            return _make_interface()

        async def fake_save(*args, **kwargs):
            saved.append(kwargs)

        async def fake_ensure(case):
            return 1

        monkeypatch.setattr(orch_mod, "execute_case", fake_execute)
        orch = RunOrchestrator()
        monkeypatch.setattr(orch, "_get_interface", fake_get_interface)
        monkeypatch.setattr(orch, "_save_result", fake_save)
        monkeypatch.setattr(orch, "_ensure_case_version", fake_ensure)
        monkeypatch.setattr(orch, "_is_cancelled", lambda run_id: False)
        out = await orch._execute_with_retry(
            1, None, _Agent(), _RetryCase(), {}, None, 30, 1, 0,
            verdict_fn=lambda o: _error_verdict(o, _RetryCase()))
        return out, saved

    @pytest.mark.asyncio
    async def test_success_writes_final_verdict(self, monkeypatch):
        out, saved = await self._run(monkeypatch, CaseOutcome(unified={"answer": "干净回答"}))
        assert out.ok
        assert saved[-1]["final_pass_fail"] == "pass"

    @pytest.mark.asyncio
    async def test_success_with_keyword_writes_fail(self, monkeypatch):
        out, saved = await self._run(monkeypatch, CaseOutcome(unified={"answer": "抱歉 x"}))
        assert saved[-1]["final_pass_fail"] == "fail"

    @pytest.mark.asyncio
    async def test_technical_failure_writes_na_not_error(self, monkeypatch):
        """§8.2 核心分野：error 侧技术失败落 na，共享链才落 error。"""
        out, saved = await self._run(
            monkeypatch, CaseOutcome(error_type="timeout", error_detail="inject"))
        assert not out.ok
        assert saved[-1]["final_pass_fail"] == "na"

    @pytest.mark.asyncio
    async def test_verdict_fn_absent_keeps_shared_semantics(self, monkeypatch):
        """不传 verdict_fn = 普通路径：不写终值（仍由 scorer 修正）。"""
        saved: list[dict] = []

        async def fake_execute(adapter, client, case, timeout_s):
            return CaseOutcome(unified={"answer": "x"})

        async def fake_save(*args, **kwargs):
            saved.append(kwargs)

        monkeypatch.setattr(orch_mod, "execute_case", fake_execute)
        orch = RunOrchestrator()
        monkeypatch.setattr(orch, "_get_interface", lambda i: _ret(_make_interface()))
        monkeypatch.setattr(orch, "_save_result", fake_save)
        monkeypatch.setattr(orch, "_ensure_case_version", lambda c: _ret(1))
        await orch._execute_with_retry(1, None, _Agent(), _RetryCase(), {}, None, 30, 1, 0)
        assert saved[-1].get("final_pass_fail") is None


async def _ret(v):
    return v


# ---------------- §8.6 收尾四态 ----------------
class _FakeSessionCtx:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *exc):
        return False


class _FinishDB:
    def __init__(self, run, results):
        self._run = run
        self._results = results
        self.added = []
        self.committed = False

    async def get(self, model, pk, **kw):
        return self._run

    async def execute(self, stmt):
        return _ScalarResult(self._results)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True


def _result(pass_fail):
    return SimpleNamespace(case_id=1, pass_fail=pass_fail)


def _run_obj(status="running", total_case=0):
    return SimpleNamespace(
        status=status, total_case=total_case, pass_case=0, fail_case=0,
        error_case=99, na_case=0, agent_score=7.5, finished_at=None, run_config={})


class TestFinishErrorRegression:
    async def _finish(self, monkeypatch, run, results, cancelled=False):
        db = _FinishDB(run, results)
        monkeypatch.setattr(orch_mod, "SessionLocal", lambda: _FakeSessionCtx(db))

        async def fake_load(db_, run_):
            return []  # 无缺失 → 不触发对账回填

        monkeypatch.setattr(orch_mod, "_load_run_cases", fake_load)
        orch = RunOrchestrator()
        monkeypatch.setattr(orch, "_is_cancelled", lambda run_id: cancelled)
        run.total_case = len(results)
        await orch._finish_error_regression(1)
        return run, db

    @pytest.mark.asyncio
    async def test_all_pass_fail_is_completed(self, monkeypatch):
        run, db = await self._finish(monkeypatch, _run_obj(),
                                     [_result("pass"), _result("fail")])
        assert run.status == COMPLETED
        assert (run.pass_case, run.fail_case, run.na_case) == (1, 1, 0)
        assert db.committed

    @pytest.mark.asyncio
    async def test_any_na_is_partial_failed(self, monkeypatch):
        """§8.6：≥1 na 落 partial_failed——共享 `_finish` 在此会落 completed（不可复用之处）。"""
        run, _ = await self._finish(monkeypatch, _run_obj(), [_result("pass"), _result("na")])
        assert run.status == PARTIAL_FAILED
        assert run.na_case == 1

    @pytest.mark.asyncio
    async def test_cancelled_beats_na(self, monkeypatch):
        run, _ = await self._finish(monkeypatch, _run_obj(), [_result("na")], cancelled=True)
        assert run.status == CANCELLED

    @pytest.mark.asyncio
    async def test_empty_result_set_is_cancelled(self, monkeypatch):
        """§8.6：实跑集为空 → cancelled（不是 completed——没有任何簇被判过）。"""
        run, _ = await self._finish(monkeypatch, _run_obj(), [])
        assert run.status == CANCELLED

    @pytest.mark.asyncio
    async def test_error_case_always_zero_and_score_null(self, monkeypatch):
        """§8.6：error_case 不按 'error' 计（技术失败计 na）；error run 不参与评分。"""
        run, _ = await self._finish(monkeypatch, _run_obj(), [_result("na")])
        assert run.error_case == 0
        assert run.agent_score is None

    @pytest.mark.asyncio
    async def test_external_terminal_not_overwritten(self, monkeypatch):
        """scanner 已标 timeout / API 已标 cancelled → 只对账统计，不覆盖终态。"""
        run, _ = await self._finish(monkeypatch, _run_obj(status="timeout"), [_result("pass")])
        assert run.status == "timeout"

    @pytest.mark.asyncio
    async def test_backfill_missing_case_as_na_scheduler_unexecuted(self, monkeypatch):
        """R-22 对账：未执行 case 回填 na + scheduler_unexecuted（不是 error/cancelled）。"""
        run = _run_obj()
        db = _FinishDB(run, [])
        monkeypatch.setattr(orch_mod, "SessionLocal", lambda: _FakeSessionCtx(db))

        missing = SimpleNamespace(id=42)

        async def fake_load(db_, run_):
            return [missing]

        monkeypatch.setattr(orch_mod, "_load_run_cases", fake_load)
        orch = RunOrchestrator()
        monkeypatch.setattr(orch, "_is_cancelled", lambda run_id: False)
        monkeypatch.setattr(orch, "_ensure_case_version", lambda c: _ret(9))
        run.total_case = 1
        await orch._finish_error_regression(1)
        assert len(db.added) == 1
        row = db.added[0]
        assert row.pass_fail == "na"
        assert row.error_type == "scheduler_unexecuted"
        assert row.case_id == 42


class TestRunErrorWiring:
    """`_run_error` 全链接线（无 DB）。

    **这条守卫的来历**（批 C4a 真库探针抓出）：改 §8.3 共享池时误删了 `_run_error` 里的
    `timeout_s = run_config.get(...)`，执行到构造任务列表处抛 `NameError` ⇒ error run 一律
    落 partial_failed，而当时**全量 916 条单测全绿**——没有一条走过 `_run_error` 全链。
    单测覆盖不到的分支，只有真跑才现形；故此处在无 DB 下把该函数走一遍。
    """

    class _CfgDB:
        async def get(self, model, pk, **kw):
            from app.models import TestSuite
            return SimpleNamespace(is_error_suite=True) if model is TestSuite else None

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    async def _run(self, monkeypatch, run_config, *, cancelled=False, precheck_ok=True) -> dict:
        """走 `_run_error` 一遍，捕获三件事：`args`(已构造执行任务) / `finished`(已调收尾)
        / `skipped`(已调 `_mark_error_skipped`)。

        `cancelled` **置真标志**驱动真 `_is_cancelled`（而非把它替换成 lambda True）——
        后者会把「标志读写」这一步也桩掉，`_cancel` 字典的清理语义就无处可验。
        `precheck_ok=False` 走 `pinned=False` 触发 D8 三条里的第一条。
        """
        calls: dict = {}
        monkeypatch.setattr(orch_mod, "SessionLocal",
                            lambda: _FakeSessionCtx(self._CfgDB()))
        monkeypatch.setattr(orch_mod, "build_agent_client", lambda **kw: self._FakeClient())
        orch = RunOrchestrator()
        monkeypatch.setattr(orch, "_decrypt_auth", lambda a: None)
        monkeypatch.setattr(orch, "_heartbeat", lambda *a: _ret(None))

        async def fake_one(*args):
            calls["args"] = args

        async def fake_finish(rid):
            calls["finished"] = rid

        async def fake_skipped(rid, reason):
            calls["skipped"] = (rid, reason)

        monkeypatch.setattr(orch, "_run_one_error", fake_one)
        monkeypatch.setattr(orch, "_finish_error_regression", fake_finish)
        monkeypatch.setattr(orch, "_mark_error_skipped", fake_skipped)
        run = SimpleNamespace(suite_id=1, pinned=precheck_ok, case_ids=[1],
                              run_config=run_config)
        if cancelled:
            orch._cancel[7] = True
        await orch._run_error(7, run, _Agent(), [SimpleNamespace(id=11)], run_config)
        return calls

    @pytest.mark.asyncio
    async def test_wires_case_timeout_from_run_config(self, monkeypatch):
        calls = await self._run(monkeypatch, {"case_timeout": 123, "max_retries": 2})
        assert calls["args"][6] == 123                      # timeout_s 位（第 7 个位置参数）
        assert calls["args"][3].id == 11                    # case 透传
        assert calls["finished"] == 7

    @pytest.mark.asyncio
    async def test_defaults_timeout_when_absent(self, monkeypatch):
        calls = await self._run(monkeypatch, {})
        assert calls["args"][6] == ERROR_CASE_TIMEOUT_S

    # ---- 两条早退（此前**只测了 happy path**，早退路径零驱动）----

    @pytest.mark.asyncio
    async def test_cancelled_at_takeover_skips_execution_and_finish(self, monkeypatch):
        """接管时已取消 → **不执行、不调收尾**，run 交 scanner 兜底回收。

        这不是缺陷而是契约：:384 的 return 发生在心跳任务建立（:399）与 `try` 之前，
        故此处**没有任何需要清理的残留**；run 停在 pending/running、由 scanner 的租约/
        硬超时回收走真收尾（该侧行为见 `test_integration_scanner_error_run.py` 用例①）。
        本用例钉住这个契约 —— 若日后有人在 return 前补上收尾，此处即红（重复收尾会二次推 link）。
        """
        calls = await self._run(monkeypatch, {}, cancelled=True)
        assert "args" not in calls, "接管即取消，却仍构造了执行任务"
        assert "finished" not in calls, "接管即取消，却仍调了收尾（应留给 scanner 回收路径）"
        assert "skipped" not in calls, "取消不是前置校验失败，不得走 _mark_error_skipped"

    @pytest.mark.asyncio
    async def test_precheck_failure_marks_skipped_without_finish(self, monkeypatch):
        """D8 前置校验不过 → `_mark_error_skipped`（**不是**收尾），且不构造执行任务。

        两条早退走的是**不同**分支：取消是「静默交还」，前置不过要落 partial_failed +
        fail_reason 供 online 读。混用会给出错误结论（取消也被报成 precheck_failed）。
        """
        calls = await self._run(monkeypatch, {}, precheck_ok=False)
        assert "args" not in calls, "前置校验不过，却仍构造了执行任务"
        assert "finished" not in calls, "前置校验不过，却仍调了收尾（应收敛在 _mark_error_skipped）"
        assert calls["skipped"][0] == 7
        assert "pinned" in calls["skipped"][1], "未把 D8 的具体原因透出"


class TestMarkErrorSkipped:
    """`_mark_error_skipped` 的**效果**（无 DB）。

    `_error_precheck_failures`（判据）早已有测试，但判据触发的**落库效果**零覆盖 ——
    判据说「不过」，效果决定 online 看到什么。二者任一改坏都得红。
    """

    async def _skip(self, monkeypatch, run, reason="pinned=False"):
        db = _FinishDB(run, [])
        monkeypatch.setattr(orch_mod, "SessionLocal", lambda: _FakeSessionCtx(db))
        await RunOrchestrator()._mark_error_skipped(7, reason)
        return db

    @pytest.mark.asyncio
    async def test_partial_failed_with_reason_and_zero_total(self, monkeypatch):
        run = _run_obj(status="running", total_case=3)
        run.run_config = {"case_timeout": 120}
        db = await self._skip(monkeypatch, run, reason="pinned=False")

        assert run.status == PARTIAL_FAILED          # 不是 completed、更不是 cancelled
        assert run.total_case == 0                   # ★ 清算：一条都没跑，分母必须归零
        assert run.finished_at is not None
        assert run.run_config["fail_reason"] == ERROR_SKIP_REASON
        assert run.run_config["error_precheck"] == "pinned=False"   # 具体原因可读
        assert run.run_config["case_timeout"] == 120  # 原键不被抹掉
        assert db.committed

    @pytest.mark.asyncio
    async def test_does_not_overwrite_terminal_run(self, monkeypatch):
        """已终态（如 scanner 已标 timeout）→ **原样返回**，不覆盖、不提交。

        `:470` 的状态前置判断与 `_finish_error_regression` 的 `external_terminal` 同源约定：
        外部先置的终态是权威，后来者只许补统计、不许改判。
        """
        run = _run_obj(status="timeout", total_case=3)
        db = await self._skip(monkeypatch, run)

        assert run.status == "timeout"
        assert run.total_case == 3                   # 未被清零
        assert run.run_config == {}                  # 未写入 fail_reason
        assert not db.committed, "已终态仍提交了事务"


class TestErrorCircuitDomainWiring:
    """§8.4 熔断域接线守卫（无 DB）：`_run_one_error` 必须按 **error 域**读写熔断行。

    **这条守卫的来历**（批 C4b）：域隔离全靠调用点传 `domain=`——漏传时单测全绿、全量回归
    也全绿（fallback 是 `"manual"`，不报错），只有真库的域隔离探针才翻得出来。故在此把
    调用点钉死：换域/漏域必须让本组红。
    """

    class _FakeBreaker:
        def __init__(self) -> None:
            self.released = 0

        def try_acquire(self) -> bool:
            return True

        def record_failure(self) -> None:
            pass

        def release_probe(self) -> None:
            self.released += 1

    class _FakeLimiter:
        async def acquire(self, run_id, agent_key):  # noqa: D102
            pass

        def release(self, run_id, agent_key):  # noqa: D102
            pass

    @pytest.mark.asyncio
    async def test_error_run_reads_and_writes_error_domain(self, monkeypatch):
        calls: list = []
        breaker = self._FakeBreaker()

        async def fake_load(db, agent_id, br=None, domain="manual"):
            calls.append(("load", domain))
            return breaker

        async def fake_save(db, agent_id, br, domain="manual"):
            calls.append(("save", domain))

        monkeypatch.setattr(circuit_repo, "load", fake_load)
        monkeypatch.setattr(circuit_repo, "save", fake_save)
        monkeypatch.setattr(orch_mod, "SessionLocal", lambda: _FakeSessionCtx(None))
        orch = RunOrchestrator()
        monkeypatch.setattr(orch, "_limiter", self._FakeLimiter())

        async def fake_exec(*a, **kw):
            return None          # 未执行 → 不改熔断计数，本组只看域的传递

        monkeypatch.setattr(orch, "_execute_with_retry", fake_exec)
        await orch._run_one_error(7, SimpleNamespace(), _Agent(), SimpleNamespace(id=11),
                                  None, None, 30, 0, 2, 30)
        assert calls == [("load", ERROR_CIRCUIT_DOMAIN), ("save", ERROR_CIRCUIT_DOMAIN)]
        assert breaker.released == 1     # 探针名额必须归还（半开态单探针约束）


def test_error_run_config_defaults_are_error_specific():
    """§7.4：error-run 专用 run_config 与常规评测不同（复现更长、重试更多）。"""
    assert ERROR_CASE_TIMEOUT_S == 600


# ---------------- 批 C3：§5.2 skip 表（纯函数；DB 面由 auto_schedule_probe 覆盖） ----------

SIGNAL = 4242
OTHER_SIGNAL = 4243


def _decide(status, latest_signal=OTHER_SIGNAL):
    return _decide_schedule(latest_status=status, latest_signal_id=latest_signal,
                            signal_run_id=SIGNAL)


def test_decide_builds_when_latest_is_none():
    """首次触发（该 (agent, version) 无 error run）→ 建。"""
    assert _decide_schedule(latest_status=None, latest_signal_id=None,
                            signal_run_id=SIGNAL) is True


@pytest.mark.parametrize("status", ["pending", "running", "scoring"])
def test_decide_skips_when_not_terminal(status):
    """已建未跑完 / 非终态（scoring 也是未完成的评分态）→ 不建。"""
    assert _decide(status) is False


@pytest.mark.parametrize("status", ["completed", "partial_failed"])
def test_decide_skips_when_judgeable_terminal(status):
    """已有可判终态 → 不重复回归（技术 na 重跑 = 二期开放项，§5.2）。"""
    assert _decide(status) is False


@pytest.mark.parametrize("status", ["timeout", "cancelled"])
def test_decide_rebuilds_on_bad_terminal_with_new_signal(status):
    """坏终态 + **新信号** → 给一次重建机会（§5.2）。"""
    assert _decide(status) is True


def test_decide_skips_scoring_failed():
    """`scoring_failed` **不在重建集**：§5.2 只列 timeout/cancelled，且 error run 状态机不含
    scoring 态（`phase2.md:200`：error run 从不触发 score_run ⇒ scanner ③ 没有可标对象）
    ⇒ 并进重建集是不可达分支。按表外状态处置（不建 + WARNING）。"""
    assert _decide("scoring_failed") is False


@pytest.mark.parametrize("status", ["completed", "partial_failed", "timeout", "cancelled",
                                    "scoring_failed"])
def test_decide_consumed_anchor_absorbs_same_signal(status):
    """吸收态优先于终态规则：同一信号**无论 latest 处于何种终态**都只动作一次（§5.2 v0.4）。
    这条是「补偿对账复用同一锚反复调用 → 无吸收态则自动重跑循环」的直接守卫。"""
    assert _decide(status, latest_signal=SIGNAL) is False


def test_decide_skips_unknown_status():
    """表外状态（新状态被引入而此处未同步）→ 保守不建，且不静默（记 WARNING）。"""
    assert _decide("interrupted") is False
