"""审计日志（audit_log）写入：报告导出 / 凭证查看 / 敏感操作。

audit_log 表在 DDL 阶段已建（solution_detail 四-4.6），本模块是首个消费者。
调用方负责事务提交（write_audit 只 db.add，不 commit，便于与业务变更同事务）。
"""
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import AuditLog

logger = logging.getLogger(__name__)


def _client_ip(request) -> str | None:
    """客户端 IP：X-Forwarded-For 取「可信代理链右侧第 N 个」（N=settings.xff_trusted_proxy_count）。

    P2-D20：原实现取最左值——攻击者可伪造任意 IP 污染审计日志。链路为
    前端 nginx + api-gateway 各 append 一次（$proxy_add_x_forwarded_for），
    backend 收到 [伪造可选, 真实客户端IP, nginx容器IP]，从右数第 N 个 = 真实客户端 IP；
    攻击者伪造再多值也被代理 append 的真实 IP 顶在右侧。XFF 缺失/分段不足 →
    兜底 socket 直连 IP（不可伪造，宁取代理 IP 也不取不可信 header）。
    """
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        parts = [p.strip() for p in fwd.split(",") if p.strip()]
        n = settings.xff_trusted_proxy_count
        if n > 0 and len(parts) >= n:
            return parts[-n]
    return request.client.host if request.client else None


async def write_audit(db: AsyncSession, user, request, action: str,
                      target_type: str, target_id, detail: dict | None = None) -> None:
    """落一条审计记录（不提交事务）。target_id 统一为 str 落库。"""
    db.add(AuditLog(
        user_id=user.id, action=action, target_type=target_type,
        target_id=str(target_id), detail=detail, ip=_client_ip(request),
    ))
    logger.debug("audit: user=%s action=%s target=%s/%s", user.username, action, target_type, target_id)
