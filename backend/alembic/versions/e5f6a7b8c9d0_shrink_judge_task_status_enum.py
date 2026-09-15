"""shrink judge_task.status enum: drop residual 'pending_human'

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-15

T-3.15 裁定 (a)。库侧该列是 `a0711408024f_initial` 的一次性 DDL 快照（5 值），模型侧自
「轻量化改造」起就是 4 值：`a9e6b4c2d8f1_drop_governance_features` 那次改造**声明**要移除
「人工复核」机制，但其处置范围只含模型 + 三张遗留治理表，**枚举值从未纳入该次范围**
（不是「漏删」，是「从未进入视野」）。

**为什么做**：真库该值 0 行、全仓 `grep pending_human` 只命中文档（无写点）⇒ SQLAlchemy
读到它时的 `LookupError`（`sqlalchemy/sql/sqltypes.py:1711-1724` `_object_value_for_elem`）
**不可达**，单看「删干净」答不出具体故障。收益在别处：该分叉使 `alembic check` **恒报红**，
而**真漂移就混在这片红里**——C4b 那次同类红中混着的 `agent_circuit.opened_at` 一处真漂移
当时就没被独立注意到。修掉本处后，`alembic check` 的红每一处都是真的。

**MySQL 改 enum 会重建表**：`judge_task` 仅 1430 行（2026-09-15 实测 `{done:1429, failed:1}`），
重建瞬时完成。**本迁移不碰 `agent_circuit.opened_at`**（`task.md:239` 明说两处处置可分开）。
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 照 `alembic check` 给出的目标形态写（它报的就是模型侧声明）
_NEW = sa.Enum('pending', 'processing', 'done', 'failed', name='judge_task_status')
_OLD = sa.Enum('pending', 'processing', 'done', 'failed', 'pending_human',
               name='judge_task_status')


def upgrade() -> None:
    # 不传 server_default：两侧 DDL 都没有它（模型侧 `default="pending"` 是 Python 端默认，
    # 非 server_default），传了反而会被 alembic 当成要变更。
    op.alter_column('judge_task', 'status',
                    existing_type=_OLD,
                    type_=_NEW,
                    existing_nullable=False)


def downgrade() -> None:
    # 可逆：把枚举值加回是无损操作（与 C4b 那个「两域合并语义无定义」故
    # NotImplementedError 的情形不同 —— 这里没有数据需要重新归属）。
    op.alter_column('judge_task', 'status',
                    existing_type=_NEW,
                    type_=_OLD,
                    existing_nullable=False)
