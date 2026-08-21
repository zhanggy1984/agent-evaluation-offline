"""Agent 级熔断器（§15.4）：closed/open/half_open 状态机。

- 连续 failure_threshold 次失败（用例超时/5xx）→ open，后续用例直接标 error；
- open 持续 open_duration 后转 half_open，只放行 1 个探针；
- 探针成功 → 回 closed（计数清零）；失败 → 立即回 open（计数重置，重新计时）。

7.6 C3 多 worker 化：_opened_at 用 wall clock（time.time()，epoch 秒）而非 time.monotonic()——
DB 持久化的 opened_at 需跨进程/跨重启可比（进程重启 monotonic 归零会令冷却立即失效）。
状态可 to_dict()/from_dict() 往返持久化（threshold/open_duration 由调用方按 run 配置构造，
不进 dict——每 run 级参数一致，last-write-wins 不影响判定）。
"""
import time
from enum import Enum


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, open_duration_s: float = 30.0) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold 必须 ≥1")
        self.failure_threshold = failure_threshold
        self.open_duration = open_duration_s
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at: float | None = None
        self._probe_inflight = 0  # half_open 同时只放行 1 个探针

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def failures(self) -> int:
        return self._failures

    def to_dict(self) -> dict:
        """持久化快照（state 用 str 值，DB 列 String 直存）。"""
        return {
            "state": self._state.value,
            "failures": self._failures,
            "opened_at": self._opened_at,
            "probe_inflight": self._probe_inflight,
        }

    def from_dict(self, data: dict) -> "CircuitBreaker":
        """从持久化快照恢复（threshold/open_duration 保留调用方构造值）。"""
        self._state = CircuitState(data.get("state", CircuitState.CLOSED.value))
        self._failures = int(data.get("failures", 0))
        self._opened_at = data.get("opened_at")
        self._probe_inflight = int(data.get("probe_inflight", 0))
        return self

    def record_success(self) -> None:
        self._failures = 0
        if self._state == CircuitState.HALF_OPEN:
            self._probe_inflight = 0
            self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        if self._state == CircuitState.HALF_OPEN:
            # 探针失败 → 立即回 open，计数重置（重新累计连续失败）
            self._probe_inflight = 0
            self._open()
            return
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._open()

    def try_acquire(self) -> bool:
        """是否放行本次用例：
        closed → True；open 冷却已到 → 转 half_open 放行探针；open 冷却未到 → False；
        half_open 已有探针在飞 → False（单探针约束）。
        """
        if self._state == CircuitState.CLOSED:
            return True
        if self._state == CircuitState.OPEN:
            if self._opened_at is not None and time.time() - self._opened_at >= self.open_duration:
                self._state = CircuitState.HALF_OPEN
            else:
                return False
        # half_open：单探针
        if self._probe_inflight >= 1:
            return False
        self._probe_inflight += 1
        return True

    def release_probe(self) -> None:
        """探针结束（无论成败）都必须调用，归还 half_open 放行名额。"""
        if self._probe_inflight > 0:
            self._probe_inflight -= 1

    def _open(self) -> None:
        self._failures = 0
        self._state = CircuitState.OPEN
        self._opened_at = time.time()
