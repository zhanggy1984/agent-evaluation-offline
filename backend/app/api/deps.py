"""FastAPI 依赖：鉴权 / RBAC / 对象级校验（§四-4.5 数据可见性）。

- 每请求查 DB 角色（不信任 JWT 内角色，§九-3）
- 对象级：留出集 owner 不可见、职责分离（owner 禁标自己 agent 的 golden/断言）、viewer 裁剪
"""
from datetime import datetime, timezone

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.errors import ApiError, E_ACCOUNT_LOCKED, E_NO_PERMISSION, E_TOKEN_INVALID
from app.core.security import decode_access_token
from app.models.user import User

_bearer = HTTPBearer(auto_error=False)

ROLE_ADMIN = "admin"
ROLE_EVALUATOR = "evaluator"
ROLE_VIEWER = "viewer"


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None:
        raise ApiError(E_TOKEN_INVALID, "未登录", 401)
    payload = decode_access_token(credentials.credentials)
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        raise ApiError(E_TOKEN_INVALID, "token 载荷非法", 401)
    user = await db.get(User, user_id)  # 每请求查 DB（角色/enabled 实时）
    if user is None or not user.enabled:
        raise ApiError(E_TOKEN_INVALID, "账号不存在或已禁用", 401)
    if user.locked_until and datetime.now(timezone.utc) < user.locked_until.replace(tzinfo=timezone.utc):
        raise ApiError(E_ACCOUNT_LOCKED, "账号锁定", 403)
    return user


def require_role(*roles: str):
    """依赖工厂：要求当前用户属于 roles 之一。"""

    async def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise ApiError(E_NO_PERMISSION, f"需要角色 {'/'.join(roles)}", 403)
        return user

    return _dep


async def require_owner_or_qa(agent_owner_id: int | None, user: User) -> None:
    """职责分离：owner 可管理自己 agent 的普通字段；golden_answer/assertions 由 admin 或独立 QA 标。
    owner_id==当前用户时，禁止标/改该 agent 的 golden_answer/assertions（由字段级权限在 case 层调用）。
    """
    # 由用例更新接口按字段级权限调用；此处仅提供判定 helper
    if user.role == ROLE_ADMIN:
        return
    if agent_owner_id is not None and agent_owner_id == user.id:
        raise ApiError(E_NO_PERMISSION, "owner 不可标/改自己 agent 的 golden_answer/assertions", 403)


def viewer_sees_evidence(user: User) -> bool:
    """viewer 可见性：L4 证据（reasoning 原文/judge 理由/tool_calls/source_detail）viewer 不可达。"""
    return user.role != ROLE_VIEWER


def is_held_out_visible(trigger_type: str, current_user: User, agent_owner_id: int | None) -> bool:
    """留出集可见性：owner 不可见 held-out 用例与结果（§四-4.5）。"""
    if trigger_type == "held_out" and agent_owner_id == current_user.id:
        return False
    return True
