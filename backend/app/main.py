"""FastAPI 应用入口。

- 单进程部署（uvicorn workers=1），进程内状态（熔断/信号量/限速）成立
- startup：启动 scanner 后台循环（§15.1 心跳租约/硬超时回收）
- shutdown：取消 scanner task
"""
import asyncio
import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import (
    agents, annotations, auth, cases, config, dashboard, exports, meta, runs,
    scaffold, uploads, users,
)
from app.core.db import SessionLocal
from app.core.errors import register_error_handlers
from app.core.logging import setup_logging, trace_id_var
from app.judge.worker import judge_worker_loop
from app.runner.scanner import scanner_loop

setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="AI Agent 评测系统", version="0.1.0")

# 前端跨域（前端 nginx 同源部署时其实不需要；开发期 vite 直连需要）
# 宿主端口迁移后：8080 已让给 good-question，容器前端走 8180
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:8180"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_error_handlers(app)


@app.middleware("http")
async def trace_middleware(request: Request, call_next):
    """链路追踪：取网关透传的 X-Request-ID（无则生成 uuid），写入 contextvar 供日志
    filter 使用，并在响应头回传（经网关时网关会隐藏后端重复头，无副作用）。"""
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
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
