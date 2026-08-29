"""P1-a 强制改密后端兜底单测（app/api/deps.py）。

- get_current_user（严格版）：password_changed_at 为空 → 403 需先改密（改密前 API 直连被拦）
- get_current_user_allow_change：放行（change-password / me 改密流程端点用）
- 已改密账号（password_changed_at 非空）：正常放行

依赖函数直接调用（fastapi Depends 默认参数不触发），不建测试 app；风格同 test_engine._run。
"""
import asyncio
import datetime
import types

import pytest

from app.api import deps
from app.core.errors import ApiError, E_NEED_CHANGE_PASSWORD


def _user(password_changed_at):
    return types.SimpleNamespace(password_changed_at=password_changed_at)


def test_guard_blocks_when_need_change_password():
    """首登未改密（password_changed_at=None）：严格鉴权拦截，403 + E_NEED_CHANGE_PASSWORD。"""
    with pytest.raises(ApiError) as ei:
        asyncio.run(deps.get_current_user(user=_user(None)))
    assert ei.value.code == E_NEED_CHANGE_PASSWORD
    assert ei.value.status_code == 403
    assert "修改密码" in ei.value.message


def test_guard_allows_when_password_changed():
    """已改密（password_changed_at 非空）：严格鉴权正常放行。"""
    user = _user(datetime.datetime.now(datetime.timezone.utc))
    assert asyncio.run(deps.get_current_user(user=user)) is user


def test_allow_change_passes_when_not_changed():
    """放行版：即使未改密也返回 user（change-password / me 改密流程入口）。"""
    user = _user(None)
    assert asyncio.run(deps.get_current_user_allow_change(user=user)) is user


# 注：require_role 内部依赖 get_current_user（Depends 链），改密拦截在 FastAPI 解析时生效；
# 直接调用不触发 Depends，故不单测继承（deps.py 结构审查可见）。
