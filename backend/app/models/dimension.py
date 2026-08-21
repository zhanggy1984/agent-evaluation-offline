"""维度 / 权重 / 阈值 / 价格 / rubric / 插件注册表。"""
from sqlalchemy import (
    Boolean, DateTime, Enum, ForeignKey, Integer, JSON, Numeric, PrimaryKeyConstraint,
    String, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Dimension(Base):
    """评测维度主表（六处引用其 code，防拼写漂移）。"""
    __tablename__ = "dimension"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(
        Enum("accuracy", "performance", "cost", name="dimension_category"), nullable=False
    )


class AgentDimensionWeight(Base):
    """权重配置（agent 级单一事实来源；interface_id=0 表示 agent 默认）。"""
    __tablename__ = "agent_dimension_weight"
    __table_args__ = (
        PrimaryKeyConstraint("agent_id", "interface_id", "dimension_code"),
    )

    agent_id: Mapped[int] = mapped_column(ForeignKey("agent.id"), nullable=False)
    interface_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dimension_code: Mapped[str] = mapped_column(ForeignKey("dimension.code"), nullable=False)
    weight: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)


class BaselineTarget(Base):
    """达标分（门禁阈值）。interface_id=0 为哨兵（非外键）；approved 才参与快照。"""
    __tablename__ = "baseline_target"
    __table_args__ = (
        PrimaryKeyConstraint("agent_id", "interface_id", "dimension_code"),
    )

    agent_id: Mapped[int] = mapped_column(ForeignKey("agent.id"), nullable=False)
    interface_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dimension_code: Mapped[str] = mapped_column(ForeignKey("dimension.code"), nullable=False)
    target_score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    calibration_source: Mapped[str | None] = mapped_column(String(256))
    approved_by_1: Mapped[int | None] = mapped_column(Integer)
    approved_by_2: Mapped[int | None] = mapped_column(Integer)
    approval_status: Mapped[str] = mapped_column(
        Enum("auto", "pending_approval", "approved", name="baseline_approval_status"),
        nullable=False, default="auto",
    )


class ModelPrice(Base):
    """模型单价（成本计算数据源）。"""
    __tablename__ = "model_price"
    __table_args__ = (
        PrimaryKeyConstraint("model", "effective_from"),
    )

    model: Mapped[str] = mapped_column(String(64), nullable=False)
    input_price: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False)
    output_price: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False)
    cache_hit_price: Mapped[float | None] = mapped_column(Numeric(12, 6))
    effective_from: Mapped[object] = mapped_column(DateTime, nullable=False)


class JudgeRubric(Base):
    """judge rubric（interface_id=0 通用；非 0 覆盖）。"""
    __tablename__ = "judge_rubric"
    __table_args__ = (
        UniqueConstraint("dimension_code", "interface_id", "version", name="uk_rubric"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dimension_code: Mapped[str] = mapped_column(ForeignKey("dimension.code"), nullable=False)
    interface_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[str] = mapped_column(String(16), nullable=False)
    template: Mapped[dict] = mapped_column(JSON, nullable=False)


class MetricDef(Base):
    """维度插件注册（class_path 白名单索引）。"""
    __tablename__ = "metric_def"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dimension_code: Mapped[str] = mapped_column(ForeignKey("dimension.code"), nullable=False)
    class_path: Mapped[str] = mapped_column(String(256), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class AssertionOpDef(Base):
    """断言算子注册（自定义算子仅 admin，class_path 白名单校验）。"""
    __tablename__ = "assertion_op_def"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    op: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    class_path: Mapped[str] = mapped_column(String(256), nullable=False)
