"""P1-5：DB 连接池配置断言。

create_async_engine 惰性建池（不真连库，conftest 兜底注入 sqlalchemy_url），
单测直接查池配置。pool_timeout=10 是 P1-3 的配套：池耗尽默认阻塞 30s 后抛
池 TimeoutError → 500 且请求挂 30s；降到 10s 快速落 503（dependency_unavailable）。
"""
from app.core import db


def test_db_pool_timeout_10s():
    # 池耗尽等待上限必须显式配置（默认 30s 对并发评测请求太长）
    assert db.engine.pool.timeout() == 10


def test_db_pool_size_unchanged():
    # 回归护栏：并发容量配置不被本次改动破坏
    pool = db.engine.pool
    assert pool.size() == 10  # pool_size
    assert pool._max_overflow == 10
    assert pool._pre_ping is True
    assert pool._recycle == 3600
