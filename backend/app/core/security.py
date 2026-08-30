"""安全基座：MultiFernet / JWT(HS256) / bcrypt（§九）。

- MultiFernet 多代密钥：首代加密，其余代仅解密（密钥轮换）
- JWT 硬编码 algorithms=["HS256"]，显式拒绝 alg=none/其他
- bcrypt cost=12；哈希为 CPU 密集，调用方必须走 run_in_executor（禁阻塞事件循环）
"""
import secrets
import time
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt as pyjwt
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import settings
from app.core.errors import ApiError, E_TOKEN_INVALID

_fernet: MultiFernet | None = None


def get_fernet() -> MultiFernet:
    global _fernet
    if _fernet is None:
        _fernet = MultiFernet([Fernet(k.encode("utf-8")) for k in settings.fernet_key_list])
    return _fernet


def fernet_encrypt(data: bytes) -> bytes:
    """认证加密（Fernet），密钥环境变量注入。"""
    return get_fernet().encrypt(data)


def fernet_decrypt(token: bytes) -> bytes:
    try:
        return get_fernet().decrypt(token)
    except InvalidToken:
        raise ApiError(E_TOKEN_INVALID, "凭证解密失败")


# ---------------- 密码 ----------------
# P2-D15：固定 dummy bcrypt hash（cost=12，与真实 hash 同耗时）。
# 登录时用户不存在/被禁用也会对它的 dummy hash 跑一次 checkpw，抹平「用户名枚举」
# 时序侧信道（存在用户要 ~100-250ms，不存在瞬间返回）。明文是占位符，无任何账号使用。
DUMMY_PASSWORD_HASH = "$2b$12$/LSL0FMQ.ObmrphY/K31J.IDnnodSRGpx4xjkAPALMhY9.IiOgMQ."


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


# ---------------- JWT（HS256 硬编码） ----------------
_ALGORITHMS = ["HS256"]


def create_access_token(user_id: int, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "role": role,  # 仅作 JWT 载荷，权限判定仍每请求查 DB（§九-3）
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_access_minutes),
    }
    return pyjwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    try:
        payload = pyjwt.decode(token, settings.jwt_secret, algorithms=_ALGORITHMS)
    except pyjwt.ExpiredSignatureError:
        raise ApiError(E_TOKEN_INVALID, "token 已过期", 401)
    except Exception:
        raise ApiError(E_TOKEN_INVALID, "token 无效", 401)
    if payload.get("type") != "access":
        raise ApiError(E_TOKEN_INVALID, "token 类型错误", 401)
    return payload


def new_family_id() -> str:
    return secrets.token_hex(16)  # 32 hex 字符，作为 family_id（CHAR(36) 内）


def new_token_value() -> str:
    """随机 refresh token 原文（库中只存 sha256 摘要）。"""
    return secrets.token_urlsafe(48)
