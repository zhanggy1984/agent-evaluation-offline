"""审计日志（audit_log）写入：报告导出 / 凭证查看 / 敏感操作。

audit_log 表在 DDL 阶段已建（solution_detail 四-4.6），本模块是首个消费者。
调用方负责事务提交（write_audit 只 db.add，不 commit，便于与业务变更同事务）。
"""
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog

logger = logging.getLogger(__name__)


def _client_ip(request) -> str | None:
    """客户端 IP：优先 X-Forwarded-For（nginx 反代拿真实 IP），兜底直连地址。"""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


async def write_audit(db: AsyncSession, user, request, action: str,
                      target_type: str, target_id, detail: dict | None = None) -> None:
    """落一条审计记录（不提交事务）。target_id 统一为 str 落库。"""
    db.add(AuditLog(
        user_id=user.id, action=action, target_type=target_type,
        target_id=str(target_id), detail=detail, ip=_client_ip(request),
    ))
    logger.debug("audit: user=%s action=%s target=%s/%s", user.username, action, target_type, target_id)
