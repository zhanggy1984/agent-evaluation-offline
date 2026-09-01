"""异步数据库引擎与会话工厂（pool_size 显式配置，方案决策）。

单进程部署（workers=1），连接池按并发上限配置：pool_size=10、max_overflow=10
满足全局 max_inflight(16) + 后台任务开销。
"""
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(
    settings.sqlalchemy_url,
    pool_size=10,
    max_overflow=10,
    pool_timeout=10,         # P1-5：池耗尽等待上限。默认 30s 后抛池 TimeoutError → 500 且
                             # 请求挂 30s；降到 10s 快速落 P1-3 的 503（dependency_unavailable），
                             # 让平台容量问题尽快暴露、不长时间占用 worker 连接。
    pool_pre_ping=True,      # 心跳探测，防 MySQL wait_timeout 断连
    pool_recycle=3600,       # 防 NAT/网关侧空闲回收
    echo=False,
)

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：每请求一个 session。"""
    async with SessionLocal() as session:
        yield session
