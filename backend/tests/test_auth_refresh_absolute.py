"""P2-D19 refresh token 族级绝对过期上限单测（mock db，不进真库；function 级 loop）。

问题背景（auth.py refresh）：每次轮换 expires_at = now + 7 天，只要每 7 天续一次，
refresh family 可无限延续——泄露 token 靠持续轮换永久有效。
修复：refresh 时查同 family 最早 token 的 created_at（= 首次登录建族时刻），
若 now - family_created_at > REFRESH_ABSOLUTE_DAYS（30 天）→ 整族撤销 + 401 必须重新登录。

断言点：
- 超绝对期 → 401 E_TOKEN_INVALID + 整族 revoked + commit
- 绝对期内 → 正常轮换返回新 refresh_token（旧 token 置 revoked）
"""
import types
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api import auth as auth_mod
from app.core.errors import ApiError, E_TOKEN_INVALID


def _token(**kw):
    """构造 refresh 用到的 RefreshToken 属性（SimpleNamespace，避免 ORM 初始化开销）。"""
    now = datetime.utcnow()
    defaults = {
        "user_id": 1,
        "token_hash": "x" * 64,
        "family_id": "fam-1",
        "expires_at": now + timedelta(days=auth_mod.REFRESH_TOKEN_DAYS),
        "revoked": False,
        "created_at": now,
    }
    defaults.update(kw)
    return types.SimpleNamespace(**defaults)


def _make_db(row, oldest, family_rows=None):
    """mock AsyncSession：execute 按 refresh 内调用顺序返回 row → oldest →（超期时）family_rows。"""
    db = AsyncMock()
    results = [
        MagicMock(scalar_one_or_none=lambda: row),
        MagicMock(scalar_one=lambda: oldest),
    ]
    if family_rows is not None:
        results.append(MagicMock(scalars=lambda: MagicMock(all=lambda: family_rows)))
    db.execute.side_effect = results
    db.add = MagicMock()  # 真实 AsyncSession.add 是同步方法（AsyncMock 默认 async 会返未 await 协程）
    db.get = AsyncMock(return_value=types.SimpleNamespace(id=1, enabled=True, role="admin"))
    return db


# ---------------- 超绝对期：整族撤销 + 401 ----------------
@pytest.mark.asyncio(loop_scope="function")
async def test_refresh_family_absolute_expired_revokes_all():
    old_created = datetime.utcnow() - timedelta(days=31)
    current = _token()
    family_rows = [_token(created_at=old_created), _token(created_at=old_created, revoked=True)]
    db = _make_db(current, family_rows[0], family_rows)

    with pytest.raises(ApiError) as ei:
        await auth_mod.refresh(auth_mod.RefreshBody(refresh_token=current.token_hash), db)

    assert ei.value.status_code == 401
    assert ei.value.code == E_TOKEN_INVALID
    assert "最长有效期" in ei.value.message
    # 超期语义与复用检测一致：整族撤销，不留半死 token
    assert all(r.revoked for r in family_rows), "超期后整族应全部 revoked"
    db.commit.assert_awaited_once()


# ---------------- 绝对期内：正常轮换 ----------------
@pytest.mark.asyncio(loop_scope="function")
async def test_refresh_within_absolute_window_rotates():
    recent_created = datetime.utcnow() - timedelta(days=5)
    current = _token(created_at=recent_created)
    db = _make_db(current, current)

    resp = await auth_mod.refresh(auth_mod.RefreshBody(refresh_token=current.token_hash), db)

    assert resp["data"]["refresh_token"], "绝对期内应正常轮换返回新 token"
    assert current.revoked is True, "旧 token 轮换后应置 revoked"
