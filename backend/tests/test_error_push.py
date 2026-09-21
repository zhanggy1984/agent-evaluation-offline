"""批 C2：出站结果推送（§10.1 载荷 / 分片 / 重试）。

⚠️ 不覆盖：真 HTTP 推 online（见 `tests/integration/` 的推送探针）、C3 触发路径、C4 并发。
"""
from types import SimpleNamespace

import pytest

from app.core.backflow_client import BackflowClientError
from app.runner import error_push as ep


def _case(cid, cluster_id, case_type="regression_error", input=None):
    env = {"source": {"agent": "cs-agent", "cluster_id": cluster_id}} if cluster_id else {"source": {}}
    return SimpleNamespace(id=cid, case_type=case_type, backflow_envelope=env, input=input)


def _result(case_id, pf, error_type=None, detail=None):
    return SimpleNamespace(case_id=case_id, pass_fail=pf, error_type=error_type,
                           error_detail=detail)


class TestCasesOfCluster:
    def test_splits_by_cluster_id(self):
        cases = [_case(1, 100), _case(2, 200), _case(3, 100)]
        out = ep._cases_of_cluster(cases, {c.id: _result(c.id, "pass") for c in cases})
        assert set(out) == {100, 200}
        assert [c["case_id"] for c in out[100]] == ["1", "3"]

    def test_missing_cluster_id_goes_to_none_bucket(self):
        """无 cluster_id 不静默丢——调用方据 None 桶记 error 日志。"""
        cases = [_case(1, None)]
        out = ep._cases_of_cluster(cases, {1: _result(1, "pass")})
        assert list(out) == [None]

    def test_na_row_carries_error_type(self):
        cases = [_case(1, 100)]
        out = ep._cases_of_cluster(cases, {1: _result(1, "na", "timeout", "超时")})
        assert out[100][0]["error_type"] == "timeout"
        assert out[100][0]["error_detail"] == "超时"

    def test_missing_result_row_defaults_to_na(self):
        out = ep._cases_of_cluster([_case(1, 100)], {})
        assert out[100][0]["pass_fail"] == "na"

    def test_substituted_input_is_flagged(self):
        """批 54：替换凭据必须**外发**。

        不外发时，online 侧一次「样例跑通的 pass」与「原场景真修好了」**逐字同形**——
        用户看到 pass、无从知道它跑的是替身输入 ⇒ 假绿，且两侧测试全绿、没有任何判据会红。
        """
        c = _case(1, 100, input={"file_path": "/app/uploads/s.pdf",
                                 "_substituted_from": "/app/uploads/ghost.pdf"})
        out = ep._cases_of_cluster([c], {1: _result(1, "pass")})
        assert out[100][0]["input_substituted"] is True

    def test_unsubstituted_input_carries_no_flag(self):
        """反方向钉住：没替换过就**不落键**（与本函数 error_type/error_detail 同写法）。

        为什么不落 `False`：载荷形状对每一行都变，且 online 侧再也分不出「没替换」与
        「旧 offline 不发该字段」——而这两种在 online 的语义恰好相同，落 False 只是白白
        让旧行与新行的 raw_json 长得不一样，回查时徒增噪音。
        """
        for inp in ({"file_path": "/app/uploads/s.pdf"}, None, "明文 input"):
            out = ep._cases_of_cluster([_case(1, 100, input=inp)], {1: _result(1, "pass")})
            assert "input_substituted" not in out[100][0], inp

    def test_non_dict_input_does_not_raise(self):
        """`input` 非 dict（纯文本 input 的 case 落库为 str）时不许炸——推送是 run 收尾路径，
        这里抛异常会让整个簇的结果推不出去，代价远大于少一个标记。"""
        out = ep._cases_of_cluster([_case(1, 100, input="帮我查一下XX政策")],
                                   {1: _result(1, "pass")})
        assert out[100][0]["pass_fail"] == "pass"


