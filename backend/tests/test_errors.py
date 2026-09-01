"""P1-3：HTTP 层异常处理器契约测试（新建文件）。

register_error_handlers 新增三类 handler：
- RequestValidationError → 400 契约体（FastAPI 默认 422 {"detail": [...]} 不符 {code,message,data}）
- HTTPException（含未注册路由 404 / 405）→ 契约体
- DB 依赖故障（OperationalError / SQLAlchemy TimeoutError / 内建 TimeoutError）→ 503
- IntegrityError 等业务/编程错误仍落 500（不误捕为依赖故障）

用独立最小 app 测 handler 注册（不依赖真实 DB / 中间件），TestClient 同步请求。
"""
import pytest
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from app.core.errors import E_VALIDATION, register_error_handlers


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/boom/op")
    async def boom_op():
        raise OperationalError("stmt", {}, Exception("db conn refused"))

    @app.get("/boom/sqlatimeout")
    async def boom_sqlatimeout():
        raise SQLAlchemyTimeoutError("pool exhausted")

    @app.get("/boom/timeout")
    async def boom_timeout():
        raise TimeoutError("async dependency timeout")

    @app.get("/boom/integrity")
    async def boom_integrity():
        raise IntegrityError("stmt", {}, Exception("unique constraint"))

    @app.get("/boom/http404")
    async def boom_http404():
        raise HTTPException(status_code=404, detail="Not Found")

    @app.get("/boom/validation")  # Query 强校验触发 RequestValidationError
    async def boom_validation(limit: int = Query(..., ge=1)):
        return {"limit": limit}

    register_error_handlers(app)
    return app


@pytest.fixture()
def client():
    # raise_server_exceptions=False：Exception 兜底 handler 走 ServerErrorMiddleware，
    # 后者发完 500 响应后仍 re-raise（供服务器记录）——测试态不把该 re-raise 当测试失败，
    # 而是断言其发出的 500 契约体。
    with TestClient(_app(), raise_server_exceptions=False) as c:
        yield c


def test_request_validation_contract(client):
    resp = client.get("/boom/validation", params={"limit": 0})
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == E_VALIDATION
    assert body["data"] is None
    assert "校验失败" in body["message"]


def test_operational_error_503(client):
    resp = client.get("/boom/op")
    assert resp.status_code == 503
    body = resp.json()
    assert body["code"] == 503
    assert body["message"] == "dependency_unavailable"


def test_sqlalchemy_pool_timeout_503(client):
    resp = client.get("/boom/sqlatimeout")
    assert resp.status_code == 503
    assert resp.json()["message"] == "dependency_unavailable"


def test_builtin_timeout_503(client):
    resp = client.get("/boom/timeout")
    assert resp.status_code == 503
    assert resp.json()["message"] == "dependency_unavailable"


def test_integrity_error_still_500(client):
    # 不误捕：IntegrityError（业务/编程错误）不是依赖故障，仍落兜底 500
    resp = client.get("/boom/integrity")
    assert resp.status_code == 500
    assert resp.json()["code"] == 500


def test_http_exception_contract(client):
    resp = client.get("/boom/http404")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == 404
    assert "Not Found" in body["message"]
    assert body["data"] is None


def test_unregistered_route_404_contract(client):
    # 真实未注册路由：Starlette 内建 HTTPException 404 也走契约体（原 {"detail": "Not Found"}）
    resp = client.get("/no-such-route")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == 404
    assert body["data"] is None
