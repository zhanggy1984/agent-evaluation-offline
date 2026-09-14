"""回流最小闭环 DDL（批 A）

**与详设 SD §4.5 的偏离（有意，非笔误）**：SD 把它拆成 4 个 revision（rev1 case 扩列 /
rev2 inbox / rev3 trigger+anchor / rev4 backfill）。本实现合为**一个**迁移，理由：
① 四段改动**同批开发、同批部署**，拆开只是多三次 upgrade 往返，不产生任何独立可验收的
中间态；② 合起来才能保证「upgrade 到 head 即可用」这一条对运维成立。SD §4.5 已转为
**偏离记录**（`error-backflow-solution_detail.md` §4.5 顶部块，含理由 + 代价 + downgrade
限制）；`error-backflow-task.md` O-B.4 同步标注。⚠️ 本行原写「已在本次实施中同步标注」
时尚**未**同步（2026-09-14 事后补做），留此说明以免后人以为该订正与实现同批完成。

**内容**：
1. `test_suite.is_error_suite`（回流专用集，每 agent 至多一个）
2. `test_case` 三新列 `case_type/payload_id/backflow_envelope` + UK `uk_case_payload_id`
3. `test_case` 三列放宽 `expected/assertions/metrics` → NULL（错误 case 不参与评分）
4. `eval_run.trigger_type` ENUM 加 `error_regression` + 两新列 `trigger_signal_id/excluded_case_ids`
5. 新表 `error_backflow_inbox`

**downgrade 的两处已知限制**（如实标注，不是 bug）：把 `expected/assertions/metrics` 改回
NOT NULL、把 `trigger_type` 收回两值，在**已有错误 case / 已有 error_regression run** 的库上
会失败（MySQL 拒绝把 NULL 列收紧）。这是「不回退已投产数据」的正常语义——真要回退须先清数据。

Revision ID: c3d4e5f6a7b8
Revises: b1a2c3d4e5f6
Create Date: 2026-09-14 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b1a2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. test_suite.is_error_suite
    op.add_column(
        "test_suite",
        sa.Column("is_error_suite", sa.Boolean(), nullable=False, server_default=text("0")),
    )

    # 2. test_case 三新列 + 幂等唯一键
    op.add_column("test_case", sa.Column("case_type", sa.String(32), nullable=True))
    op.add_column("test_case", sa.Column("payload_id", sa.String(64), nullable=True))
    op.add_column("test_case", sa.Column("backflow_envelope", sa.JSON(), nullable=True))
    op.create_unique_constraint("uk_case_payload_id", "test_case", ["payload_id"])

    # 3. 三列放宽为 NULL（错误 case 无 metrics；assertions 由词表转录、可空）
    for col in ("expected", "assertions", "metrics"):
        op.execute(f"ALTER TABLE test_case MODIFY {col} JSON NULL")

    # 4. eval_run：ENUM 扩值 + 两新列。
    # trigger_type 是 MySQL 原生 ENUM，加值必须 MODIFY 整列定义（含 NOT NULL/DEFAULT，漏写会丢）
    op.execute(
        "ALTER TABLE eval_run MODIFY trigger_type "
        "ENUM('manual','held_out','error_regression') NOT NULL DEFAULT 'manual'"
    )
    op.add_column("eval_run", sa.Column("trigger_signal_id", sa.BigInteger(), nullable=True))
    op.add_column("eval_run", sa.Column("excluded_case_ids", sa.JSON(), nullable=True))

    # 5. 收单记忆表
    op.create_table(
        "error_backflow_inbox",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("payload_id", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("case_type", sa.String(32), nullable=False),
        sa.Column("envelope_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("reject_code", sa.String(32), nullable=True),
        sa.Column("reject_detail", sa.String(512), nullable=True),
        sa.Column("case_id", sa.BigInteger(), nullable=True),
        sa.Column("ack_status", sa.String(16), nullable=False),
        sa.Column("last_error", sa.String(512), nullable=True),
        sa.Column(
            "received_at", sa.DateTime(), nullable=False,
            server_default=text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False,
            server_default=text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("payload_id", name="uk_inbox_payload"),
    )
    op.create_index(
        "idx_status_ack", "error_backflow_inbox",
        ["status", "ack_status", "updated_at"],
    )
    # updated_at 的 ON UPDATE：autogenerate 不识别 server_onupdate（先例 b1a2c3d4e5f6 手补），
    # 必须 op.execute —— alter_column 无 server_onupdate 形参，值落 **kw 被静默丢弃（勿改回）
    op.execute(
        "ALTER TABLE error_backflow_inbox MODIFY updated_at DATETIME NOT NULL "
        "DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"
    )


def downgrade() -> None:
    op.drop_table("error_backflow_inbox")

    op.drop_column("eval_run", "excluded_case_ids")
    op.drop_column("eval_run", "trigger_signal_id")
    op.execute(
        "ALTER TABLE eval_run MODIFY trigger_type "
        "ENUM('manual','held_out') NOT NULL DEFAULT 'manual'"
    )

    # 收紧回 NOT NULL：库内若已有错误 case（三列含 NULL）会失败——见文件头「已知限制」
    for col in ("expected", "assertions", "metrics"):
        op.execute(f"ALTER TABLE test_case MODIFY {col} JSON NOT NULL")

    op.drop_constraint("uk_case_payload_id", "test_case", type_="unique")
    op.drop_column("test_case", "backflow_envelope")
    op.drop_column("test_case", "payload_id")
    op.drop_column("test_case", "case_type")

    op.drop_column("test_suite", "is_error_suite")
