"""agent_circuit 加熔断域列并改复合主键（§8.4 熔断域隔离，批 C4b）

给 `agent_circuit` 增加 `domain` 列（默认 'manual'），主键由 `(agent_id)` 改为
`(agent_id, domain)`：manual/held_out 与 error 复现各占一行、互不牵连。原先一行共用
会让 error 复现的连败把 manual 评测一起熔断。

**存量行处置**：旧行装的是 error/manual **共用**产生的计数，无法拆分归属。按拍板结论
统一归入 `manual` 域**并清零**（state='closed' / failures=0 / opened_at=NULL /
probe_inflight=0）——保留旧计数会让 manual 域上线即带着已满的失败计数，
下一次失败就熔断；清零只损失一次冷启动的计数连续性，不影响任何不变量。

**与详设 §8.4 的偏离**：详设的独立 key 形如 `{agent_id}:error_regression`，本实现用
`domain` 列表达同一语义（同表多行），不新建表；且 §8.4 的「独立阈值（默认同 5，实施
可配）」**未实现**——两域共用 run 配置的 `breaker_threshold`，本次只隔离状态。

downgrade **不实现**：回到 agent 级单域意味着两域状态要合并成一行，而合并语义无权威定义
（计数相加会凭空造出本来不存在的失败数）。宁可显式失败，不做静默的错合并。

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-14
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # domain 必须带 DEFAULT：存量行非空，且 app 侧裸 SQL 插入（无域字段时）要能落 manual
    op.execute("ALTER TABLE agent_circuit "
               "ADD COLUMN domain VARCHAR(32) NOT NULL DEFAULT 'manual'")
    # ⚠️ DROP 与 ADD PRIMARY KEY 必须在**同一条 ALTER** 里：agent_id 上的外键需要索引支撑，
    # 拆成两条语句时中间态无可用索引，MySQL 会直接拒绝（errno 1553）
    op.execute("ALTER TABLE agent_circuit "
               "DROP PRIMARY KEY, ADD PRIMARY KEY (agent_id, domain)")
    # 存量计数是两域共用的，无法归属 ⇒ 归 manual 并清零（见文件头）
    op.execute("UPDATE agent_circuit SET state='closed', failures=0, opened_at=NULL, "
               "probe_inflight=0")


def downgrade() -> None:
    raise NotImplementedError(
        "agent_circuit 的 domain 拆分不可回退：两域状态合并无权威语义（计数相加会造出不存在的"
        "失败数）。如确需回退，请先人工裁定合并规则并手写迁移。")
