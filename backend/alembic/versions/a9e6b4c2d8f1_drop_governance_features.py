"""drop governance tables (issue / alarm_notify / judge_drift_history)

Revision ID: a9e6b4c2d8f1
Revises: c6f7a2d1e3b5
Create Date: 2026-08-21 14:00:00.000000

轻量化改造：移除骨架生成 / issue 系统 / 漂移检测（含 overfit）/ 告警 / 人工复核后，
对应模型已从 app.models 移除（misc.py 无 Issue/AlarmNotify，run.py 无 JudgeDriftHistory），
本迁移删除这三张遗留治理表。不可逆（数据丢失），实施前已 mysqldump 备份全库。
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'a9e6b4c2d8f1'
down_revision: Union[str, None] = 'c6f7a2d1e3b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # issue 表含 issue_timestamps 追加列，drop 整表一并清除
    op.drop_table('judge_drift_history')
    op.drop_table('alarm_notify')
    op.drop_table('issue')


def downgrade() -> None:
    # 不可逆：结构重建请回看 initial（issue/judge_drift_history）与 d5f9a1c2b3e4（alarm_notify），
    # 历史数据无法恢复。
    pass
