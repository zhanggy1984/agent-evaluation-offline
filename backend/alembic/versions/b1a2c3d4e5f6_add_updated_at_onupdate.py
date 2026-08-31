"""四表 updated_at 补 ON UPDATE CURRENT_TIMESTAMP

模型已声明 server_onupdate（misc.py/run.py/case.py），但 autogenerate 不识别
server_onupdate（先例 32f602c89957 issue 表手补）→ 初始迁移只建 server_default、
无 ON UPDATE → ORM UPDATE 不触发列自动刷新，updated_at 停在创建时刻：
- system_config：put_global_config 只改 value，配置变更时间不可追溯（V2）
- judge_task / test_case / agent_circuit：同一盲区，一并根治

用 op.execute 直接 MODIFY：alembic MySQL 方言的 alter_column 无 server_onupdate
形参，值落 **kw 被静默丢弃，只发 SET DEFAULT 不报错不生效（评审高危点，勿改回）。

Revision ID: b1a2c3d4e5f6
Revises: 9e7f6a5b4c3d
Create Date: 2026-08-31 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = 'b1a2c3d4e5f6'
down_revision: Union[str, None] = '9e7f6a5b4c3d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("system_config", "judge_task", "test_case", "agent_circuit")


def upgrade() -> None:
    for t in _TABLES:
        op.execute(
            f"ALTER TABLE {t} MODIFY updated_at DATETIME NOT NULL "
            "DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"
        )


def downgrade() -> None:
    for t in _TABLES:
        op.execute(
            f"ALTER TABLE {t} MODIFY updated_at DATETIME NOT NULL "
            "DEFAULT CURRENT_TIMESTAMP"
        )
