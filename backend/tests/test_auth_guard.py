"""P1-a 强制改密后端兜底单测（app/api/deps.py）。

- get_current_user（严格版）：password_changed_at 为空 → 403 需先改密（改密前 API 直连被拦）
- get_current_user_allow_change：放行（change-password / me 改密流程端点用）
- 已改密账号（password_changed_at 非空）：正常放行

依赖函数直接调用（fastapi Depends 默认参数不触发），不建测试 app；风格同 test_engine._run。
"""
import asyncio
from asyncio_util import run_in_isolated_loop
import datetime
import types

import pytest

from app.api import deps, users
from app.core.errors import ApiError, E_NEED_CHANGE_PASSWORD

# 测试假密码：pre-commit 门禁把字面量凭证赋值当敏感信息拦截，
# 常量名带 TEST_PASSWORD 走白名单通道（仅测试用，非真实凭证）。
TEST_PASSWORD = "newpass123"


def _user(password_changed_at):
    return types.SimpleNamespace(password_changed_at=password_changed_at)


def test_guard_blocks_when_need_change_password():
    """首登未改密（password_changed_at=None）：严格鉴权拦截，403 + E_NEED_CHANGE_PASSWORD。"""
    with pytest.raises(ApiError) as ei:
        run_in_isolated_loop(deps.get_current_user(user=_user(None)))
    assert ei.value.code == E_NEED_CHANGE_PASSWORD
    assert ei.value.status_code == 403
    assert "修改密码" in ei.value.message


def test_guard_allows_when_password_changed():
    """已改密（password_changed_at 非空）：严格鉴权正常放行。"""
    user = _user(datetime.datetime.now(datetime.timezone.utc))
    assert run_in_isolated_loop(deps.get_current_user(user=user)) is user


def test_allow_change_passes_when_not_changed():
    """放行版：即使未改密也返回 user（change-password / me 改密流程入口）。"""
    user = _user(None)
    assert run_in_isolated_loop(deps.get_current_user_allow_change(user=user)) is user


# 注：require_role 内部依赖 get_current_user（Depends 链），改密拦截在 FastAPI 解析时生效；
# 直接调用不触发 Depends，故不单测继承（deps.py 结构审查可见）。


class _FakeUserDB:
    """update_user 用 mock：get→user，execute(RefreshToken 查询)→tokens 列表。"""

    def __init__(self, user, tokens):
        self._user, self._tokens = user, tokens

    async def get(self, model, pk, with_for_update=False):
        return self._user

    async def execute(self, stmt):
        return types.SimpleNamespace(
            scalars=lambda: types.SimpleNamespace(all=lambda: self._tokens))

    async def commit(self):
        pass


@pytest.mark.asyncio
async def test_admin_reset_password_forces_change_and_revokes_tokens(monkeypatch):
    """B1：admin 重置密码 → password_changed_at 置 None（首登强制改密）+ 撤销全部 refresh token。"""
    user = types.SimpleNamespace(
        id=1, username="u1", role="evaluator", enabled=True, password_hash="old",
        password_changed_at=datetime.datetime.now(datetime.timezone.utc))
    tokens = [types.SimpleNamespace(revoked=False), types.SimpleNamespace(revoked=False)]
    db = _FakeUserDB(user, tokens)

    def _hash(pwd):  # hash_password 是同步函数（asyncio.to_thread 线程池执行）
        return "hashed-new"

    monkeypatch.setattr(users, "hash_password", _hash)
    body = users.UserUpdate(role=None, enabled=None, password=TEST_PASSWORD)
    await users.update_user(1, body, types.SimpleNamespace(role="admin"), db)
    assert user.password_hash == "hashed-new"
    assert user.password_changed_at is None       # B1：重置即进强制改密（对齐 create_user）
    assert all(t.revoked for t in tokens)          # B1：旧 refresh 族全部撤销，防换 token 进系统
