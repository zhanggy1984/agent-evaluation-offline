"""T-5.5 / G3 批 1 单测：ack 积压判据的集合变化三态 + 告警出口的降级路径。

**本文件不连库**（真库探针另计）：用假 session 灌 `payload_id` 列表，只验判据逻辑与
「发不发」的决策。真库探针负责验「SQL 真的捞得对」。
"""
from __future__ import annotations

import pytest

from app.core import alert
from app.core.config import settings
from app.runner import ack_stale_probe as asp


class _FakeScalars:
    def __init__(self, rows: list[str]) -> None:
        self._rows = rows

    def all(self) -> list[str]:
        return self._rows


class _FakeResult:
    def __init__(self, rows: list[str]) -> None:
        self._rows = rows

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._rows)


class _FakeSession:
    """`async with SessionLocal() as db` + `await db.execute(stmt)` 的最小替身。"""

    def __init__(self, rows: list[str]) -> None:
        self._rows = rows

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def execute(self, _stmt: object) -> _FakeResult:
        return _FakeResult(self._rows)


def _patch(monkeypatch: pytest.MonkeyPatch, rows: list[str], sent: list) -> None:
    monkeypatch.setattr(asp, "SessionLocal", lambda: _FakeSession(rows))

    async def _fake_notify(level: str, title: str, detail: str) -> None:
        sent.append((level, title, detail))

    monkeypatch.setattr(asp, "notify", _fake_notify)


@pytest.mark.asyncio
async def test_new_ids_triggers_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    """从空集 → 有积压：发一条，且明细含新增条数。"""
    sent: list = []
    _patch(monkeypatch, ["p1", "p2"], sent)

    got = await asp.probe_once(set())

    assert got == {"p1", "p2"}
    assert len(sent) == 1
    assert "新增 2 条" in sent[0][2]


@pytest.mark.asyncio
async def test_unchanged_set_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    """集合不变：**不重发**（防每 60s 刷屏）。"""
    sent: list = []
    _patch(monkeypatch, ["p1", "p2"], sent)

    got = await asp.probe_once({"p1", "p2"})

    assert got == {"p1", "p2"}
    assert sent == []


@pytest.mark.asyncio
async def test_partial_clear_still_alerts(monkeypatch: pytest.MonkeyPatch) -> None:
    """积压减少但仍存在：集合变了 ⇒ 发（否则运营看不到「正在好转/恶化」）。"""
    sent: list = []
    _patch(monkeypatch, ["p2"], sent)

    await asp.probe_once({"p1", "p2"})

    assert len(sent) == 1
    assert "共 1 条" in sent[0][2]


@pytest.mark.asyncio
async def test_cleared_set_reports_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """集合清空 = 恢复：发「已恢复」，且带上上一轮条数。"""
    sent: list = []
    _patch(monkeypatch, [], sent)

    got = await asp.probe_once({"p1", "p2"})

    assert got == set()
    assert len(sent) == 1
    assert "已恢复" in sent[0][2] and "上一轮 2 条" in sent[0][2]


@pytest.mark.asyncio
async def test_both_empty_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    """两边都空（从未积压）：不发——**否则恢复告警会恒发**。"""
    sent: list = []
    _patch(monkeypatch, [], sent)

    await asp.probe_once(set())

    assert sent == []


@pytest.mark.asyncio
async def test_notify_degrades_to_log_when_url_absent(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """未配置 URL ⇒ 只落日志，不建 HTTP 客户端。"""
    monkeypatch.setattr(settings, "alert_webhook_url", "")

    def _boom(*_a: object, **_kw: object) -> None:
        raise AssertionError("未配置 URL 时不得发起 HTTP")

    monkeypatch.setattr(alert.httpx, "AsyncClient", _boom)

    with caplog.at_level("WARNING"):
        await alert.notify("warning", "标题", "明细")

    assert any("无通道" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_notify_swallows_send_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """通道报错不得抛出——判据循环不能被告警拖死。"""
    monkeypatch.setattr(settings, "alert_webhook_url", "http://alert.invalid/hook")

    class _BoomClient:
        def __init__(self, *_a: object, **_kw: object) -> None:
            pass

        async def __aenter__(self) -> "_BoomClient":
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

        async def post(self, *_a: object, **_kw: object) -> None:
            raise RuntimeError("通道挂了")

    monkeypatch.setattr(alert.httpx, "AsyncClient", _BoomClient)

    with caplog.at_level("WARNING"):
        await alert.notify("warning", "标题", "明细")  # 不抛即通过

    assert any("发送失败" in r.message for r in caplog.records)
