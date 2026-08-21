"""7.2 单用例执行集成测试（app/runner/executor.py）。

execute_case 纯依赖注入（adapter/client/case 全传入，不触 DB），宿主直接跑。
覆盖 adapter+assembler 协作与全部 error_type 分类（§8 统一技术失败口径）：
SSE 正常/断流无 done/无 usage/HTTP 非 2xx/超时/连接错误/解析错误/前置失败/
构建失败/未知异常兜底；同步变体正常与解析失败。
用 httpx.MockTransport 模拟 agent 网络层，chunk 走真实 SSEParser 解析。
"""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from app.adapters.base import RequestSpec
from app.core.sse_parser import SSEParseError, SSEParser
from app.runner import executor
from app.runner.executor import ERROR_CONNECT, ERROR_CONTRACT, ERROR_HTTP, \
    ERROR_NO_DONE, ERROR_NO_USAGE, ERROR_SSE_PARSE, ERROR_TIMEOUT, execute_case


def _sse_bytes(*frames) -> bytes:
    """SSE 帧 → 字节：event/data/id 各一行，空行结束。"""
    out = b""
    for typ, data, eid in frames:
        out += f"event: {typ}\nid: {eid}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")
    return out


def _case(cid=1):
    return SimpleNamespace(id=cid)


class _SSEAdapter:
    """SSE 变体 mock：parse_stream_chunk 走真实 SSEParser（验证 executor+parser+assembler 协作）。"""

    contract_type = "sse"

    def __init__(self, chunks=b"", prepare_error=None, build_error=None,
                 parse_error=None, unknown_error=False):
        self._chunks = chunks
        self._prepare_error = prepare_error
        self._build_error = build_error
        self._parse_error = parse_error
        self._unknown_error = unknown_error

    async def prepare(self, case, client=None):
        if self._prepare_error:
            raise self._prepare_error

    def build_request(self, case):
        if self._build_error:
            raise self._build_error
        return RequestSpec(method="POST", url="http://mock-agent.test/v1/chat/completions",
                           headers={"Content-Type": "application/json"},
                           json={"messages": [{"role": "user", "content": "hi"}]})

    def parse_stream_chunk(self, chunk):
        if self._parse_error:
            raise self._parse_error
        if self._unknown_error:
            raise RuntimeError("未知异常")
        return SSEParser().feed(self._chunks if chunk is None else chunk)


class _SyncAdapter:
    """同步变体 mock：response JSON → 统一结果。"""

    contract_type = "sync"

    def __init__(self, unified=None, parse_error=None, prepare_error=None, build_error=None):
        self._unified = unified
        self._parse_error = parse_error
        self._prepare_error = prepare_error
        self._build_error = build_error

    async def prepare(self, case, client=None):
        if self._prepare_error:
            raise self._prepare_error

    def build_request(self, case):
        if self._build_error:
            raise self._build_error
        return RequestSpec(method="POST", url="http://mock-agent.test/sync", headers={},
                           json={})

    def parse_sync(self, resp_json):
        if self._parse_error:
            raise self._parse_error
        return self._unified


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------- SSE 变体 ----------------
_NORMAL = _sse_bytes(
    ("answer", {"delta": "你", "ts": 1.0}, "a1"),
    ("answer", {"delta": "好", "ts": 1.1}, "a2"),
    ("usage", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}, "u1"),
    ("done", {"ts": 1.2}, "d1"),
)


def test_sse_normal():
    async def main():
        async with _client(lambda req: httpx.Response(200, content=_NORMAL)) as c:
            out = await execute_case(_SSEAdapter(chunks=_NORMAL), c, _case())
        assert out.ok
        assert out.unified["answer"] == "你好"
        assert out.unified["usage"]["total_tokens"] == 15
        assert out.status_code == 200
        assert out.timing["end_ts"] is not None
    asyncio.run(main())


def test_sse_no_done():
    # 断流：有 answer/usage 无 done → ERROR_NO_DONE
    chunks = _sse_bytes(("answer", {"delta": "x"}, "a1"), ("usage", {"total_tokens": 1}, "u1"))
    async def main():
        async with _client(lambda req: httpx.Response(200, content=chunks)) as c:
            out = await execute_case(_SSEAdapter(chunks=chunks), c, _case())
        assert not out.ok and out.error_type == ERROR_NO_DONE
    asyncio.run(main())


def test_sse_no_usage():
    # 契约违反：有 answer/done 无 usage → ERROR_NO_USAGE
    chunks = _sse_bytes(("answer", {"delta": "x"}, "a1"), ("done", {}, "d1"))
    async def main():
        async with _client(lambda req: httpx.Response(200, content=chunks)) as c:
            out = await execute_case(_SSEAdapter(chunks=chunks), c, _case())
        assert not out.ok and out.error_type == ERROR_NO_USAGE
    asyncio.run(main())


def test_http_non_200():
    async def main():
        async with _client(lambda req: httpx.Response(500, text="boom")) as c:
            out = await execute_case(_SSEAdapter(), c, _case())
        assert not out.ok and out.error_type == ERROR_HTTP
        assert "500" in out.error_detail
    asyncio.run(main())


def test_connect_error():
    def handler(req):
        raise httpx.ConnectError("connection refused")
    async def main():
        async with _client(handler) as c:
            out = await execute_case(_SSEAdapter(), c, _case())
        assert not out.ok and out.error_type == ERROR_CONNECT
    asyncio.run(main())


