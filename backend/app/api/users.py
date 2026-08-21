"""用户管理（admin）：列表/创建/改角色/禁用。"""
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.core.db import get_db
from app.core.errors import ApiError, E_CONFLICT, E_NOT_FOUND
from app.core.response import ok, page
from app.core.security import hash_password
from app.models.user import ROLE, User

router = APIRouter(prefix="/users", tags=["users"])

AdminOnly = Depends(require_role("admin"))


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    role: str = Field(default="viewer", pattern="^(admin|evaluator|viewer)$")


class UserUpdate(BaseModel):
    role: str | None = Field(default=None, pattern="^(admin|evaluator|viewer)$")
    enabled: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)


@router.get("")
async def list_users(
    page_num: int = 1,
    page_size: int = 20,
    role: str | None = None,
    _: User = AdminOnly,
    db: AsyncSession = Depends(get_db),
):
    page_size = min(max(page_size, 1), 100)
    stmt = select(User)
    if role:
        stmt = stmt.where(User.role == role)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        await db.execute(stmt.order_by(User.id.desc()).offset((page_num - 1) * page_size).limit(page_size))
    ).scalars().all()
    return ok(page(
        [{"id": u.id, "username": u.username, "role": u.role, "enabled": u.enabled} for u in rows],
        total, page_num, page_size,
    ))


@router.post("")
async def create_user(body: UserCreate, _: User = AdminOnly, db: AsyncSession = Depends(get_db)):
    exists = (await db.execute(select(User.id).where(User.username == body.username))).first()
    if exists:
        raise ApiError(E_CONFLICT, "用户名已存在", 409)
    user = User(
        username=body.username,
        password_hash=await asyncio.to_thread(hash_password, body.password),
        role=body.role,
    )
    db.add(user)
    await db.commit()
    return ok({"id": user.id, "username": user.username, "role": user.role})


@router.put("/{user_id}")
async def update_user(
    user_id: int,
    body: UserUpdate,
    _: User = AdminOnly,
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, user_id)
    if user is None:
        raise ApiError(E_NOT_FOUND, "用户不存在", 404)
    if body.role is not None:
        user.role = body.role
    if body.enabled is not None:
        user.enabled = body.enabled
    if body.password:
        user.password_hash = await asyncio.to_thread(hash_password, body.password)
        user.password_changed_at = datetime.now(timezone.utc)
    await db.commit()
    return ok({"id": user.id, "username": user.username, "role": user.role, "enabled": user.enabled})


@router.delete("/{user_id}")
async def disable_user(user_id: int, _: User = AdminOnly, db: AsyncSession = Depends(get_db)):
    """软禁用（不物理删）。"""
    user = await db.get(User, user_id)
    if user is None:
        raise ApiError(E_NOT_FOUND, "用户不存在", 404)
    user.enabled = False
    await db.commit()
    return ok()