class TestAssemblePayload:
    def _body(self, **kw):
        run = SimpleNamespace(id=3042, version="2026.09.14-r1", status="partial_failed",
                              finished_at=None)
        args = dict(run=run, agent_name="cs-agent", cluster_id=100, cases=[], latest="v3", prev=None)
        args.update(kw)
        return ep.assemble_payload(**args)

    def test_id_fields_are_str_and_cluster_id_is_int(self):
        """⚠️ 序列化陷阱：online 侧 run_id/case_id 是 str、trigger_signal_id 是 int，方向相反。"""
        body = self._body(cases=[{"case_id": "7", "case_type": "regression_error", "pass_fail": "pass"}])
        assert isinstance(body["run_id"], str) and body["run_id"] == "3042"
        assert isinstance(body["trigger_signal_id"], int)
        assert isinstance(body["cases"][0]["case_id"], str)

    def test_agent_is_online_side_identity_not_offline_agent_name(self):
        assert self._body(agent_name="cs-agent")["agent"] == "cs-agent"

    def test_prev_terminal_null_allowed(self):
        assert self._body(prev=None)["prev_terminal_version"] is None
        assert self._body(prev="v2")["prev_terminal_version"] == "v2"

    def test_empty_cases_is_legal(self):
        assert self._body()["cases"] == []
        assert ep.self_check(self._body()) == []

    def test_schema_version_is_1_0(self):
        assert self._body()["schema_version"] == "1.0"


class TestSelfCheck:
    def _body(self, **kw):
        body = {"schema_version": "1.0", "finished_ts": "2026-09-14T12:00:00", "cases": []}
        body.update(kw)
        return body

    def test_ok(self):
        assert ep.self_check(self._body()) == []

    def test_duplicate_case_id_rejected(self):
        dup = [{"case_id": "1"}, {"case_id": "1"}]
        assert ep.self_check(self._body(cases=dup))

    def test_bad_schema_version_rejected(self):
        assert ep.self_check(self._body(schema_version="2.0"))

    def test_bad_finished_ts_rejected(self):
        assert ep.self_check(self._body(finished_ts="not-a-date"))


class TestVerKey:
    def test_numeric_ordering_not_lexicographic(self):
        """字符串序会把 v10 排在 v9 前；水位判序必须走数值元组。"""
        assert ep._ver_key("v10") > ep._ver_key("v9")
        assert ep._ver_key("2026.09.14") < ep._ver_key("2026.10.01")

    def test_non_numeric_segment_is_zero(self):
        assert ep._ver_key("v1.r1") == (1, 0)


class _RowsDB:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, stmt):
        return SimpleNamespace(all=lambda: self._rows)


class TestWatermarks:
    """两水位口径（§10.1 硬约束 + online verify 用法推导）。"""

    async def _wm(self, rows, run_id=5, version="v5"):
        run = SimpleNamespace(id=run_id, version=version)
        return await ep._watermarks(_RowsDB(rows), 1, run)

    @pytest.mark.asyncio
    async def test_latest_includes_self(self):
        """必须含本 run：不含则 online `_ver_key(fv) > _ver_key(latest)` 恒 no_progress。"""
        latest, _ = await self._wm([(5, "v5"), (3, "v3")])
        assert latest == "v5"

    @pytest.mark.asyncio
    async def test_latest_is_max_by_ver_key_not_by_id(self):
        latest, _ = await self._wm([(1, "v2"), (2, "v10")])
        assert latest == "v10"

    @pytest.mark.asyncio
    async def test_prev_is_most_recent_before_self_not_max(self):
        """prev 是**时间线紧邻前一个**（online 用它查缺行），不是最大版本。"""
        _, prev = await self._wm([(1, "v9"), (3, "v3"), (5, "v5")])
        assert prev == "v3"

    @pytest.mark.asyncio
    async def test_prev_none_when_no_prior(self):
        _, prev = await self._wm([(5, "v5"), (6, "v6")])
        assert prev is None


