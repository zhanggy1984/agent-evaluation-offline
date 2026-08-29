"""judge_task run_id FK 改挂 eval_run.id

P2-D5：run_id 原引用非唯一列 eval_result.run_id（约束 judge_task_ibfk_3），
且 ON DELETE CASCADE 会随任一条 eval_result 删除连带清掉该 run 全部 judge_task。
改挂 eval_run.id（run 主键）：只有删 run 才级联清判分任务，语义正确。
judge_task.run_id 存的本来就是 run id，零数据迁移。

Revision ID: f2a1b3c4d5e6
Revises: a9e6b4c2d8f1
Create Date: 2026-08-29 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'f2a1b3c4d5e6'
down_revision: Union[str, None] = 'a9e6b4c2d8f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 约束名按 initial 建表 FK 顺序自动编号（ibfk_3=run_id；e05783cacd70 已占用 ibfk_1）
    op.drop_constraint('judge_task_ibfk_3', 'judge_task', type_='foreignkey')
    op.create_foreign_key('fk_judge_task_run_id_eval_run', 'judge_task', 'eval_run',
                          ['run_id'], ['id'], ondelete='CASCADE')


def downgrade() -> None:
    op.drop_constraint('fk_judge_task_run_id_eval_run', 'judge_task', type_='foreignkey')
    op.create_foreign_key('judge_task_ibfk_3', 'judge_task', 'eval_result',
                          ['run_id'], ['run_id'], ondelete='CASCADE')
