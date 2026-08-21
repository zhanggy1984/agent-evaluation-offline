"""6.6 告警通知纯逻辑单测（宿主直接跑，不触 app.core.db）。

覆盖：_parse_alarm_config（默认值/显式值/非法回落）、_build_alarm/_render_email
（标题/正文/HTML 转义）、_notify 状态机（配置不完整跳过 / 去重窗口跳过 / 窗口外重发 /
recovered 后立即重告警 / 恢复发送 / 未告警静默 / 发送失败不落库不刷新）——
DB 用 fake session，发送用 AsyncMock。notify_alarm 独立事务入口由容器冒烟覆盖。
"""
import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from app.core import alarm
from app.models import AlarmNotify


def _cfg(**over):
    base = {
        "host": "smtp.example.com", "port": 465, "username": "alarm",
        "from_addr": "alarm@example.com", "password": "pw", "enabled": True,
        "recipients": ["ops@example.com"], "dedupe_window": 3600, "error_ratio": 0.5,
    }
    base.update(over)
    return base


class _FakeDb:
    """_notify 最小 session 假面：scalar 返回 rec，add 记录，commit 计数。"""

    def __init__(self, rec=None):
        self.rec = rec
        self.added = []
        self.commits = 0

    async def scalar(self, stmt):
        return self.rec

    def add(self, obj):
        self.added.append(obj)
        if self.rec is None:
            self.rec = obj

    async def commit(self):
        self.commits += 1


def _rec(state="alerted", last_sent_at=None):
    return AlarmNotify(kind="run", key="run-1", run_id=1, state=state,
                       last_sent_at=last_sent_at or datetime.now())


def _run_notify(db, **kw):
    kw.setdefault("kind", "run")
    kw.setdefault("key", "run-1")
    kw.setdefault("run_id", 1)
    kw.setdefault("active", True)
    kw.setdefault("summary", "详情")
    return asyncio.run(alarm._notify(db, _cfg(), **kw))


# ---------------- _parse_alarm_config ----------------
def test_parse_defaults():
    cfg = alarm._parse_alarm_config({}, "")
    assert cfg["enabled"] is False and cfg["recipients"] == []
    assert cfg["host"] == "" and cfg["password"] == ""
    assert cfg["port"] == 465 and cfg["dedupe_window"] == 3600
    assert cfg["error_ratio"] == 0.5


def test_parse_values():
    vals = {
        "smtp.host": "smtp.ex.com", "smtp.port": 587, "smtp.username": "u",
        "smtp.from_addr": "a@b.com", "alarm.enabled": "true",
        "alarm.recipients": " x@y.com , z@w.com ", "alarm.dedupe_window": 120,
        "alarm.error_ratio": 0.3,
    }
    cfg = alarm._parse_alarm_config(vals, "pw")
    assert cfg["host"] == "smtp.ex.com" and cfg["port"] == 587
    assert cfg["username"] == "u" and cfg["from_addr"] == "a@b.com"
    assert cfg["password"] == "pw"
    assert cfg["enabled"] is True
    assert cfg["recipients"] == ["x@y.com", "z@w.com"]
    assert cfg["dedupe_window"] == 120 and cfg["error_ratio"] == 0.3


def test_parse_invalid_fallback():
    cfg = alarm._parse_alarm_config({"smtp.port": "abc", "alarm.error_ratio": "oops"}, "")
    assert cfg["port"] == 465 and cfg["error_ratio"] == 0.5


def test_parse_enabled_bool_and_string():
    assert alarm._parse_alarm_config({"alarm.enabled": True}, "")["enabled"] is True
    assert alarm._parse_alarm_config({"alarm.enabled": "yes"}, "")["enabled"] is True
    assert alarm._parse_alarm_config({"alarm.enabled": False}, "")["enabled"] is False


# ---------------- _build_alarm / _render_email ----------------
def test_build_alarm_alert():
    alert = alarm._build_alarm("run", True, "run #1 异常：fail=2")
    assert alert["title"] == "评测异常告警"
    assert alert["level"] == "critical" and alert["active"] is True
    assert alert["details"] == "run #1 异常：fail=2"