class TestPushOneRetry:
    async def _push(self, monkeypatch, outcomes):
        calls = []

        async def fake_push(body):
            calls.append(body)
            item = outcomes[len(calls) - 1]
            if isinstance(item, Exception):
                raise item
            return item

        async def no_sleep(_):
            return None

        monkeypatch.setattr(ep.backflow_client, "push_results", fake_push)
        monkeypatch.setattr(ep.asyncio, "sleep", no_sleep)
        return await ep._push_one({"run_id": "1", "trigger_signal_id": 9}), calls

    @pytest.mark.asyncio
    async def test_success_on_first_attempt_no_retry(self, monkeypatch):
        resp, calls = await self._push(monkeypatch, [{"accepted": True}])
        assert resp == {"accepted": True} and len(calls) == 1

    @pytest.mark.asyncio
    async def test_retries_three_times_then_gives_up(self, monkeypatch):
        """§10.1：首推 + 3 次重试（退避 1/2/4s），全败放弃且**不抛**（不阻塞收尾）。"""
        resp, calls = await self._push(monkeypatch, [Exception("x")] * 4)
        assert resp is None and len(calls) == 4

    @pytest.mark.asyncio
    async def test_recovers_on_second_attempt(self, monkeypatch):
        resp, calls = await self._push(monkeypatch, [Exception("boom"), {"accepted": True}])
        assert resp == {"accepted": True} and len(calls) == 2

    # ---- 可重试性分流（O-F.8：secret 缺失/错误 ⇒ 被拒且**不重试**）----
    # 判据一律用**调用次数**（乘法性观测量），不断言日志文案——文案断言无判别力。

    @pytest.mark.asyncio
    async def test_auth_rejection_sends_once(self, monkeypatch):
        """401 = 确定性拒绝 ⇒ 只发一次，不退避。"""
        err = BackflowClientError("结果推送返回 401", 401)
        resp, calls = await self._push(monkeypatch, [err] * 4)
        assert resp is None and len(calls) == 1

    @pytest.mark.asyncio
    async def test_other_4xx_also_sends_once(self, monkeypatch):
        """403/400/422 同属确定性拒绝——判据是**码的类别**，不是 401 特判。"""
        for code in (403, 400, 422):
            err = BackflowClientError(f"结果推送返回 {code}", code)
            resp, calls = await self._push(monkeypatch, [err] * 4)
            assert resp is None and len(calls) == 1, f"code={code} 不该重试"

    @pytest.mark.asyncio
    async def test_5xx_still_retries(self, monkeypatch):
        err = BackflowClientError("结果推送返回 503", 503)
        resp, calls = await self._push(monkeypatch, [err] * 4)
        assert resp is None and len(calls) == 4

    @pytest.mark.asyncio
    async def test_throttle_and_timeout_still_retry(self, monkeypatch):
        for code in (408, 429):
            err = BackflowClientError(f"结果推送返回 {code}", code)
            _, calls = await self._push(monkeypatch, [err] * 4)
            assert len(calls) == 4, f"code={code} 应可重试"

    @pytest.mark.asyncio
    async def test_network_error_without_status_still_retries(self, monkeypatch):
        """`status_code` 缺省（含既有单参构造）⇒ 视作网络层，维持「重试 3 次」原语义。"""
        resp, calls = await self._push(monkeypatch, [BackflowClientError("连接失败")] * 4)
        assert resp is None and len(calls) == 4

    @pytest.mark.asyncio
    async def test_one_shot_rejection_does_not_delay_finish(self, monkeypatch):
        """不可重试时**不得**进入退避（否则收尾仍被拖住 ~7s）。计数 sleep 调用。"""
        sleeps = []

        async def spy_sleep(s):
            sleeps.append(s)

        async def fake_push(body):
            raise BackflowClientError("结果推送返回 401", 401)

        monkeypatch.setattr(ep.backflow_client, "push_results", fake_push)
        monkeypatch.setattr(ep.asyncio, "sleep", spy_sleep)
        await ep._push_one({"run_id": "1", "trigger_signal_id": 9})
        assert sleeps == []
