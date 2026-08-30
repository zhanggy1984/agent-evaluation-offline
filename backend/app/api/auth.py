"""认证：登录（限速+锁定）/ refresh 轮换（family 复用检测）/ 登出 / 改密 / me。

- 登录限速按「IP+用户名」双维度（内存滑动窗口，单进程部署前提）
- 失败 5 次锁定 15 分钟（锁定时长建议短）
- refresh 轮换 + family_id 复用检测：旧 token 重放 → 整族撤销
- bcrypt 走 run_in_executor（禁阻塞事件循环）
"""
import asyncio
import hashlib
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_allow_change, require_role
from app.core.db import get_db
from app.core.errors import ApiError, E_ACCOUNT_LOCKED, E_TOKEN_INVALID, E_VALIDATION
from app.core.response import ok
from app.core.security import (
    DUMMY_PASSWORD_HASH, create_access_token, hash_password, new_family_id,
    new_token_value, verify_password,
)
from app.models.user import RefreshToken, User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

MAX_FAILED = 5
LOCK_MINUTES = 15
# P2-D19：refresh 有效期——单 token 轮换 7 天；族级绝对过期 30 天（超期必须重新登录，防无限续期）
REFRESH_TOKEN_DAYS = 7
REFRESH_ABSOLUTE_DAYS = 30


class _LoginLimiter:
    """IP+用户名 双维度滑动窗口限速（内存实现，单进程部署适用）。"""

    def __init__(self, window_s: int = 60, max_hits: int = 10):
        self._window = window_s
        self._max = max_hits
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        lst = [t for t in self._hits.get(key, []) if now - t < self._window]
        self._hits[key] = lst
        return len(lst) < self._max

    def hit(self, key: str) -> None:
        self._hits.setdefault(key, []).append(time.monotonic())


_limiter = _LoginLimiter()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def _issue_tokens(db: AsyncSession, user: User) -> dict:
    """发 access + refresh（新 family），refresh 库中只存摘要。"""
    refresh_raw = new_token_value()
    family_id = new_family_id()
    db.add(RefreshToken(
        user_id=user.id,
        token_hash=_sha256(refresh_raw),
        family_id=family_id,
        expires_at=datetime.utcnow() + timedelta(days=REFRESH_TOKEN_DAYS),
    ))
    await db.commit()
    return {
        "access_token": create_access_token(user.id, user.role),
        "refresh_token": refresh_raw,
        "role": user.role,
        "must_change_password": user.password_changed_at is None,
    }


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


@router.post("/login")
async def login(body: LoginBody, request: Request, db: AsyncSession = Depends(get_db)):
    client_ip = request.client.host if request.client else "unknown"
    rate_key = f"{client_ip}:{body.username}"
    if not _limiter.allow(rate_key):
        raise ApiError(E_TOKEN_INVALID, "登录尝试过于频繁，请稍后再试", 429)

    user = (await db.execute(select(User).where(User.username == body.username))).scalar_one_or_none()
    # 锁定比较用 naive UTC：DB 列 DATETIME 读出为 naive，aware now 与其比较必 TypeError
    now = datetime.utcnow()
    if user is not None and user.locked_until and now < user.locked_until:
        raise ApiError(E_ACCOUNT_LOCKED, f"账号已锁定至 {user.locked_until.isoformat()}", 403)

    ok_pwd = False
    if user is not None and user.enabled:
        ok_pwd = await asyncio.to_thread(verify_password, body.password, user.password_hash)
    else:
        # P2-D15：用户不存在/被禁用时也跑一次 dummy bcrypt（cost=12 与真实 hash 同耗时），
        # 抹平「用户名枚举」时序侧信道——所有非成功路径都恰有一次 bcrypt 计算。
        # 结果丢弃：dummy 明文无账号使用，即使 checkpw 碰巧返回 True 也照样走 401。
        await asyncio.to_thread(verify_password, body.password, DUMMY_PASSWORD_HASH)

    if user is None or not ok_pwd:
        if user is not None:
            user.failed_attempts += 1
            if user.failed_attempts >= MAX_FAILED:
                user.locked_until = now + timedelta(minutes=LOCK_MINUTES)
                user.failed_attempts = 0
            await db.commit()
        _limiter.hit(rate_key)
        raise ApiError(E_TOKEN_INVALID, "用户名或密码错误", 401)

    # 登录成功：清零失败计数
    user.failed_attempts = 0
    user.locked_until = None
    await db.commit()
    tokens = await _issue_tokens(db, user)
    logger.info("login ok user=%s ip=%s", user.username, client_ip)
    return ok(tokens)


