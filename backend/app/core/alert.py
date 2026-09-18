"""上线告警出口（T-5.5 / G3，2026-09-16 立）。

**为什么要有它**：判据有了不等于有人看得见。回流的判据此前只落进程日志，而运营期没人
逐行读日志 ⇒ 静默断流无预警（同形已踩两次：R-28 恢复路径被自家谓词挡死、sp 启动日志
假警报，二者均只能靠翻日志才发现）。本模块是**唯一的主动出口**。

**未配置即降级**：`alert_webhook_url` 为空时只写 `logger.warning`——不阻断、不报错。
未接通知设施的部署必须能安全载入本模块，否则「加了告警反而起不来」。

**告警失败不得拖垮业务**：`httpx` 异常一律吞掉只记日志。本模块由判据循环调用，
它抛异常会让判据循环退出——那比漏发一条告警更坏。

**为何直用 httpx 而不走 `core/http.py`**：后者的 SSRF 白名单
（`DEFAULT_AGENT_CIDRS`）是为**面向被测 agent 的出站**设的；告警目标是运营方配置的
通知设施，与 agent 域无关，套同一白名单属误用。**如本仓另有「所有出站必须过 http 层」
的口径，此处应改**（见 T-5.5 未验边界）。
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

# 出站告警超时取短值：通道慢不该拖住判据循环
_TIMEOUT_S = 5.0


async def notify(level: str, title: str, detail: str) -> None:
    """发一条告警。未配置 URL 或发送失败，**均降级为日志**、不抛异常。"""
    from app.core.config import settings

    text = f"[{level}] {title} | {detail}"
    url = settings.alert_webhook_url
    if not url:
        logger.warning("告警（无通道，降级日志）：%s", text)
        return
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            await client.post(
                url, json={"level": level, "title": title, "detail": detail}
            )
        logger.warning("告警已发出：%s", text)
    except Exception:
        # 通道失败不影响判据循环——只记，不抛
        logger.exception("告警发送失败（降级日志）：%s", text)
