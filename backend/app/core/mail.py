"""6.6 邮件告警发送（标准库 smtplib，零新依赖）。

职责：把一封 HTML 邮件实际发出去（低层纯函数，宿主单测 mock smtplib 即可跑）。
告警编排（读 smtp.* 配置、去重、拼内容、asyncio.to_thread 包装）在 core/alarm.py，
mail.py 不感知 DB/配置，保持单一职责。

SSL 约定：port 465 → SMTP_SSL；否则 STARTTLS（含 587 及自定义端口）。
发送异常向上抛 SMTPException，由 alarm.py 兜底记 warning——告警是旁路，绝不阻断业务主流程。
"""
from __future__ import annotations

import logging
import smtplib
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

logger = logging.getLogger(__name__)


def send_smtp_email(*, host: str, port: int, username: str, password: str,
                    from_addr: str, recipients: list[str], subject: str,
                    html: str) -> None:
    """同步发送一封 HTML 邮件。port 465 走 SMTP_SSL，否则 STARTTLS 后 login。"""
    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr(("AI 评测平台告警", from_addr))
    msg["To"] = ", ".join(recipients)
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=10) as s:
            s.login(username, password)
            s.sendmail(from_addr, recipients, msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=10) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(username, password)
            s.sendmail(from_addr, recipients, msg.as_string())
