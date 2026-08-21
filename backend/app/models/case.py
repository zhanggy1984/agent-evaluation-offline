"""用例集 / 用例 / 版本快照 / 标注 / 场景。"""
from sqlalchemy import (
    Boolean, CHAR, DateTime, Enum, ForeignKey, Index, Integer, JSON, Numeric, String,
    Text, UniqueConstraint, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TestSuite(Base):
    __tablename__ = "test_suite"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agent.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[object] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class TestCase(Base):
    """统一 input 格式 {content,params,file_ref,data_id,conversation_id,seed}。"""
    __tablename__ = "test_case"
    __table_args__ = (
        Index("idx_case_suite", "suite_id"),
        Index("idx_case_interface", "interface_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    suite_id: Mapped[int] = mapped_column(ForeignKey("test_suite.id"), nullable=False)
    interface_id: Mapped[int] = mapped_column(ForeignKey("agent_interface.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    input_type: Mapped[str] = mapped_column(
        Enum("text", "file", "conversation", name="input_type"), nullable=False
    )
    input: Mapped[dict | None] = mapped_column(JSON)
    input_turns: Mapped[dict | None] = mapped_column(JSON)   # conversation: [{"turn":1,"content":"..."}]
    file_ref: Mapped[str | None] = mapped_column(String(128))
    expected: Mapped[dict] = mapped_column(JSON, nullable=False)  # {golden_answer, structure, judge_gold_scores, turn_golden_answers[]}
    assertions: Mapped[list] = mapped_column(JSON, nullable=False)  # 断言数组：[{op, args, dimension}]
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False)   # {dimension: {enabled}}
    status: Mapped[str] = mapped_column(
        Enum("draft", "active", "invalidated", name="case_status"), nullable=False, default="draft"
    )
    is_gold: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_held_out: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    annotation_status: Mapped[str] = mapped_column(
        Enum("draft", "single", "double", "consensus", "disputed", name="annotation_status"),
        nullable=False, default="draft",
    )
    validated_agent_version: Mapped[str | None] = mapped_column(String(64))
    validated_knowledge_version: Mapped[str | None] = mapped_column(String(64))
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[object] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[object] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
    )


class CaseVersion(Base):
    """用例版本快照（变更才快照；weight/threshold 从权重/阈值表冻结拷入）。"""
    __tablename__ = "case_version"
    __table_args__ = (
        UniqueConstraint("case_id", "version_no", name="uk_case_version"),
        Index("idx_case_version_hash", "case_id", "content_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("test_case.id"), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class CaseAnnotation(Base):
    """双人标注（同人同维度不重复标）。"""
    __tablename__ = "case_annotation"
    __table_args__ = (
        UniqueConstraint("case_id", "annotator_id", "dimension_code", name="uk_annotation"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("test_case.id"), nullable=False)
    annotator_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False)
    dimension_code: Mapped[str] = mapped_column(ForeignKey("dimension.code"), nullable=False)
    level: Mapped[float | None] = mapped_column(Numeric(3, 2))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[object] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class SceneCatalog(Base):
    __tablename__ = "scene_catalog"
    __table_args__ = (
        UniqueConstraint("agent_id", "scene_tag", name="uk_scene"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agent.id"), nullable=False)
    scene_tag: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(String(256))


class CaseScene(Base):
    """用例-场景多值关联（agent 归属由 case→suite→agent 推导）。"""
    __tablename__ = "case_scene"

    case_id: Mapped[int] = mapped_column(ForeignKey("test_case.id"), primary_key=True)
    scene_tag: Mapped[str] = mapped_column(String(64), primary_key=True)
