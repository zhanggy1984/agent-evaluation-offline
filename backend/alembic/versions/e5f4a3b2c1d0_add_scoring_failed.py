"""eval_run.status 新增 scoring_failed

scoring 超时兜底（§15.1）：采集完成但评分未在时限内完成 → scoring_failed，
与 timeout（执行超时）区分。由 scanner 规则③ 写入。

Revision ID: e5f4a3b2c1d0
Revises: a0711408024f
Create Date: 2026-08-17 14:15:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = 'e5f4a3b2c1d0'
down_revision: Union[str, None] = 'a0711408024f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# MySQL ENUM 顺序与 app/models/run.py 的 RUN_STATUS 保持一致
_NEW = "('pending','running','scoring','scoring_failed','completed','partial_failed','timeout','cancelled')"
_OLD = "('pending','running','scoring','completed','partial_failed','timeout','cancelled')"


def upgrade() -> None:
    op.execute(f"ALTER TABLE eval_run MODIFY COLUMN status ENUM{_NEW} NOT NULL")


def downgrade() -> None:
    op.execute(f"ALTER TABLE eval_run MODIFY COLUMN status ENUM{_OLD} NOT NULL")
