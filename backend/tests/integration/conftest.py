"""7.2 容器集成测试公共设施（真库 + aiomysql + pytest-asyncio）。

默认不连库：未设 RUN_INTEGRATION=1 时整目录 skip（全量回归不碰库、也不依赖 socat
转发容器）；显式设 RUN_INTEGRATION=1 才连真库跑。

宿主 Windows 运行（7.8 起唯一运行路径）：
- **容器内无 pytest**：Dockerfile 只装 requirements.txt（无 pytest/pytest-asyncio），
  docker exec python -m pytest → No module named pytest。「容器装 1.4.0」历史方案
  从未落地（T219），integration 只在宿主跑，单一路径 = 宿主 pytest-asyncio 0.24。
- 宿主连库需 socat 转发（Windows 无法直达 docker bridge 容器 IP）：临时容器
  temp-mysql-fwd 把 127.0.0.1:3307 转发到 ai-eval-mysql:3306；跑前设
  DB_HOST=127.0.0.1 DB_PORT=3307 DB_PASSWORD=<mysql 密码>。
  创建命令：`docker run -d --name temp-mysql-fwd --network ai-evaluation_app
  -p 127.0.0.1:3307:3306 alpine/socat tcp-listen:3306,fork,reuseaddr
  tcp-connect:ai-eval-mysql:3306`
- pytest-asyncio 0.24 的 `_get_marked_loop_scope` 对无参 `@pytest.mark.asyncio`
  硬编码返回 "function"（不读 pytest.ini 的 asyncio_default_test_loop_scope），
  导致测试跑 function loop、async fixture（asyncio_default_fixture_loop_scope=session）
  跑 session loop——SQLAlchemy 连接绑定 fixture 的 session loop，测试 body 复用连接时
  checkout 跨 loop 报 "Future attached to a different loop"。解法：**每个测试显式
  `@pytest.mark.asyncio(loop_scope="session")`**，与 session 级 async fixture 同 loop。
  （曾用全局 monkeypatch `_get_marked_loop_scope` 强制 session，但会污染非 integration
  的 asyncio 测试——它们依赖 function loop 的独立 event_loop，被强制 session 后撞
  "There is no current event loop in thread"（全量 pytest 7 failed 实证）。显式 marker
  作用域天然限定在本目录。实测 Windows 默认 Proactor 单 loop 下 aiomysql 兼容正常，
  无需切 Selector。）
- db session fixture + env 数据容器 fixture（it72-* 跑完自动清理，不留残库）。
"""
import os

import pytest

# 未设 RUN_INTEGRATION=1 时，收集后跳过本目录全部测试（全量回归不碰库、不依赖转发容器）。
# 用 modifyitems 而非模块级 pytest.skip：后者在 conftest 里只统计 1 skipped，24 个测试
# 应显示 24 skipped 才清晰。
def pytest_collection_modifyitems(config, items):
    if os.getenv("RUN_INTEGRATION"):
        return
    for item in items:
        if os.sep + "integration" + os.sep in str(item.fspath):
            item.add_marker(pytest.mark.skip(reason="integration 需显式 RUN_INTEGRATION=1 才连真库"))

pytest.importorskip("aiomysql")  # 宿主无 aiomysql → 本目录全部 skip

from pytest_asyncio import fixture as async_fixture  # noqa: E402

from app.core.db import SessionLocal  # noqa: E402
from app.core.limiter import KeyedLimiter  # noqa: E402
from app.runner.orchestrator import orchestrator  # noqa: E402
from helpers import Env  # noqa: E402

@async_fixture(autouse=True)
async def _reset_orchestrator():
    """orchestrator 单例每测试前重置（熔断/限流/取消/心跳零残留）。

    7.6 C3 熔断 DB 化：进程内 _breakers 已删除，跨测试隔离改为清空 agent_circuit 表
    （熔断状态现在存 DB，内存单例清零无意义）。
    """
    from sqlalchemy import delete  # noqa: E402

    from app.models import AgentCircuit  # noqa: E402

    async with SessionLocal() as s:
        await s.execute(delete(AgentCircuit))
        await s.commit()
    orchestrator._cancel.clear()
    for t in orchestrator._heartbeats.values():
        t.cancel()
    orchestrator._heartbeats.clear()
    orchestrator._limiter = KeyedLimiter(16, 3)
    orchestrator._run_limits.clear()  # 7.8 前置④ per-run 桶零残留
    yield
    for t in orchestrator._heartbeats.values():
        t.cancel()
    orchestrator._heartbeats.clear()


@async_fixture
async def db():
    """async DB session（SQLAlchemy async，容器内可用）。"""
    async with SessionLocal() as s:
        yield s


@async_fixture
async def env(db):
    """it72-* 数据容器；测试结束自动逆序清理（cleanup 自建独立 session，不受测试 session 状态影响）。"""
    e = Env()
    yield e
    await e.cleanup()
