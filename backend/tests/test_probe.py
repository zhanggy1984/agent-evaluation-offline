"""B.5 契约探测单元测试：逐字段判定 + probe_interface 整流程。

核心逻辑定义见 CLAUDE.md：探测的字段判定是核心分支逻辑。用 unittest（不引入 pytest），
fake adapter/client 喂模拟响应，不触真实网络。
"""
import asyncio
import json
import os
import types
import unittest

import httpx

from app.adapters.base import RequestSpec
from app.core.probe import (
    _check_sse, _check_sync, _is_input_error, _usage_ok, probe_interface,
    validate_probe_input,
)
from app.core.sse_parser import SSEParser

USAGE = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}


def _sse_frame(ev_type: str, data: dict, ev_id: str | None = None) -> bytes:
    lines = [f"event: {ev_type}"] if ev_type else []
    lines.append("data: " + json.dumps(data, ensure_ascii=False))
    if ev_id:
        lines.append(f"id: {ev_id}")
    return ("\n".join(lines) + "\n\n").encode()


def _sse_chunks(events: list[tuple[str, dict]]) -> bytes:
    return b"".join(_sse_frame(t, d) for t, d in events)


class _FakeStream:
    """SSE 流式响应：async context manager + aiter_bytes。"""

    def __init__(self, status_code: int, body: bytes):
        self.status_code = status_code
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_bytes(self):
        yield self._body

    async def aread(self):
        return self._body


class _FakeAdapter:
    """最小 duck-typing adapter：走真实 SSEParser 解析 chunk。"""

    def __init__(self, contract_type: str = "sse"):
        self.contract_type = contract_type
        self.interface = types.SimpleNamespace(id=1)
        self._fail_prepare: str | None = None
        self._fail_prepare_exc: Exception | None = None

    async def prepare(self, case, client=None):
        if self._fail_prepare_exc is not None:
            raise self._fail_prepare_exc
        if self._fail_prepare:
            raise RuntimeError(self._fail_prepare)

    def build_request(self, case):
        return RequestSpec("POST", "http://agent.local/v1/chat", {})

    def parse_stream_chunk(self, chunk: bytes):
        return SSEParser().feed(chunk)


class _FakeClient:
    """fake httpx.AsyncClient：stream 返回 _FakeStream，request 返回 httpx.Response。"""

    def __init__(self, stream: _FakeStream | None = None, req: httpx.Response | None = None):
        self._stream = stream
        self._req = req

    def stream(self, method, url, headers=None, json=None, timeout=None):
        return self._stream

    async def request(self, method, url, headers=None, json=None, timeout=None):
        return self._req


def _run(coro):
    return asyncio.run(coro)


# ---------------- usage 三分量 ----------------

class TestUsageOk(unittest.TestCase):
    def test_valid(self):
        self.assertTrue(_usage_ok(USAGE))

    def test_missing_field(self):
        self.assertFalse(_usage_ok({"prompt_tokens": 1, "completion_tokens": 2}))

    def test_negative(self):
        self.assertFalse(_usage_ok({"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": -3}))

    def test_not_dict(self):
        self.assertFalse(_usage_ok([1, 2, 3]))
        self.assertFalse(_usage_ok(None))

    def test_non_numeric(self):
        self.assertFalse(_usage_ok({"prompt_tokens": "a", "completion_tokens": 2, "total_tokens": 3}))


# ---------------- SSE 逐字段判定 ----------------

class TestCheckSse(unittest.TestCase):
    def test_all_events_pass(self):
        seen = {t: 1 for t in ("meta", "stage", "reasoning", "tool_call", "answer", "usage", "done")}
        fields, errors = _check_sse(seen)
        self.assertEqual(errors, [])
        self.assertTrue(all(fields[k] for k in ("answer", "usage", "done")))

    def test_missing_usage(self):
        seen = {"answer": 1, "done": 1}
        _, errors = _check_sse(seen)
        self.assertTrue(any("usage" in e for e in errors))

    def test_missing_done(self):
        seen = {"answer": 1, "usage": 1}
        _, errors = _check_sse(seen)
        self.assertTrue(any("done" in e for e in errors))

    def test_error_event_fails(self):
        seen = {"answer": 1, "usage": 1, "done": 1, "error": 1}
        _, errors = _check_sse(seen)
        self.assertTrue(any("error" in e for e in errors))

    def test_events_enumeration(self):
        seen = {"answer": 2, "done": 1}
        fields, _ = _check_sse(seen)
        self.assertEqual(fields["events"], ["answer", "done"])


