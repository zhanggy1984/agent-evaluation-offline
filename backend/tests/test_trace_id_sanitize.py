"""P2-E4 trace_id 消毒单测：X-Request-ID 格式白名单，非法值重生成 uuid。

正常链路网关强制覆盖 X-Request-ID（nginx $request_id，32 hex），但直连 backend 时
客户端可传任意值——控制字符会污染日志/响应头（日志注入），超长值撑爆响应头。
中间件对非 `[A-Za-z0-9._-]{1,64}` 的请求头一律重生成 uuid。
"""
import re
import types
from unittest.mock import MagicMock

import pytest

from app.core.logging import trace_id_var
from app.main import trace_middleware

_HEX32 = re.compile(r"^[0-9a-f]{32}$")


async def _run(header_value=None):
    headers = {"X-Request-ID": header_value} if header_value is not None else {}
    req = types.SimpleNamespace(headers=headers)
    resp = MagicMock()
    resp.headers = {}

    async def _next(_r):
        return resp

    await trace_middleware(req, _next)
    return resp.headers, trace_id_var.get()


@pytest.mark.asyncio(loop_scope="function")
async def test_valid_hex_kept_verbatim():
    rid = "0f8f0f8f0f8f0f8f0f8f0f8f0f8f0f8f"
    headers, tid = await _run(rid)
    assert tid == rid, "合法 32 hex 应原样保留（保持网关 trace 关联）"
    assert headers["X-Request-ID"] == rid


@pytest.mark.asyncio(loop_scope="function")
async def test_newline_injected_value_replaced():
    # 直连场景伪造：控制字符（\n）会注入日志/响应头
    _, tid = await _run("good\nLocation: javascript:alert(1)")
    assert _HEX32.fullmatch(tid), "含控制字符应重生成 uuid"
    assert "\n" not in tid


@pytest.mark.asyncio(loop_scope="function")
async def test_missing_header_generates_uuid():
    _, tid = await _run()
    assert _HEX32.fullmatch(tid)


@pytest.mark.asyncio(loop_scope="function")
async def test_overlong_value_replaced():
    # 超长（>64）撑爆响应头/日志行
    _, tid = await _run("a" * 200)
    assert _HEX32.fullmatch(tid)


@pytest.mark.asyncio(loop_scope="function")
async def test_dotted_dashed_value_kept():
    # uuid 形态（含横线）属安全格式，应保留
    rid = "123e4567-e89b-12d3-a456-426614174000"
    _, tid = await _run(rid)
    assert tid == rid
