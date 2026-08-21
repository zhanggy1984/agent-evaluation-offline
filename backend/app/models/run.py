"""run / 结果 / judge 任务 / 漂移历史。"""
from sqlalchemy import (
    Boolean, DateTime, Enum, ForeignKey, Index, Integer, JSON, Numeric, String, Text,
    UniqueConstraint, text,
)
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# MySQL ENUM 值定义（与 DDL 一致）
# scoring_failed：采集完成但评分超时（scanner 兜底），与 timeout（执行超时）区分
RUN_STATUS = ("pending", "running", "scoring", "scoring_failed", "completed", "partial_failed", "timeout", "cancelled")
TRIGGER_TYPE = ("manual", "held_out")
PASS_FAIL = ("pass", "fail", "error", "na")
# pending_human（人工复核）7.5e 预留机制已随轻量化删除：真实 judge 不输出 confidence 永不触发
JUDGE_TASK_STATUS = ("pending", "processing", "done", "failed")


class EvalRun(Base):
    __tablename__ = "eval_run"
    __table_args__ = (
        Index("idx_run_agent_started", "agent_id", "started_at"),
        Index("idx_run_agent_version", "agent_id", "version"),
        Index("idx_run_status_lease", "status", "lease_until"),
        Index("idx_run_agent_suite_started", "agent_id", "suite_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agent.id"), nullable=False)
    suite_id: Mapped[int] = mapped_column(ForeignKey("test_suite.id"), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    trigger_type: Mapped[str] = mapped_column(
        Enum(*TRIGGER_TYPE, name="trigger_type"), nullable=False, default="manual"
    )
    status: Mapped[str] = mapped_column(
        Enum(*RUN_STATUS, name="eval_run_status"), nullable=False, default="pending"
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)  # fencing token（cancel 自增）
    lease_until: Mapped[object | None] = mapped_column(DateTime)                 # 短租约 90s，心跳 30s 刷新
    hard_deadline: Mapped[object | None] = mapped_column(DateTime)               # 硬超时，不随心跳续
    started_at: Mapped[object | None] = mapped_column(DateTime)
    finished_at: Mapped[object | None] = mapped_column(DateTime)
    total_case: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pass_case: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fail_case: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_case: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    na_case: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    agent_score: Mapped[float | None] = mapped_column(Numeric(6, 2))
    judge_incomplete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # 关键版本不清理
    ttft_p50: Mapped[float | None] = mapped_column(Numeric(10, 3))
    ttft_p95: Mapped[float | None] = mapped_column(Numeric(10, 3))
    e2e_p50: Mapped[float | None] = mapped_column(Numeric(10, 3))
    e2e_p95: Mapped[float | None] = mapped_column(Numeric(10, 3))
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    total_cost: Mapped[float | None] = mapped_column(Numeric(12, 6))
    env_snapshot: Mapped[dict | None] = mapped_column(JSON)   # 两段：contract_version+adapter_config_hash；meta 回填 git_sha/knowledge_version/model
    run_config: Mapped[dict | None] = mapped_column(JSON)     # scope=run 配置冻结值（创建时快照）


class EvalResult(Base):
    """单条用例结果。data_id 追溯 reset(seed) 返回的真实数据 id。"""
    __tablename__ = "eval_result"
    __table_args__ = (
        UniqueConstraint("run_id", "case_id", name="uk_result"),
        Index("idx_result_case", "case_id"),
        Index("idx_result_case_ver", "case_version_id"),
        Index("idx_result_run_pf", "run_id", "pass_fail"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("eval_run.id", ondelete="CASCADE"), nullable=False)
    case_id: Mapped[int] = mapped_column(ForeignKey("test_case.id"), nullable=False)
    case_version_id: Mapped[int] = mapped_column(ForeignKey("case_version.id"), nullable=False)
    data_id: Mapped[str | None] = mapped_column(String(128))
    model: Mapped[str | None] = mapped_column(String(64))
    score_total: Mapped[float | None] = mapped_column(Numeric(6, 2))     # 全 N/A 时 NULL
    score_per_dimension: Mapped[dict | None] = mapped_column(JSON)       # accuracy 四维 [{code,value,na,na_reason}]
    pass_fail: Mapped[str] = mapped_column(Enum(*PASS_FAIL, name="pass_fail"), nullable=False)
    answer: Mapped[str | None] = mapped_column(MEDIUMTEXT)
    reasoning: Mapped[str | None] = mapped_column(MEDIUMTEXT)
    tool_calls: Mapped[dict | None] = mapped_column(JSON)
    usage: Mapped[dict | None] = mapped_column(JSON)                     # [{attempt,prompt_tokens,completion_tokens,total_tokens}] 全 attempt
    timing: Mapped[dict | None] = mapped_column(JSON)                    # [{attempt,start_ts,first_token_ts,end_ts}]
    assertion_results: Mapped[dict | None] = mapped_column(JSON)
    judge_results: Mapped[dict | None] = mapped_column(JSON)
    error_type: Mapped[str | None] = mapped_column(String(32))
    error_detail: Mapped[str | None] = mapped_column(Text)
    ttft_p50: Mapped[float | None] = mapped_column(Numeric(10, 3))
    ttft_p95: Mapped[float | None] = mapped_column(Numeric(10, 3))
    e2e_p50: Mapped[float | None] = mapped_column(Numeric(10, 3))
    e2e_p95: Mapped[float | None] = mapped_column(Numeric(10, 3))
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    total_cost: Mapped[float | None] = mapped_column(Numeric(12, 6))
    finished_at: Mapped[object | None] = mapped_column(DateTime)


class JudgeTask(Base):
    """语义维度判分任务（可恢复队列 + 抢占认领）。"""
    __tablename__ = "judge_task"
    __table_args__ = (
        Index("idx_judge_status", "status", "next_retry_at"),
    )

    run_id: Mapped[int] = mapped_column(ForeignKey("eval_result.run_id", ondelete="CASCADE"), primary_key=True)
    # case_id 业务上就是用例 id，FK 指向 test_case.id（唯一键）；
    # 旧版指向 eval_result.case_id（非唯一列）会阻塞按 case 级联删除 eval_result（FK 1451）
    case_id: Mapped[int] = mapped_column(ForeignKey("test_case.id"), primary_key=True)
    dimension_code: Mapped[str] = mapped_column(ForeignKey("dimension.code"), primary_key=True)
    status: Mapped[str] = mapped_column(
        Enum(*JUDGE_TASK_STATUS, name="judge_task_status"), nullable=False, default="pending"
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    claim_id: Mapped[str | None] = mapped_column(String(64))   # 每次认领唯一 token
    lease_until: Mapped[object | None] = mapped_column(DateTime)
    next_retry_at: Mapped[object | None] = mapped_column(DateTime)
    result: Mapped[dict | None] = mapped_column(JSON)
    updated_at: Mapped[object] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
    )
