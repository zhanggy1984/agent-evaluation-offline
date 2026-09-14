"""回流收单记忆（P1 §5.2 / SD §4.3）。

**为什么要有这张表**：拉取是「请求驱动、无时钟」的——online 不知道 offline 处理到哪，
只有 ack 回写才推进 online 侧状态。若中间断了（进程崩、ack 网络失败），必须有本地
**收单记忆**才能做到：① 同一个 `payload_id` 不会重复建 case（幂等）；② 建了 case 但
ack 没发出去的行能**重扫自愈**（`case_created` + `ack_status != 'acked'`）。

两维状态**正交**，不要合成一列：
- `status` = 收单处置结果（new / case_created / rejected），**终态**
- `ack_status` = 对 online 的回写进度（none / pending / acked / blocked），**可推进**

合成一列的后果：`rejected` 也要 ack（invalidated），若只留一维就表达不了
「已驳回但 ack 还没发出去」这个真实存在的中间态。
"""
from sqlalchemy import (
    BigInteger, DateTime, Index, JSON, String, UniqueConstraint, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

INBOX_STATUS = ("new", "case_created", "rejected")
ACK_STATUS = ("none", "pending", "acked", "blocked")
# online 侧结构自检未过（缺字段） / offline 侧容量或映射缺口 / 人工作废
REJECT_CODE = ("online_content_gap", "offline_cap_gap", "manual_invalidate")


class ErrorBackflowInbox(Base):
    __tablename__ = "error_backflow_inbox"
    __table_args__ = (
        # **具名**约束：迁移建的也是 uk_inbox_payload，两边名字必须一致，
        # 否则 autogenerate 每轮都会把匿名 UniqueConstraint 当成待建差异反复重建
        UniqueConstraint("payload_id", name="uk_inbox_payload"),
        # 对账 / 自愈 / 卡住重扫三类扫描位共用
        Index("idx_status_ack", "status", "ack_status", "updated_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    payload_id: Mapped[str] = mapped_column(String(64), nullable=False)  # online uuid4 幂等键（UK 见 __table_args__）
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    case_type: Mapped[str] = mapped_column(String(32), nullable=False)
    envelope_json: Mapped[dict] = mapped_column(JSON, nullable=False)  # 信封原文留档（不改写）
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="new")
    reject_code: Mapped[str | None] = mapped_column(String(32))
    reject_detail: Mapped[str | None] = mapped_column(String(512))
    case_id: Mapped[int | None] = mapped_column(BigInteger)  # 建成 error case id；作废后置 NULL
    ack_status: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    last_error: Mapped[str | None] = mapped_column(String(512))  # 最近处理异常摘要
    received_at: Mapped[object] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[object] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
    )
