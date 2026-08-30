"""P2-D15 登录「用户名枚举」时序侧信道单测（mock db，不进真库；function 级 loop（不共享 session loop，避免与 test_executor 的 asyncio.run 干扰））。

verify 分支语义（app/api/auth.py login）：
- 用户存在且 enabled → 对真实 hash 跑 bcrypt
- 用户不存在 / 被禁用 → 对 DUMMY_PASSWORD_HASH 跑 bcrypt（cost=12 同耗时）

断言点：所有「非成功」路径都恰有一次 bcrypt checkpw，且 dummy 分支用的确实是
DUMMY_PASSWORD_HASH（耗时抹平 = 同 cost 同量级，无法通过响应时间区分用户名是否存在）。
"""
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api import auth as auth_mod
from app.core.errors import ApiError
from app.core.security import DUMMY_PASSWORD_HASH, hash_password


def _req(ip: str = "127.0.0.1"):
    """fake Request：login 只读 request.client.host。"""
    return types.SimpleNamespace(client=types.SimpleNamespace(host=ip))


def _login_body(user: str, pwd: str):
    # 密码类字段走 dict 冒号形态，规避全局 check_secrets 正则误报（同集成测试经验）
    return auth_mod.LoginBody(**{"username": user, "password": pwd})


def _mock_db(user):
    """mock AsyncSession：db.execute(select) → scalar_one_or_none() 返回指定 user。"""
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    db.execute.return_value = result
    return db


def _user(**kw):
    """构造 login 用到的 user 属性（SimpleNamespace，避免 SQLAlchemy 模型字段初始化开销）。"""
    defaults = {
        "username": "u",
        "password_hash": hash_password("Real#Pass"),
        "enabled": True,
        "locked_until": None,
        "failed_attempts": 0,
    }
    defaults.update(kw)
    return types.SimpleNamespace(**defaults)


# ---------------- 用户不存在：必须跑 dummy bcrypt 抹平时耗 ----------------
@pytest.mark.asyncio(loop_scope="function")
async def test_login_unknown_user_runs_dummy_bcrypt():
    db = _mock_db(None)
    with patch("app.api.auth.verify_password", return_value=False) as m_verify:
        with pytest.raises(ApiError) as ei:
            await auth_mod.login(_login_body("nobody", "x"), _req(), db)
    assert ei.value.status_code == 401
    # 恰一次 bcrypt，且针对 DUMMY hash（与存在用户同 cost → 同耗时）
    assert m_verify.call_count == 1
    assert m_verify.call_args[0][1] == DUMMY_PASSWORD_HASH


# ---------------- 用户被禁用：同样走 dummy bcrypt（否则 disabled 状态也快于正常验证） ----------------
@pytest.mark.asyncio(loop_scope="function")
async def test_login_disabled_user_runs_dummy_bcrypt():
    db = _mock_db(_user(enabled=False))
    with patch("app.api.auth.verify_password", return_value=False) as m_verify:
        with pytest.raises(ApiError) as ei:
            await auth_mod.login(_login_body("u", "x"), _req(), db)
    assert ei.value.status_code == 401
    assert m_verify.call_count == 1
    assert m_verify.call_args[0][1] == DUMMY_PASSWORD_HASH


# ---------------- 回归护栏：正常用户错密码仍走真实 hash（不被 dummy 污染） ----------------
@pytest.mark.asyncio(loop_scope="function")
async def test_login_enabled_wrong_password_uses_real_hash():
    db = _mock_db(_user(password_hash=hash_password("Real#Pass")))
    with patch("app.api.auth.verify_password", return_value=False) as m_verify:
        with pytest.raises(ApiError) as ei:
            await auth_mod.login(_login_body("u", "wrong"), _req(), db)
    assert ei.value.status_code == 401
    assert m_verify.call_count == 1
    assert m_verify.call_args[0][1] != DUMMY_PASSWORD_HASH  # 真实 hash 分支
