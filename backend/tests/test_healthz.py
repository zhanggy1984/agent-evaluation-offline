"""P2-D14：/healthz 探 DB 语义单测。

healthz 不再是无状态探活，而是探共享 infra MySQL（SELECT 1）：
- DB 可达 → 200 {"status":"ok"}
- DB 不可达 → 503 {"status":"degraded"}（就绪语义，Docker HEALTHCHECK 标 unhealthy 但容器不 crash-loop）

直接调用 app.main.healthz 协程（不进 TestClient，避免起全量 app 中间件/依赖），
mock app.main.SessionLocal 的 async 上下文管理器。
"""
import json

import pytest
from unittest.mock import AsyncMock, patch

from app.main import healthz


def _mock_session(cm_exit=None):
    """构造 mock 的 async 上下文管理器（模拟 `async with SessionLocal() as db:`）。"""
    session = AsyncMock()
    session.__aexit__.return_value = cm_exit
    return session


@pytest.mark.asyncio(loop_scope="session")
async def test_healthz_db_ok():
    # DB 可达：__aenter__ 返回的 db.execute(SELECT 1) 正常完成
    session = _mock_session()
    session.__aenter__.return_value = AsyncMock()

    with patch("app.main.SessionLocal", return_value=session):
        resp = await healthz()

    assert resp == {"status": "ok"}
    session.__aenter__.return_value.execute.assert_awaited_once()


@pytest.mark.asyncio(loop_scope="session")
async def test_healthz_db_unreachable_503():
    # DB 不可达：进入 async with 时抛异常 → except 兜底 → 503 degraded
    session = _mock_session()
    session.__aenter__.side_effect = RuntimeError("db down")

    with patch("app.main.SessionLocal", return_value=session):
        resp = await healthz()

    assert resp.status_code == 503
    assert json.loads(resp.body)["status"] == "degraded"


def test_backup_script_exists():
    """P2-D14：备份脚本存在性校验（幂等脚本实跑验证在 B 级自测完成，这里只保仓库结构不破）。"""
    import os

    # tests/ 在 backend/tests/ 下，脚本在仓库根 scripts/ 下
    repo_root = os.path.join(os.path.dirname(__file__), "..", "..")
    script = os.path.join(repo_root, "scripts", "backup_db.ps1")
    assert os.path.isfile(script), f"backup_db.ps1 缺失：{script}"
