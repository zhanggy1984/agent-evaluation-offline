"""FastAPI 应用入口。

- 单进程部署（uvicorn workers=1），进程内状态（熔断/信号量/限速）成立
- startup：启动 scanner 后台循环（§15.1 心跳租约/硬超时回收）
- shutdown：取消 scanner task
"""
import asyncio
import logging
import re
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import (
    agents, annotations, auth, cases, config, dashboard, exports, meta, runs,
    scaffold, uploads, users,
)
from app.core.config import settings
from app.core.db import SessionLocal
from app.core.errors import E_BODY_TOO_LARGE, register_error_handlers
from app.core.logging import setup_logging, trace_id_var
from app.judge.worker import judge_worker_loop
from app.runner.scanner import scanner_loop

setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="AI Agent 评测系统", version="0.1.0")

# P2-E6：CORS 允许源改为 config（cors_origins 逗号分隔，可配 CORS_ORIGINS= 置空关闭）。
# 默认保留开发期 vite 直连（5173）+ 容器前端（8180）；生产同源部署建议置空收紧。
# 注：同源部署时跨域请求到不了后端，CORS 仅对前端直连 backend 场景生效。
def _parse_cors_origins(value: str) -> list[str]:
    """逗号分隔 CORS 源 → 去空白去空项（独立函数便于单测）。"""
    return [o.strip() for o in value.split(",") if o.strip()]


_cors_origins = _parse_cors_origins(settings.cors_origins)
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

register_error_handlers(app)


# P2-E4：trace_id 格式白名单。正常链路网关强制覆盖 X-Request-ID（nginx $request_id，32 hex），
# 但直连 backend 时客户端可传任意值——控制字符会污染日志/响应头（日志注入），超长值撑爆响应头。
# 非安全格式一律重生成 uuid（合法 uuid 天然匹配）。
_TRACE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@app.middleware("http")
async def trace_middleware(request: Request, call_next):
    """链路追踪：取网关透传的 X-Request-ID（无则生成 uuid），写入 contextvar 供日志
    filter 使用，并在响应头回传（经网关时网关会隐藏后端重复头，无副作用）。"""
    rid = request.headers.get("X-Request-ID") or ""
    if not _TRACE_ID_RE.fullmatch(rid):
        rid = uuid.uuid4().hex
    trace_id_var.set(rid)
    response = await call_next(request)
    response.headers.setdefault("X-Request-ID", rid)
    return response


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """7.6 A5：安全响应头。API 无内联内容，CSP 收紧到 default-src 'none'；
    /docs /redoc /openapi.json 例外（Swagger UI 需加载 CDN 脚本，仅跳过 CSP，其余头保留）。"""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.url.path not in ("/docs", "/redoc", "/openapi.json"):
        response.headers.setdefault("Content-Security-Policy", "default-src 'none'")
    return response


# P2-D16：请求体大小上限（backend 纵深防御）。普通 API 10 MiB；/api/uploads 豁免
# （上传大小由 file_max_size 业务校验 + 前端 nginx client_max_body_size 50m 兜底）。
# 对外唯一入口是前端 nginx，此处防内网直连 backend:8000 时大 body 耗尽内存。
# chunked 无 Content-Length 时依赖 nginx 兜底（nginx 按实际传输长度限制），不做
# 流式读取计数——避免消费 body 破坏 multipart 上传的流式处理（UploadFile spool 到临时文件）。
BODY_LIMIT_BYTES = 10 * 1024 * 1024


@app.middleware("http")
async def body_size_limit(request: Request, call_next):
    # 注册在中间件栈最外层（最先执行）：超限直接 413，不进入后续处理
    if not request.url.path.startswith("/api/uploads"):
        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) > BODY_LIMIT_BYTES:
                    return JSONResponse(
                        {"code": E_BODY_TOO_LARGE, "message": "请求体过大", "data": None},
                        status_code=413,
                    )
            except ValueError:
                pass  # 非法 Content-Length 不拦截，交给下游处理
    return await call_next(request)


app.include_router(auth.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(agents.router, prefix="/api")
app.include_router(scaffold.router, prefix="/api")
app.include_router(config.router, prefix="/api")
app.include_router(runs.router, prefix="/api")
app.include_router(cases.router, prefix="/api")
app.include_router(cases.case_router, prefix="/api")
app.include_router(uploads.router, prefix="/api")
app.include_router(annotations.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(exports.router, prefix="/api")
app.include_router(exports.download_router, prefix="/api")
app.include_router(meta.router, prefix="/api")

_scanner_task: asyncio.Task | None = None
_judge_task: asyncio.Task | None = None


@app.get("/healthz")
async def healthz():
    # P2-D14：探 DB（SELECT 1），DB 不可达 → 503 degraded（就绪语义）。Docker HEALTHCHECK
    # 用 urllib 对 503 抛错 → 容器标 unhealthy；restart 策略不因 unhealthy 重启（基于退出码），
    # 故 DB 挂时容器保持运行不 crash-loop，网关/监控可见 503 信号。
    try:
        async with SessionLocal() as db:
            await db.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception:
        logger.warning("healthz: DB 不可达")
        return JSONResponse({"status": "degraded"}, status_code=503)


@app.on_event("startup")
async def startup():
    global _scanner_task, _judge_task
    _scanner_task = asyncio.create_task(scanner_loop())
    _judge_task = asyncio.create_task(judge_worker_loop())
    logger.info("startup: scanner + judge worker 已启动")


@app.on_event("shutdown")
async def shutdown():
    if _scanner_task is not None:
        _scanner_task.cancel()
    if _judge_task is not None:
        _judge_task.cancel()
        logger.info("shutdown: scanner + judge worker 已停止")
