"""7.6 安全修复集成测试（容器真库）：A1 refresh family 复用检测。

直接调用 API 路由函数（绕过 HTTP 层，用真 DB session），聚焦业务分支逻辑：
- 合法二次刷新（家族含历史已撤销 token）不再误判复用 → 正常轮换（修复倒置逻辑）
- 已撤销 token 重放 → 整族撤销（含家族内其它未撤销 token）

A2 /failures viewer 裁剪、A5 安全头、SSRF/reset/文件/凭证等由 verify_sec76.py 端到端覆盖。
"""
import pytest

pytest.importorskip("aiomysql")

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.api import auth as auth_mod
from app.core.db import SessionLocal
from app.core.errors import ApiError
from app.models import User
from app.models.user import RefreshToken


async def _seed_family(db, user_id):
    """造两代同族 refresh：t1 已撤销（历史上用过的旧 token）、t2 当前有效。"""
    fam = auth_mod.new_family_id()
    now = datetime.now(timezone.utc)
    t1 = auth_mod.new_token_value()
    t2 = auth_mod.new_token_value()
    db.add_all([
        RefreshToken(user_id=user_id, token_hash=auth_mod._sha256(t1), family_id=fam,
                     expires_at=now + timedelta(days=7), revoked=True),
        RefreshToken(user_id=user_id, token_hash=auth_mod._sha256(t2), family_id=fam,
                     expires_at=now + timedelta(days=7), revoked=False),
    ])
    await db.commit()
    return fam, t1, t2


async def _drop_family(db, fam):
    await db.execute(delete(RefreshToken).where(RefreshToken.family_id == fam))
    await db.commit()


async def _admin_id():
    async with SessionLocal() as s:
        return (await s.execute(select(User.id).where(User.username == "admin"))).scalar_one()


@pytest.mark.asyncio(loop_scope="session")
async def test_second_refresh_ok_no_false_reuse(db):
    """A1 回归：家族有历史已撤销 token 时，用当前有效 token 二次刷新不被误判复用。"""
    uid = await _admin_id()
    fam, t1, t2 = await _seed_family(db, uid)
    try:
        resp = await auth_mod.refresh(auth_mod.RefreshBody(refresh_token=t2), db)
        assert resp["data"]["refresh_token"], "应正常轮换返回新 token"
    finally:
        await _drop_family(db, fam)


@pytest.mark.asyncio(loop_scope="session")
async def test_replay_revoked_token_revokes_family(db):
    """A1：已撤销 token 被重放 → 整族撤销（含家族内未撤销的其它 token）。"""
    uid = await _admin_id()
    fam, t1, t2 = await _seed_family(db, uid)
    try:
        with pytest.raises(ApiError):
            await auth_mod.refresh(auth_mod.RefreshBody(refresh_token=t1), db)
        rows = (await db.execute(select(RefreshToken).where(
            RefreshToken.family_id == fam))).scalars().all()
        assert rows and all(r.revoked for r in rows), "重放后整族应全部撤销"
    finally:
        await _drop_family(db, fam)


@pytest.mark.asyncio(loop_scope="session")
async def test_refresh_db_roundtrip_naive_expiry():
    """回归：expires_at 经 MySQL 落库读回为 naive，refresh 内比较不得抛 TypeError。

    上面两个测试用同一 session 提交，identity map 命中 aware 值，掩盖了 naive/aware 比较 bug
    （HTTP 路径新连接读回 naive 必炸：7.6 s2 曾在此 500「can't compare offset-naive and
    offset-aware datetimes」）。本测试强制换连接读回，复现真实路径。
    """
    uid = await _admin_id()
    fam = auth_mod.new_family_id()
    t2 = auth_mod.new_token_value()
    async with SessionLocal() as s0:
        s0.add(RefreshToken(user_id=uid, token_hash=auth_mod._sha256(t2), family_id=fam,
                            expires_at=datetime.now(timezone.utc) + timedelta(days=7), revoked=False))
        await s0.commit()
    try:
        async with SessionLocal() as s1:  # 新连接：expires_at 必为 DB 读回的 naive
            resp = await auth_mod.refresh(auth_mod.RefreshBody(refresh_token=t2), s1)
        assert resp["data"]["refresh_token"], "DB round-trip 后 refresh 应正常轮换（不得 500）"
    finally:
        async with SessionLocal() as sd:
            await sd.execute(delete(RefreshToken).where(RefreshToken.family_id == fam))
            await sd.commit()


@pytest.mark.asyncio(loop_scope="session")
async def test_refresh_family_absolute_expiry():
    """P2-D19：族级绝对过期——单个 token 仍有效（未过期未撤销）但建族已超 30 天 → 401 + 整族撤销。

    换连接读回 naive created_at（同 naive expiry 教训：identity map 命中 aware 值会掩盖
    D19 的 `now - created_at` naive/aware 比较）。
    """
    uid = await _admin_id()
    fam = auth_mod.new_family_id()
    t = auth_mod.new_token_value()
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=31)  # 建族超绝对期；单 token 本身未过期（expires_at 未来）
    async with SessionLocal() as s0:
        s0.add(RefreshToken(user_id=uid, token_hash=auth_mod._sha256(t), family_id=fam,
                            expires_at=now + timedelta(days=7), revoked=False, created_at=old))
        await s0.commit()
    try:
        async with SessionLocal() as s1:
            with pytest.raises(ApiError) as ei:
                await auth_mod.refresh(auth_mod.RefreshBody(refresh_token=t), s1)
        assert ei.value.status_code == 401
        assert "最长有效期" in ei.value.message, "应提示重新登录，而非 token 过期"
        async with SessionLocal() as s2:
            rows = (await s2.execute(select(RefreshToken).where(
                RefreshToken.family_id == fam))).scalars().all()
            assert rows and all(r.revoked for r in rows), "超绝对期后整族应全部撤销"
    finally:
        async with SessionLocal() as sd:
            await sd.execute(delete(RefreshToken).where(RefreshToken.family_id == fam))
            await sd.commit()
