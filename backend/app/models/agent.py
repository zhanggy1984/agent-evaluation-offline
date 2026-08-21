"""被评 agent 与评测接口。"""
from sqlalchemy import (
    Boolean, DateTime, Enum, ForeignKey, Index, Integer, JSON, String, VARBINARY, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Agent(Base):
    """被评对象。base_url 出站唯一来源在 adapter_config；本列仅注册/改时内网白名单校验。"""
    __tablename__ = "agent"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    adapter_type: Mapped[str] = mapped_column(String(128), nullable=False)  # 硬编码白名单（constants）
    adapter_config: Mapped[dict | None] = mapped_column(JSON)               # 声明式字段映射（含 reset 配置）
    auth_config: Mapped[bytes | None] = mapped_column(VARBINARY(4096))      # 预留：Fernet 加密凭证（评测环境关鉴权留空）
    contract_version: Mapped[str | None] = mapped_column(String(32))
    owner_id: Mapped[int | None] = mapped_column(Integer)                   # 负责人（evaluator），无 FK（见 DDL）
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[object] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class AgentInterface(Base):
    """评测接口（一个 agent 多个 LLM 接口）。"""
    __tablename__ = "agent_interface"
    __table_args__ = (
        Index("idx_interface_agent", "agent_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agent.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    method: Mapped[str] = mapped_column(String(8), nullable=False, default="POST")
    contract_type: Mapped[str] = mapped_column(
        Enum("sse", "sync", name="contract_type"), nullable=False
    )
    contract_version: Mapped[str] = mapped_column(String(32), nullable=False)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