def test_build_alarm_recover():
    alert = alarm._build_alarm("drift", False, "维度 accuracy 已恢复")
    assert alert["title"] == "judge 漂移告警（已恢复）"
    assert alert["level"] == "warning" and alert["active"] is False


def test_render_email_content():
    subject, html = alarm._render_email(alarm._build_alarm("run", True, "run #1 异常：fail=2"))
    assert subject == "[AI 评测] 评测异常告警"
    assert "run #1 异常：fail=2" in html


def test_render_email_escapes_html():
    alert = alarm._build_alarm("overfit", True, "<script>alert(1)</script>")
    _, html = alarm._render_email(alert)
    assert "<script>" not in html and "&lt;script&gt;" in html


# ---------------- _notify 状态机 ----------------
def test_alert_creates_and_sends():
    db = _FakeDb()
    with patch.object(alarm, "_send", AsyncMock(return_value=True)) as send:
        assert _run_notify(db) is True
        send.assert_awaited_once()
        assert db.rec is not None and db.rec.state == "alerted"
        assert db.commits == 1


def test_alert_dedupe_window_skip():
    db = _FakeDb(_rec(last_sent_at=datetime.now() - timedelta(seconds=100)))
    with patch.object(alarm, "_send", AsyncMock(return_value=True)) as send:
        assert _run_notify(db) is False
        send.assert_not_awaited()
        assert db.commits == 0


def test_alert_after_window_resend():
    rec = _rec(last_sent_at=datetime.now() - timedelta(seconds=7200))
    db = _FakeDb(rec)
    with patch.object(alarm, "_send", AsyncMock(return_value=True)) as send:
        assert _run_notify(db) is True
        send.assert_awaited_once()
        assert db.commits == 1
        assert (datetime.now() - rec.last_sent_at).total_seconds() < 5


def test_alert_after_recovered_immediate():
    db = _FakeDb(_rec(state="recovered", last_sent_at=datetime.now()))
    with patch.object(alarm, "_send", AsyncMock(return_value=True)) as send:
        assert _run_notify(db) is True
        send.assert_awaited_once()
        assert db.rec.state == "alerted"


def test_recover_sends_when_alerted():
    db = _FakeDb(_rec())
    with patch.object(alarm, "_send", AsyncMock(return_value=True)) as send:
        assert _run_notify(db, active=False) is True
        send.assert_awaited_once()
        assert db.rec.state == "recovered"
        assert db.commits == 1


def test_recover_silent_when_never_alerted():
    db = _FakeDb()
    with patch.object(alarm, "_send", AsyncMock(return_value=True)) as send:
        assert _run_notify(db, active=False) is False
        send.assert_not_awaited()
        assert db.commits == 0


def test_recover_silent_when_already_recovered():
    db = _FakeDb(_rec(state="recovered"))
    with patch.object(alarm, "_send", AsyncMock(return_value=True)) as send:
        assert _run_notify(db, active=False) is False
        send.assert_not_awaited()


def test_disabled_skip():
    db = _FakeDb()
    with patch.object(alarm, "_send", AsyncMock(return_value=True)) as send:
        assert asyncio.run(alarm._notify(db, _cfg(enabled=False), "run", "run-1", 1, True, "详情")) is False
        send.assert_not_awaited()
        assert db.commits == 0 and db.added == []


def test_config_incomplete_skip():
    db = _FakeDb()
    with patch.object(alarm, "_send", AsyncMock(return_value=True)) as send:
        cfg = _cfg(host="", recipients=[])
        assert asyncio.run(alarm._notify(db, cfg, "run", "run-1", 1, True, "详情")) is False
        send.assert_not_awaited()
        assert db.commits == 0


def test_send_failure_keeps_state():
    rec = _rec(last_sent_at=datetime.now() - timedelta(seconds=7200))
    db = _FakeDb(rec)
    with patch.object(alarm, "_send", AsyncMock(return_value=False)) as send:
        assert _run_notify(db) is False
        send.assert_awaited_once()
        assert db.commits == 0  # 发送失败不落库不刷新，下次事件可重试
        assert (datetime.now() - rec.last_sent_at).total_seconds() > 7000


def test_new_send_failure_not_added():
    db = _FakeDb()
    with patch.object(alarm, "_send", AsyncMock(return_value=False)):
        assert _run_notify(db) is False
        assert db.added == []