# ---------------- sync 逐字段判定 ----------------

class TestCheckSync(unittest.TestCase):
    def _resp(self):
        return {"answer": "合同条款正确", "reasoning": "…",
                "tool_calls": [{"name": "r1", "args": {}, "result": {}}],
                "usage": dict(USAGE),
                "timing": {"start_ts": 1, "first_token_ts": None, "end_ts": 2}}

    def test_full_pass(self):
        fields, errors = _check_sync(self._resp())
        self.assertEqual(errors, [])
        self.assertTrue(fields["timing_start"] and fields["timing_end"])
        self.assertTrue(fields["usage"])

    def test_empty_answer(self):
        resp = self._resp()
        resp["answer"] = ""
        _, errors = _check_sync(resp)
        self.assertTrue(any("answer" in e for e in errors))

    def test_negative_usage(self):
        resp = self._resp()
        resp["usage"]["total_tokens"] = -1
        fields, errors = _check_sync(resp)
        self.assertFalse(fields["usage"])
        self.assertTrue(any("usage" in e for e in errors))

    def test_missing_timing_end(self):
        resp = self._resp()
        resp["timing"].pop("end_ts")
        _, errors = _check_sync(resp)
        self.assertTrue(any("timing" in e for e in errors))

    def test_tool_calls_not_list(self):
        resp = self._resp()
        resp["tool_calls"] = {"name": "x"}  # 非 list：可选字段，不产生错误
        fields, errors = _check_sync(resp)
        self.assertFalse(fields["tool_calls"])
        self.assertEqual(errors, [])


# ---------------- probe_interface SSE 整流程 ----------------

class TestProbeSse(unittest.TestCase):
    def test_pass(self):
        body = _sse_chunks([("reasoning", {"delta": "想"}),
                            ("answer", {"delta": "合同有效", "ts": 1}),
                            ("usage", dict(USAGE)),
                            ("done", {})])
        adapter = _FakeAdapter("sse")
        client = _FakeClient(stream=_FakeStream(200, body))
        pr = _run(probe_interface(adapter, client, case=None))
        self.assertTrue(pr.ok)
        self.assertEqual(pr.http_status, 200)
        self.assertTrue(pr.fields["answer"] and pr.fields["usage"] and pr.fields["done"])
        self.assertIsNotNone(pr.fields["ttft_ms"])
        self.assertEqual(pr.errors, [])

    def test_missing_usage(self):
        body = _sse_chunks([("answer", {"delta": "x"}), ("done", {})])
        pr = _run(probe_interface(_FakeAdapter("sse"), _FakeClient(stream=_FakeStream(200, body)), case=None))
        self.assertFalse(pr.ok)
        self.assertTrue(any("usage" in e for e in pr.errors))

    def test_http_error(self):
        pr = _run(probe_interface(_FakeAdapter("sse"),
                                  _FakeClient(stream=_FakeStream(500, b"server error")), case=None))
        self.assertFalse(pr.ok)
        self.assertTrue(any("HTTP 500" in e for e in pr.errors))
        self.assertEqual(pr.raw_sample, "server error")

    def test_prepare_failure(self):
        adapter = _FakeAdapter("sse")
        adapter._fail_prepare = "登录失败"
        pr = _run(probe_interface(adapter, _FakeClient(), case=None))
        self.assertFalse(pr.ok)
        self.assertTrue(any("登录失败" in e for e in pr.errors))


# ---------------- probe_interface sync 整流程 ----------------

class TestProbeSync(unittest.TestCase):
    def _resp(self):
        return {"answer": "ok", "usage": dict(USAGE),
                "timing": {"start_ts": 1, "end_ts": 2}, "tool_calls": []}

    def test_pass(self):
        pr = _run(probe_interface(_FakeAdapter("sync"),
                                  _FakeClient(req=httpx.Response(200, json=self._resp())), case=None))
        self.assertTrue(pr.ok)
        self.assertEqual(pr.http_status, 200)
        self.assertTrue(pr.fields["usage"] and pr.fields["timing_start"] and pr.fields["timing_end"])

    def test_missing_timing(self):
        resp = self._resp()
        resp.pop("timing")
        pr = _run(probe_interface(_FakeAdapter("sync"),
                                  _FakeClient(req=httpx.Response(200, json=resp)), case=None))
        self.assertFalse(pr.ok)
        self.assertTrue(any("timing" in e for e in pr.errors))

    def test_http_error(self):
        pr = _run(probe_interface(_FakeAdapter("sync"),
                                  _FakeClient(req=httpx.Response(500, text="boom")), case=None))
        self.assertFalse(pr.ok)
        self.assertTrue(any("HTTP 500" in e for e in pr.errors))

    def test_non_json(self):
        pr = _run(probe_interface(_FakeAdapter("sync"),
                                  _FakeClient(req=httpx.Response(200, content=b"not json")), case=None))
        self.assertFalse(pr.ok)
        self.assertTrue(any("非 JSON" in e for e in pr.errors))

    def test_elapsed_and_sample(self):
        pr = _run(probe_interface(_FakeAdapter("sync"),
                                  _FakeClient(req=httpx.Response(200, json=self._resp())), case=None))
        self.assertGreaterEqual(pr.elapsed_ms, 0)
        self.assertIn("answer", pr.raw_sample)