def test_timeout():
    def handler(req):
        raise httpx.ConnectTimeout("timed out")
    async def main():
        async with _client(handler) as c:
            out = await execute_case(_SSEAdapter(), c, _case())
        assert not out.ok and out.error_type == ERROR_TIMEOUT
    asyncio.run(main())


def test_sse_parse_error():
    # chunk 解析失败（非法 JSON data）→ ERROR_SSE_PARSE
    async def main():
        async with _client(lambda req: httpx.Response(200, content=b"event: answer\ndata: not-json\n\n")) as c:
            out = await execute_case(_SSEAdapter(), c, _case())
        assert not out.ok and out.error_type == ERROR_SSE_PARSE
    asyncio.run(main())


def test_prepare_error():
    async def main():
        async with _client(lambda req: httpx.Response(200, content=b"")) as c:
            out = await execute_case(_SSEAdapter(prepare_error=RuntimeError("login failed")), c, _case())
        assert not out.ok and out.error_type == ERROR_CONTRACT
        assert "prepare" in out.error_detail
    asyncio.run(main())


def test_build_request_error():
    async def main():
        async with _client(lambda req: httpx.Response(200, content=b"")) as c:
            out = await execute_case(_SSEAdapter(build_error=ValueError("bad template")), c, _case())
        assert not out.ok and out.error_type == ERROR_CONTRACT
        assert "build_request" in out.error_detail
    asyncio.run(main())


def test_unknown_exception_fallback():
    # 未知异常兜底：归类技术失败，不中断 run（空行 chunk 确保进入流循环触发 parse）
    async def main():
        async with _client(lambda req: httpx.Response(200, content=b"\n")) as c:
            out = await execute_case(_SSEAdapter(unknown_error=True), c, _case())
        assert not out.ok and out.error_type == ERROR_CONTRACT
    asyncio.run(main())


# ---------------- 同步变体 ----------------
def test_sync_normal():
    unified = {"answer": "同步答案", "reasoning": "", "tool_calls": [], "usage": {"total_tokens": 7}, "meta": {}}
    async def main():
        async with _client(lambda req: httpx.Response(200, json={})) as c:
            out = await execute_case(_SyncAdapter(unified=unified), c, _case())
        assert out.ok
        assert out.unified["answer"] == "同步答案"
        assert out.unified["usage"]["total_tokens"] == 7
        assert out.status_code == 200
    asyncio.run(main())


def test_sync_parse_error():
    async def main():
        async with _client(lambda req: httpx.Response(200, json={})) as c:
            out = await execute_case(_SyncAdapter(parse_error=KeyError("answer")), c, _case())
        assert not out.ok and out.error_type == ERROR_CONTRACT
        assert "parse_sync" in out.error_detail
    asyncio.run(main())


def test_sync_http_error():
    async def main():
        async with _client(lambda req: httpx.Response(403, text="forbidden")) as c:
            out = await execute_case(_SyncAdapter(), c, _case())
        assert not out.ok and out.error_type == ERROR_HTTP
    asyncio.run(main())


# ---------------- 7.5c SSE 断流续推（Last-Event-ID） ----------------

class _ResumeAdapter(_SSEAdapter):
    """支持续推的 SSE mock：持久 parser（透出 last_event_id）+ supports_resume opt-in。"""

    supports_resume = True

    def __init__(self, chunks=b"", **kwargs):
        super().__init__(chunks=chunks, **kwargs)
        self._parser = SSEParser()

    @property
    def last_event_id(self):
        return self._parser.last_id

    def parse_stream_chunk(self, chunk):
        if self._parse_error:
            raise self._parse_error
        return self._parser.feed(self._chunks if chunk is None else chunk)


def test_sse_resume_last_event_id():
    # 首流断流（answer/usage 无 done，最后事件 id=u1）→ 续推请求带 Last-Event-ID: u1 → 补 done → ok
    broken = _sse_bytes(("answer", {"delta": "x"}, "a1"), ("usage", {"total_tokens": 1}, "u1"))
    done = _sse_bytes(("done", {}, "d1"))
    seen: list[httpx.Request] = []

    def handler(req):
        seen.append(req)
        if "Last-Event-ID" not in req.headers:
            return httpx.Response(200, content=broken)
        assert req.headers["Last-Event-ID"] == "u1"  # 续推头 = 断点事件 id
        return httpx.Response(200, content=done)

    async def main():
        async with _client(handler) as c:
            out = await execute_case(_ResumeAdapter(), c, _case())
        assert out.ok
        assert len(seen) == 2
        assert seen[1].headers["Last-Event-ID"] == "u1"
        assert out.unified["answer"] == "x"
        assert out.unified["usage"]["total_tokens"] == 1
    asyncio.run(main())


def test_sse_resume_exhausted_no_done():
    # 续推后仍断流 → 到续推上限（1 次），退化为 ERROR_NO_DONE（orchestrator 按可重试兜底）
    broken = _sse_bytes(("answer", {"delta": "x"}, "a1"), ("usage", {"total_tokens": 1}, "u1"))
    seen: list[httpx.Request] = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, content=broken)

    async def main():
        async with _client(handler) as c:
            out = await execute_case(_ResumeAdapter(), c, _case())
        assert not out.ok and out.error_type == ERROR_NO_DONE
        assert len(seen) == 2  # 首流 + 1 次续推
    asyncio.run(main())
