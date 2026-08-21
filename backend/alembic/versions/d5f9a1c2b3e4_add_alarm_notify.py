"""add alarm_notify

Revision ID: d5f9a1c2b3e4
Revises: e05783cacd70
Create Date: 2026-08-19 09:30:00.000000

6.6 告警通知去重表：唯一 (kind, key)，state 标记告警/恢复态，
last_sent_at 供 dedupe_window 去重窗口判断。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd5f9a1c2b3e4'
down_revision: Union[str, None] = 'e05783cacd70'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'alarm_notify',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('kind', sa.Enum('run', 'overfit', 'drift', name='alarm_kind'), nullable=False),
        sa.Column('key', sa.String(length=128), nullable=False),
        sa.Column('run_id', sa.Integer(), nullable=True),
        sa.Column('state', sa.Enum('alerted', 'recovered', name='alarm_state'), nullable=False),
        sa.Column('last_sent_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['run_id'], ['eval_run.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('kind', 'key', name='uq_alarm_notify_kind_key'),
    )
    op.create_index('idx_alarm_state', 'alarm_notify', ['state'])


def downgrade() -> None:
    op.drop_index('idx_alarm_state', table_name='alarm_notify')
    op.drop_table('alarm_notify')
