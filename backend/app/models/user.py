"""用户 / 刷新令牌。"""
from sqlalchemy import (
    CHAR, Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

ROLE = ("admin", "evaluator", "viewer")


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(Enum(*ROLE, name="user_role"), nullable=False, default="viewer")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[object | None] = mapped_column(DateTime)
    password_changed_at: Mapped[object | None] = mapped_column(DateTime)
    created_at: Mapped[object] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class RefreshToken(Base):
    """refresh 轮换（family_id 检测旧 token 复用 → 整族撤销）。"""
    __tablename__ = "refresh_token"
    __table_args__ = (
        Index("idx_refresh_user", "user_id"),
        Index("idx_refresh_family", "family_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    family_id: Mapped[str] = mapped_column(CHAR(36), nullable=False)
    expires_at: Mapped[object] = mapped_column(DateTime, nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[object] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
