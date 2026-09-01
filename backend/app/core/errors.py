"""统一业务异常与错误码（solution_detail.md §八）。

HTTP 层统一响应 {code, message, data}；业务 code 段：
1xxx 认证 / 2xxx 资源 / 3xxx 评测 / 4xxx 文件。
"""
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

# ---- 认证 1xxx ----
E_TOKEN_INVALID = 1001
E_NO_PERMISSION = 1002
E_ACCOUNT_LOCKED = 1003
E_RATE_LIMITED = 1004
# 首登未改密：token 有效但 password_changed_at 为空，业务接口拦截（改密/me 放行）
E_NEED_CHANGE_PASSWORD = 1005

# ---- 资源 2xxx ----
E_NOT_FOUND = 2001
E_CONFLICT = 2002
E_VALIDATION = 2003

# ---- 评测 3xxx ----
E_RUN_MUTEX = 3001
E_CONTRACT_FAIL = 3002
E_ALREADY_RUNNING = 3003

# ---- 文件 4xxx ----
E_FILE_TYPE = 4001
E_FILE_SIZE = 4002
E_FILE_PATH = 4003
# P2-D16：请求体过大（body_size_limit 中间件 413，防内网直连时大 body 攻击）
E_BODY_TOO_LARGE = 4004


class ApiError(Exception):
    """业务异常，异常处理器映射为统一响应体。"""

    def __init__(self, code: int, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)

    def to_response(self) -> dict:
        return {"code": self.code, "message": self.message, "data": None}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status_code, content=exc.to_response())

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(request: Request, exc: RequestValidationError):
        # P1-3：请求校验失败遵守契约体。FastAPI 默认 422 {"detail": [...]} 不符
        # {code,message,data}；业务口径校验失败一律 400 + E_VALIDATION（对齐 ApiError 语义）。
        return JSONResponse(
            status_code=400,
            content={"code": E_VALIDATION,
                     "message": f"请求参数校验失败: {exc.errors()}", "data": None},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(request: Request, exc: StarletteHTTPException):
        # P1-3：未注册路由 404 / 405 等 Starlette 内建 HTTPException 也走契约体
        # （原响应 {"detail": "Not Found"} 与 ApiError 契约不一致，前端统一解析 {code,message}）。
        # 必须注册在 Starlette 基类（Router 抛的是 starlette.HTTPException，fastapi.HTTPException
        # 是其子类经 MRO 命中）——只注册 fastapi 子类会漏 Router 404。
        return JSONResponse(
            status_code=exc.status_code,
            headers=dict(exc.headers) if exc.headers else None,
            content={"code": exc.status_code, "message": str(exc.detail), "data": None},
        )

    @app.exception_handler(OperationalError)
    async def _handle_db_unavailable(request: Request, exc: OperationalError):
        # P1-3：DB 连接拒绝/重置/锁等待等 OperationalError → 503（依赖不可用），
        # 不再落入兜底 500。不捕全 SQLAlchemyError——IntegrityError 等业务/编程错仍 500。
        logger.error("dependency unavailable (db): %s", exc)
        return JSONResponse(status_code=503,
                            content={"code": 503, "message": "dependency_unavailable", "data": None})

    @app.exception_handler(SQLAlchemyTimeoutError)
    async def _handle_db_pool_timeout(request: Request, exc: SQLAlchemyTimeoutError):
        # P1-3：连接池等待超时 → 503。注意：SQLAlchemy 的 TimeoutError 是 SQLAlchemyError
        # 直接子类（非 OperationalError 亦非内建 TimeoutError），须显式捕获才不落 500。
        logger.error("dependency timeout (db pool): %s", exc)
        return JSONResponse(status_code=503,
                            content={"code": 503, "message": "dependency_unavailable", "data": None})

    @app.exception_handler(TimeoutError)
    async def _handle_dependency_timeout(request: Request, exc: TimeoutError):
        # P1-3：依赖（DB/中间件）等待超时 → 503（Py3.11 起 asyncio.TimeoutError 即内建
        # TimeoutError，覆盖 async 等待超时）。
        logger.error("dependency timeout: %s", exc)
        return JSONResponse(status_code=503,
                            content={"code": 503, "message": "dependency_unavailable", "data": None})

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception):
        # 兜底：不泄露内部细节，统一 500（用模块 logger，FastAPI 实例无 logger 属性）
        logger.exception("unhandled error: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"code": 500, "message": "internal_error", "data": None},
        )
