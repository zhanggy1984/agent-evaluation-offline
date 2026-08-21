"""add agent_circuit

Revision ID: c6f7a2d1e3b5
Revises: d5f9a1c2b3e4
Create Date: 2026-08-20 10:00:00.000000

7.6 C3 熔断器跨 worker 持久化：agent 级一行存状态机快照（state/failures/opened_at/probe_inflight），
opened_at 为 wall clock epoch 秒（circuit_breaker 已统一 time.time 基准）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c6f7a2d1e3b5'
down_revision: Union[str, None] = 'd5f9a1c2b3e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'agent_circuit',
        sa.Column('agent_id', sa.Integer(), nullable=False),
        sa.Column('state', sa.String(length=16), nullable=False),
        sa.Column('failures', sa.Integer(), nullable=False),
        # opened_at 必须 DOUBLE：MySQL FLOAT(单精度) 对 epoch 秒(1.7e9) 精度丢失
        # （1787201704.454987 → 1787200000，误差超 1700s），会让熔断误判冷却已到、跨 run 放行探针
        sa.Column('opened_at', sa.Float(precision=53), nullable=True),
        sa.Column('probe_inflight', sa.Integer(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['agent_id'], ['agent.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('agent_id'),
    )


def downgrade() -> None:
    op.drop_table('agent_circuit')
