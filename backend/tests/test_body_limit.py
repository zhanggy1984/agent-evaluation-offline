"""P2-D16 请求体大小上限单测（直接调 body_size_limit 中间件，不起 app/不触库）。

语义：
- 普通 API：Content-Length > BODY_LIMIT_BYTES → 413 {code: E_BODY_TOO_LARGE, ...}
- /api/uploads 豁免（上传大小由 file_max_size 业务校验 + nginx 50m 兜底）
- 非法 Content-Length（非数字）不拦截，交给下游
- chunked 无 Content-Length 时依赖 nginx 兜底（见 main.py 注释）

function 级 loop（避免与 test_executor 的 asyncio.run 干扰 session loop，见 D15 教训）。
"""
import json

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.errors import E_BODY_TOO_LARGE
from app.main import BODY_LIMIT_BYTES, body_size_limit


def _request(method: str = "POST", path: str = "/api/login", content_length=None):
    headers = []
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode("ascii")))
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
        "scheme": "http",
        "root_path": "",
    }
    return Request(scope)


async def _next(request):
    return JSONResponse({"code": 0, "message": "ok", "data": None})


@pytest.mark.asyncio(loop_scope="function")
async def test_body_over_limit_413():
    resp = await body_size_limit(_request(content_length=BODY_LIMIT_BYTES + 1), _next)
    assert resp.status_code == 413
    assert json.loads(resp.body)["code"] == E_BODY_TOO_LARGE


@pytest.mark.asyncio(loop_scope="function")
async def test_body_within_limit_passes():
    resp = await body_size_limit(_request(content_length=1024), _next)
    assert resp.status_code == 200


@pytest.mark.asyncio(loop_scope="function")
async def test_uploads_path_exempt():
    # /api/uploads 豁免：超大 Content-Length 也交给下游，上传大小由业务层校验
    resp = await body_size_limit(_request(path="/api/uploads", content_length=100 * 1024 * 1024), _next)
    assert resp.status_code == 200


@pytest.mark.asyncio(loop_scope="function")
async def test_invalid_content_length_not_blocked():
    # 非法 Content-Length（非数字）不拦截，交给下游处理
    resp = await body_size_limit(_request(content_length="abc"), _next)
    assert resp.status_code == 200
