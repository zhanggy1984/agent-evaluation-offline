"""6.6 告警通知中心：事件驱动统一入口 + 去重恢复 + 多渠道预留。

触发点只调 notify_alarm(kind, key, run_id, active, summary) 一个入口，内部用独立
SessionLocal 事务（不碰调用方 session 的 pending 变更，恢复逻辑收敛在内部）：
- active=True：发起/持续告警。state 已 alerted 且距 last_sent_at 在 dedupe_window 秒内 → 跳过
  （防刷屏）；recovered → 新告警周期，立即重发。
- active=False：恢复。未处于告警态 → 静默；否则发恢复通知并置 recovered。

去重表 alarm_notify（唯一 kind+key），key 语义：
- run：f"run-{agent_id}"（agent 级状态告警——同 agent 在去重窗口内多次失败 run 只发一封，
  防持续红灯刷屏；后续成功 run 解除恢复）
- overfit：f"overfit-{agent_id}"（恢复 = 后续 held_out 复测不再超阈值）
- drift：f"drift-{dim}"（恢复 = 后续漂移检测该维度不再 flag）

内容组装与渠道渲染解耦：_build_alarm 产出渠道无关的告警内容（title/level/details），
新增渠道只需新增渲染函数（_render_email 当前唯一渠道）+ 在 _send 分发，不改内容层。

配置（system_config global is_hot + env SMTP_PASSWORD，与 get_overfit_config 同为唯一读取点）：
smtp.host / smtp.port / smtp.username / smtp.from_addr、alarm.enabled / alarm.recipients /
alarm.dedupe_window（秒，缺省 3600）/ alarm.error_ratio（run 告警 error/total 触发比，缺省 0.5）。

发送失败只记 warning、不刷新 last_sent_at、不落库——告警是旁路，绝不阻断业务主流程，
且下次触发点事件可重试。
"""
from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from app.core.mail import send_smtp_email

logger = logging.getLogger(__name__)

ALARM_CONFIG_KEYS = (
    "smtp.host", "smtp.port", "smtp.username", "smtp.from_addr",
    "alarm.enabled", "alarm.recipients", "alarm.dedupe_window", "alarm.error_ratio",
)
# 告警种类标签（渠道无关；新增 kind 在此注册，主题/正文渲染基于此）
ALARM_KINDS = {
    "run": {"label": "评测异常告警", "level": "critical"},
    "overfit": {"label": "过拟合告警", "level": "warning"},
    "drift": {"label": "judge 漂移告警", "level": "warning"},
}
_DEFAULT_WINDOW = 3600
_DEFAULT_RATIO = 0.5


def _parse_alarm_config(vals: dict, password: str) -> dict:
    """从 system_config 行值解析告警配置（纯函数，宿主单测直接跑）。

    非法数字回落默认（配置被改脏不阻断触发点）；alarm.enabled 兼容 bool/字符串；
    recipients 逗号分隔去空白去空。
    """
    def _num(key, default, cast=float):
        v = vals.get(key)
        if v is None or v == "":
            return default
        try:
            return cast(v)
        except (TypeError, ValueError):
            logger.warning("告警配置 %s 非法（%r），回落默认 %s", key, v, default)
            return default

    enabled = str(vals.get("alarm.enabled", "")).strip().lower() in ("1", "true", "yes", "on")
    recipients = [r.strip() for r in str(vals.get("alarm.recipients") or "").split(",") if r.strip()]
    return {
        "host": str(vals.get("smtp.host") or ""),
        "port": int(_num("smtp.port", 465, int)),
        "username": str(vals.get("smtp.username") or ""),
        "from_addr": str(vals.get("smtp.from_addr") or ""),
        "password": password,
        "enabled": enabled,
        "recipients": recipients,
        "dedupe_window": int(_num("alarm.dedupe_window", _DEFAULT_WINDOW, int)),
        "error_ratio": _num("alarm.error_ratio", _DEFAULT_RATIO),
    }


async def get_alarm_config(db) -> dict:
    """告警/SMTP 配置唯一读取点（system_config global is_hot + env SMTP_PASSWORD）。"""
    from app.core.config import settings
    from app.models import SystemConfig

    rows = await db.execute(
        select(SystemConfig).where(SystemConfig.key.in_(ALARM_CONFIG_KEYS)))
    vals = {r.key: r.value for r in rows.scalars()}
    return _parse_alarm_config(vals, settings.smtp_password)


