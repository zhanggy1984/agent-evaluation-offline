"""eval_run 新增 case_ids 子集列

硬伤 #3 定向重跑：run 可只跑指定 case 子集（None=全量）。可空 JSON 列，
零数据迁移——旧行 NULL 语义即全量。独立于 run_config（run_config 是 scope=run
配置快照，塞子集会污染快照语义；列层字段让 rerun 可显式继承/改批）。

Revision ID: 9e7f6a5b4c3d
Revises: f2a1b3c4d5e6
Create Date: 2026-08-31 09:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '9e7f6a5b4c3d'
down_revision: Union[str, None] = 'f2a1b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('eval_run', sa.Column('case_ids', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('eval_run', 'case_ids')