class RefreshBody(BaseModel):
    refresh_token: str = Field(min_length=10)


@router.post("/refresh")
async def refresh(body: RefreshBody, db: AsyncSession = Depends(get_db)):
    """轮换 refresh：检测旧 token 复用 → 整族撤销。"""
    h = _sha256(body.refresh_token)
    row = (await db.execute(select(RefreshToken).where(RefreshToken.token_hash == h))).scalar_one_or_none()
    # naive UTC：DB 列 DATETIME 读出为 naive，aware now 与其比较必 TypeError（同 login:94 处理）
    now = datetime.utcnow()
    if row is None or row.expires_at < now:
        raise ApiError(E_TOKEN_INVALID, "refresh token 无效或已过期", 401)

    # 复用检测（7.6 A1 修正倒置逻辑）：已撤销 token 被重放 → 整族撤销。
    # 合法二次刷新用的是最新未撤销 token（轮换即撤销旧 token），
    # 不会误判「家族里有其它已撤销 token」为复用 → 不再误撤整族。
    if row.revoked:
        family_rows = (
            await db.execute(select(RefreshToken).where(RefreshToken.family_id == row.family_id))
        ).scalars().all()
        for r in family_rows:
            r.revoked = True
        await db.commit()
        logger.warning("refresh reuse detected, family=%s revoked", row.family_id)
        raise ApiError(E_TOKEN_INVALID, "检测到 token 复用，整族已撤销", 401)

    # P2-D19：族级绝对过期——family 从首次登录建族起最长 REFRESH_ABSOLUTE_DAYS 天，
    # 超期必须重新登录（单 token 7 天可续，族级 30 天硬顶，防泄露 token 无限续期）。
    oldest = (await db.execute(
        select(RefreshToken).where(RefreshToken.family_id == row.family_id)
        .order_by(RefreshToken.created_at.asc()).limit(1)
    )).scalar_one()
    if now - oldest.created_at > timedelta(days=REFRESH_ABSOLUTE_DAYS):
        family_rows = (await db.execute(
            select(RefreshToken).where(RefreshToken.family_id == row.family_id)
        )).scalars().all()
        for r in family_rows:
            r.revoked = True
        await db.commit()
        logger.warning("refresh family 超绝对期, family=%s revoked", row.family_id)
        raise ApiError(E_TOKEN_INVALID, "登录会话已超过最长有效期，请重新登录", 401)

    user = await db.get(User, row.user_id)
    if user is None or not user.enabled:
        raise ApiError(E_TOKEN_INVALID, "账号不可用", 401)

    row.revoked = True
    new_refresh = new_token_value()
    db.add(RefreshToken(
        user_id=row.user_id,
        token_hash=_sha256(new_refresh),
        family_id=row.family_id,  # 同族延续
        expires_at=now + timedelta(days=REFRESH_TOKEN_DAYS),
    ))
    await db.commit()
    return ok({
        "access_token": create_access_token(user.id, user.role),
        "refresh_token": new_refresh,
        "role": user.role,
    })


class LogoutBody(BaseModel):
    refresh_token: str


@router.post("/logout")
async def logout(body: LogoutBody, db: AsyncSession = Depends(get_db)):
    """撤销当前 refresh 族（登出即整族失效）。"""
    h = _sha256(body.refresh_token)
    row = (await db.execute(select(RefreshToken).where(RefreshToken.token_hash == h))).scalar_one_or_none()
    if row is not None:
        family_rows = (
            await db.execute(select(RefreshToken).where(RefreshToken.family_id == row.family_id))
        ).scalars().all()
        for r in family_rows:
            r.revoked = True
        await db.commit()
    return ok()


class ChangePasswordBody(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=128)


@router.post("/change-password")
async def change_password(
    body: ChangePasswordBody,
    user: User = Depends(get_current_user_allow_change),
    db: AsyncSession = Depends(get_db),
):
    if not await asyncio.to_thread(verify_password, body.old_password, user.password_hash):
        raise ApiError(E_VALIDATION, "原密码错误", 400)
    user.password_hash = await asyncio.to_thread(hash_password, body.new_password)
    user.password_changed_at = datetime.now(timezone.utc)
    # 改密后撤销全部 refresh（防旧 token 继续有效）
    rows = (await db.execute(select(RefreshToken).where(RefreshToken.user_id == user.id))).scalars().all()
    for r in rows:
        r.revoked = True
    await db.commit()
    return ok()


@router.get("/me")
async def me(user: User = Depends(get_current_user_allow_change)):
    return ok({
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "must_change_password": user.password_changed_at is None,
    })