def _build_alarm(kind: str, active: bool, summary: str) -> dict:
    """渠道无关的告警内容：title/level/details。邮件等渠道基于此渲染。"""
    label = ALARM_KINDS[kind]["label"]
    return {
        "title": label if active else f"{label}（已恢复）",
        "level": ALARM_KINDS[kind]["level"],
        "active": active,
        "details": summary,
    }


def _render_email(alert: dict) -> tuple[str, str]:
    """告警内容 → 邮件（subject, html）。新增渠道在此新增渲染函数。"""
    body = html.escape(alert["details"]).replace("\n", "<br/>")
    html_body = (
        "<html><head><meta charset='utf-8'/></head><body>"
        f"<h3>{alert['title']}</h3><div>{body}</div>"
        "<hr/><p style='color:#888'>AI 评测平台自动发送，请勿回复。</p></body></html>"
    )
    return f"[AI 评测] {alert['title']}", html_body


async def _send(cfg: dict, subject: str, html_body: str) -> bool:
    """实际发送（to_thread 不阻塞事件循环）。失败记 warning 返回 False，绝不抛出。"""
    try:
        await asyncio.to_thread(
            send_smtp_email, host=cfg["host"], port=cfg["port"], username=cfg["username"],
            password=cfg["password"], from_addr=cfg["from_addr"],
            recipients=cfg["recipients"], subject=subject, html=html_body)
        return True
    except Exception as e:  # smtplib 全家 + 网络抖动：旁路告警，记录后让触发点继续
        logger.warning("告警邮件发送失败：%s", e)
        return False


async def _notify(db, cfg: dict, kind: str, key: str, run_id: int | None,
                  active: bool, summary: str) -> bool:
    """核心状态机（接收已解析配置与 session，宿主单测 mock db 直接测）。

    在 notify_alarm 的独立事务内运行，DB 变更只含 alarm_notify，不碰调用方 pending。
    """
    from app.models import AlarmNotify

    if not cfg["enabled"] or not cfg["recipients"] or not cfg["host"] or not cfg["username"]:
        logger.debug("告警配置不完整（enabled=%s recipients=%d host=%r username=%r），跳过 kind=%s key=%s",
                     cfg["enabled"], len(cfg["recipients"]), cfg["host"], cfg["username"], kind, key)
        return False

    rec = await db.scalar(select(AlarmNotify).where(
        AlarmNotify.kind == kind, AlarmNotify.key == key))
    now = datetime.now()
    window = timedelta(seconds=max(cfg["dedupe_window"], 0))
    alert = _build_alarm(kind, active, summary)

    if not active:
        if rec is None or rec.state != "alerted":
            return False  # 未在告警态，恢复无意义
        subject, html_body = _render_email(alert)
        if not await _send(cfg, subject, html_body):
            return False
        rec.state = "recovered"
        rec.last_sent_at = now
        await db.commit()
        logger.info("告警恢复已发送：kind=%s key=%s", kind, key)
        return True

    # active：去重窗口判定
    if rec is None:
        rec = AlarmNotify(kind=kind, key=key, run_id=run_id, state="alerted")
    elif rec.state == "recovered":
        rec.state = "alerted"
        rec.run_id = run_id
    else:  # alerted 且窗口内 → 跳过
        if now - rec.last_sent_at < window:
            logger.debug("告警 %s/%s 在去重窗口内，跳过", kind, key)
            return False

    subject, html_body = _render_email(alert)
    if not await _send(cfg, subject, html_body):
        return False  # 发送失败：不落库不刷新，下次事件重试
    db.add(rec)
    rec.last_sent_at = now
    await db.commit()
    logger.info("告警已发送：kind=%s key=%s", kind, key)
    return True


async def notify_alarm(kind: str, key: str, run_id: int | None,
                       active: bool, summary: str) -> bool:
    """事件驱动告警统一入口（独立事务，不碰调用方 session）。返回是否真的发送。"""
    from app.core.db import SessionLocal

    async with SessionLocal() as db:
        cfg = await get_alarm_config(db)
        return await _notify(db, cfg, kind, key, run_id, active, summary)
