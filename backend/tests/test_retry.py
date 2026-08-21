"""7.1 指数退避重试单测（app/core/retry.py）。

async 用 asyncio.run 包装（宿主模式，无 pytest-asyncio）。
monkeypatch asyncio.sleep 断言退避公式；random.random 冻结验证 jitter 取值。
"""
import asyncio

import pytest

from app.core.retry import retry_with_backoff


def test_first_attempt_success():
    calls = []

    async def fn():
        calls.append(1)
        return "ok"

    assert asyncio.run(retry_with_backoff(fn, attempts=3)) == "ok"
    assert len(calls) == 1  # 首次成功不重试


def test_retry_then_success(monkeypatch):
    async def noop(_s):  # 跳过真实等待
        pass

    monkeypatch.setattr("app.core.retry.asyncio.sleep", noop)
    state = {"n": 0}

    async def fn():
        state["n"] += 1
        if state["n"] < 3:
            raise ValueError("boom")
        return "ok"

    assert asyncio.run(retry_with_backoff(fn, attempts=3, base_delay=0.1, jitter=False)) == "ok"
    assert state["n"] == 3  # 第 3 次尝试成功


def test_all_attempts_fail_raise_original(monkeypatch):
    async def noop(_s):
        pass

    monkeypatch.setattr("app.core.retry.asyncio.sleep", noop)
    state = {"n": 0}

    async def fn():
        state["n"] += 1
        raise KeyError("k")

    with pytest.raises(KeyError):
        asyncio.run(retry_with_backoff(fn, attempts=2))
    assert state["n"] == 2  # 最后一次失败原样抛出（由调用方归类）


def test_attempts_invalid():
    with pytest.raises(ValueError):
        asyncio.run(retry_with_backoff(lambda: None, attempts=0))


def test_backoff_delay_formula(monkeypatch):
    delays = []

    async def fake_sleep(d):
        delays.append(d)

    monkeypatch.setattr("app.core.retry.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("app.core.retry.random.random", lambda: 1.0)  # jitter 因子=1.0（不缩放）
    state = {"n": 0}

    async def fn():
        state["n"] += 1
        raise ValueError("x")

    with pytest.raises(ValueError):
        asyncio.run(retry_with_backoff(fn, attempts=4, base_delay=1.0, max_delay=10.0, jitter=True))
    # attempts=4 → 第 2/3/4 次尝试前分别 sleep base*2^i = 1 / 2 / 4
    assert delays == [1.0, 2.0, 4.0]


def test_max_delay_cap(monkeypatch):
    delays = []

    async def fake_sleep(d):
        delays.append(d)

    monkeypatch.setattr("app.core.retry.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("app.core.retry.random.random", lambda: 1.0)  # jitter 因子=1.0
    state = {"n": 0}

    async def fn():
        state["n"] += 1
        raise ValueError("x")

    with pytest.raises(ValueError):
        asyncio.run(retry_with_backoff(fn, attempts=5, base_delay=4.0, max_delay=10.0, jitter=True))
    # 指数后 4→8→16(封顶 10)→32(封顶 10)
    assert delays == [4.0, 8.0, 10.0, 10.0]
