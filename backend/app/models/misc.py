"""系统配置 / 导出令牌 / 审计日志 / 熔断器。（issue 系统与告警通知已随轻量化删除。）"""
from sqlalchemy import (
    Boolean, CHAR, DateTime, Enum, Float, ForeignKey, Index, Integer, JSON, String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

CONFIG_SCOPE = ("run", "global", "registration")


class SystemConfig(Base):
    """配置项清单（scope/is_hot 语义见 solution_detail §七）。"""
    __tablename__ = "system_config"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, nullable=False)
    scope: Mapped[str] = mapped_column(
        Enum(*CONFIG_SCOPE, name="config_scope"), nullable=False, default="global"
    )
    is_hot: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[object] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
    )


class ExportToken(Base):
    """报告导出一次性下载令牌（随 run 清理，ON DELETE CASCADE）。"""
    __tablename__ = "export_token"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    run_id: Mapped[int] = mapped_column(ForeignKey("eval_run.id", ondelete="CASCADE"), nullable=False)
    format: Mapped[str] = mapped_column(Enum("pdf", "xlsx", name="export_format"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[object] = mapped_column(DateTime, nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[object] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class AuditLog(Base):
    """审计日志（报告导出 / 凭证查看 / 敏感操作）。"""
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("idx_audit_user", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict | None] = mapped_column(JSON)
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[object] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class AgentCircuit(Base):
    """7.6 C3 熔断器跨 worker 持久化：agent 级一行，存状态机快照。

    state 用 String 直存（closed/open/half_open），opened_at 为 wall clock epoch 秒
    （circuit_breaker 已统一 time.time 基准，跨进程/重启可比）。
    无锁读-改-写（last-write-wins）：计数偏差 ±1 不破坏「拦截/恢复」不变量，
    且 per_agent 并发本就被 limiter 限制，多 worker 下最坏多放行几个探针（soft limit）。
    """
    __tablename__ = "agent_circuit"

    agent_id: Mapped[int] = mapped_column(
        ForeignKey("agent.id", ondelete="CASCADE"), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="closed")
    failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # opened_at 必须 DOUBLE：MySQL FLOAT(单精度) 对 epoch 秒(1.7e9) 精度丢失（1787201704 → 1787200000），
    # 误差超 1700s 会让熔断误判冷却已到、跨 run 放行探针（7.6 C3 集成测试抓出的 bug）
    opened_at: Mapped[float | None] = mapped_column(Float(precision=53))
    probe_inflight: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[object] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
    )
