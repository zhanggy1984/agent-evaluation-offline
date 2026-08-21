"""7.1 熔断器状态机单测（app/core/circuit_breaker.py）。

纯逻辑，宿主直接跑。open→half_open 的时间窗用 monkeypatch 冻结 time.time 验证
（7.6 C3 DB 化后统一 wall clock 基准，跨 worker 持久化的 opened_at 才能可比）。
"""
import pytest

from app.core.circuit_breaker import CircuitBreaker, CircuitState


def test_initial_closed():
    cb = CircuitBreaker(failure_threshold=3, open_duration_s=10)
    assert cb.state == CircuitState.CLOSED
    assert cb.failures == 0
    assert cb.try_acquire() is True


def test_threshold_invalid():
    with pytest.raises(ValueError):
        CircuitBreaker(failure_threshold=0)


def test_failures_open_on_threshold():
    cb = CircuitBreaker(failure_threshold=3, open_duration_s=60)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED  # 未到阈值仍 closed
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    assert cb.failures == 0  # 转 open 时计数清零


def test_open_rejects_within_cooldown(monkeypatch):
    cb = CircuitBreaker(failure_threshold=1, open_duration_s=30)
    monkeypatch.setattr("app.core.circuit_breaker.time.time", lambda: 1000.0)
    cb.record_failure()  # 1000 转 open
    assert cb.state == CircuitState.OPEN
    assert cb.try_acquire() is False  # 冷却未到不放行


def test_open_to_half_open_single_probe(monkeypatch):
    cb = CircuitBreaker(failure_threshold=1, open_duration_s=30)
    clock = {"now": 1000.0}
    monkeypatch.setattr("app.core.circuit_breaker.time.time", lambda: clock["now"])
    cb.record_failure()
    clock["now"] = 1031.0  # 冷却已过
    assert cb.try_acquire() is True
    assert cb.state == CircuitState.HALF_OPEN
    assert cb.try_acquire() is False  # 单探针约束：已有探针在飞不放行
    cb.release_probe()
    assert cb.try_acquire() is True  # 名额归还后仍可放行（仍 half_open）


def test_probe_success_closes(monkeypatch):
    cb = CircuitBreaker(failure_threshold=1, open_duration_s=30)
    clock = {"now": 1000.0}
    monkeypatch.setattr("app.core.circuit_breaker.time.time", lambda: clock["now"])
    cb.record_failure()
    clock["now"] = 1031.0
    assert cb.try_acquire() is True  # 探针
    cb.record_success()
    assert cb.state == CircuitState.CLOSED
    assert cb.failures == 0


def test_probe_failure_reopens(monkeypatch):
    cb = CircuitBreaker(failure_threshold=1, open_duration_s=30)
    clock = {"now": 1000.0}
    monkeypatch.setattr("app.core.circuit_breaker.time.time", lambda: clock["now"])
    cb.record_failure()
    clock["now"] = 1031.0
    assert cb.try_acquire() is True
    cb.record_failure()  # 探针失败 → 立即回 open，计数重置重新计时
    assert cb.state == CircuitState.OPEN
    assert cb.failures == 0
    assert cb.try_acquire() is False


def test_success_resets_failures():
    cb = CircuitBreaker(failure_threshold=5, open_duration_s=30)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()
    assert cb.failures == 0
    assert cb.state == CircuitState.CLOSED


# ---- 7.6 C3 to_dict/from_dict 持久化往返（threshold/open_duration 不进快照） ----

def test_to_dict_closed_default():
    cb = CircuitBreaker(failure_threshold=5, open_duration_s=30)
    assert cb.to_dict() == {"state": "closed", "failures": 0,
                            "opened_at": None, "probe_inflight": 0}


def test_from_dict_restores_state_keeps_threshold():
    cb = CircuitBreaker(failure_threshold=5, open_duration_s=30)
    cb.from_dict({"state": "open", "failures": 3, "opened_at": 1234.5, "probe_inflight": 0})
    assert cb.state == CircuitState.OPEN
    assert cb.failures == 3
    assert cb._opened_at == 1234.5
    # 判定参数保持调用方构造值（每次 load 按 run 配置重建）
    assert cb.failure_threshold == 5
    assert cb.open_duration == 30


def test_roundtrip_preserves_probe_inflight():
    cb = CircuitBreaker(failure_threshold=1, open_duration_s=30)
    cb._state = CircuitState.HALF_OPEN
    cb._probe_inflight = 1
    restored = CircuitBreaker(failure_threshold=1, open_duration_s=30)
    restored.from_dict(cb.to_dict())
    assert restored.state == CircuitState.HALF_OPEN
    assert restored._probe_inflight == 1
    assert restored.try_acquire() is False  # 半开单探针约束跨持久化保持


def test_open_duration_uses_wall_clock(monkeypatch):
    """opened_at 落 wall clock（time.time），from_dict 恢复后冷却判定按统一基准。"""
    clock = {"now": 2000.0}
    monkeypatch.setattr("app.core.circuit_breaker.time.time", lambda: clock["now"])
    cb = CircuitBreaker(failure_threshold=1, open_duration_s=30)
    cb.record_failure()  # 2000 转 open
    assert cb.state == CircuitState.OPEN
    assert cb.to_dict()["opened_at"] == 2000.0
    clock["now"] = 2031.0
    assert cb.try_acquire() is True  # 冷却 31s 已过 → 半开放行探针