# ---------------- Q4：输入构造错误分类（样例文件缺失 vs 契约不达标） ----------------

class TestInputErrorClassification(unittest.TestCase):
    def test_file_not_found_is_input_error(self):
        self.assertTrue(_is_input_error(FileNotFoundError("no such file")))

    def test_uploads_path_rejected_is_input_error(self):
        # _assert_inside_uploads 白名单拒绝固定消息（含 uploads 字样）
        self.assertTrue(_is_input_error(ValueError("文件路径不在 uploads 目录内: /etc/passwd")))

    def test_prepare_http_failure_not_input_error(self):
        # agent 契约类错误（上传被拒 / 登录失败）不算输入错误
        self.assertFalse(_is_input_error(RuntimeError("prepare.upload HTTP 400: bad pdf")))

    def test_network_error_not_input_error(self):
        self.assertFalse(_is_input_error(httpx.RequestError("connect failed")))


class TestValidateProbeInput(unittest.TestCase):
    """前置校验：文件型（file_path 键）存在性 + uploads 白名单。"""

    def setUp(self):
        import tempfile
        from app.adapters import base as base_mod
        from app.core import probe as probe_mod
        self.tmp = tempfile.TemporaryDirectory()
        self._probe_mod = probe_mod
        self._old_uploads = base_mod._UPLOADS_DIR
        base_mod._UPLOADS_DIR = os.path.realpath(self.tmp.name)
        probe_mod._UPLOADS_DIR = base_mod._UPLOADS_DIR  # 报错消息里的目录同样指向 tmp

    def tearDown(self):
        from app.adapters import base as base_mod
        base_mod._UPLOADS_DIR = self._old_uploads
        self._probe_mod._UPLOADS_DIR = self._old_uploads
        self.tmp.cleanup()

    def test_non_file_input_passes(self):
        self.assertIsNone(validate_probe_input({"content": "hello"}))
        self.assertIsNone(validate_probe_input(None))
        self.assertIsNone(validate_probe_input(["not", "dict"]))

    def test_existing_file_passes(self):
        f = os.path.join(self.tmp.name, "sample.pdf")
        with open(f, "wb") as fh:
            fh.write(b"pdf")
        self.assertIsNone(validate_probe_input({"file_path": f}))

    def test_missing_file_reported(self):
        err = validate_probe_input({"file_path": os.path.join(self.tmp.name, "gone.pdf")})
        self.assertIsNotNone(err)
        self.assertIn("样例文件不存在", err)

    def test_outside_uploads_rejected(self):
        # 路径跳出 uploads（../ 逃逸）→ 白名单拒绝，不是「不存在」
        evil = os.path.join(self.tmp.name + "_evil", "..", "..", "x.pdf")
        err = validate_probe_input({"file_path": evil})
        self.assertIsNotNone(err)
        self.assertIn("不在 uploads 目录内", err)


class TestProbeInputErrorFlag(unittest.TestCase):
    """probe_interface 里输入类错误标 input_error，契约类错误不标。"""

    def test_prepare_file_missing_sets_input_error(self):
        adapter = _FakeAdapter("sse")
        adapter._fail_prepare_exc = FileNotFoundError("no such file")
        pr = _run(probe_interface(adapter, _FakeClient(), case=None))
        self.assertFalse(pr.ok)
        self.assertTrue(pr.input_error)
        self.assertTrue(any("prepare" in e for e in pr.errors))

    def test_prepare_http_failure_not_input_error(self):
        adapter = _FakeAdapter("sse")
        adapter._fail_prepare = "prepare.upload HTTP 400"
        pr = _run(probe_interface(adapter, _FakeClient(), case=None))
        self.assertFalse(pr.ok)
        self.assertFalse(pr.input_error)


if __name__ == "__main__":
    unittest.main()
