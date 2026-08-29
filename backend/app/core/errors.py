"""统一业务异常与错误码（solution_detail.md §八）。

HTTP 层统一响应 {code, message, data}；业务 code 段：
1xxx 认证 / 2xxx 资源 / 3xxx 评测 / 4xxx 文件。
"""
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

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

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception):
        # 兜底：不泄露内部细节，统一 500（用模块 logger，FastAPI 实例无 logger 属性）
        logger.exception("unhandled error: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"code": 500, "message": "internal_error", "data": None},
        )
