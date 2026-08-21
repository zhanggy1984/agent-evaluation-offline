"""7.5b 副作用接口只执行一次：orchestrator 消费 interface.retryable。

覆盖（全 mock execute_case，宿主可跑）：
- _call_once：retryable=False 首败即返（execute_case 恰好 1 次）；True 对照重试 max_retries+1 次
- _execute_with_retry：retryable=False 即使性能维度开启也不重复采样（attempts=1）；True 对照采 repeat 次
"""
import pytest

from app.models import AgentInterface
from app.runner.executor import CaseOutcome
from app.runner.orchestrator import RunOrchestrator

pytestmark = pytest.mark.asyncio


class _Agent:
    id = 1
    adapter_config = {}
    base_url = "http://mock.local"


class _Case:
    id = 1
    interface_id = 1
    metrics = {"ttft": {"enabled": True}}  # perf_needed=True：触发重复采样分支


def _make_interface(retryable: bool) -> AgentInterface:
    return AgentInterface(agent_id=1, name="it75-iface", path="/v1/chat", method="POST",
                          contract_type="sse", contract_version="1.0", enabled=True,
                          retryable=retryable)


def _fail() -> CaseOutcome:
    return CaseOutcome(error_type="timeout", error_detail="inject", timing={}, status_code=500)


def _ok() -> CaseOutcome:
    return CaseOutcome(unified={"answer": "ok", "usage": {"total_tokens": 1},
                                "meta": {"model": "mock"}},
                       timing={"first_token_ts": 0.1, "end_ts": 0.2}, status_code=200)


async def test_call_once_non_retryable_no_retry(monkeypatch):
    """retryable=False：可重试错误（timeout）也不重试，execute_case 恰好调 1 次。"""
    calls: list[int] = []

    async def fake_execute(adapter, client, case, timeout_s):
        calls.append(1)
        return _fail()

    monkeypatch.setattr("app.runner.orchestrator.execute_case", fake_execute)
    orch = RunOrchestrator()
    out = await orch._call_once(None, None, _Case(), 30, 3, retryable=False)
    assert out.error_type == "timeout"
    assert len(calls) == 1  # 副作用接口首败即返，不重试


async def test_call_once_retryable_retries(monkeypatch):
    """retryable=True（默认）：可重试错误按指数退避重试 max_retries+1 次。"""
    calls: list[int] = []

    async def fake_execute(adapter, client, case, timeout_s):
        calls.append(1)
        return _fail()

    monkeypatch.setattr("app.runner.orchestrator.execute_case", fake_execute)
    orch = RunOrchestrator()
    out = await orch._call_once(None, None, _Case(), 30, 1, retryable=True)
    assert out.error_type == "timeout"
    assert len(calls) == 2  # max_retries(1)+1


async def test_execute_with_retry_non_retryable_no_repeat_sampling(monkeypatch):
    """retryable=False + 性能维度开启：不重复采样（attempts=1），成功也只调 1 次。"""
    calls: list[int] = []

    async def fake_execute(adapter, client, case, timeout_s):
        calls.append(1)
        return _ok()

    monkeypatch.setattr("app.runner.orchestrator.execute_case", fake_execute)
    orch = RunOrchestrator()

    async def fake_get_interface(interface_id):
        return _make_interface(retryable=False)

    def fake_is_cancelled(run_id):
        return False

    async def fake_save(*args, **kwargs):
        pass

    monkeypatch.setattr(orch, "_get_interface", fake_get_interface)
    monkeypatch.setattr(orch, "_is_cancelled", fake_is_cancelled)
    monkeypatch.setattr(orch, "_save_result", fake_save)

    out = await orch._execute_with_retry(1, None, _Agent(), _Case(), {}, None, 30, 3, 2)
    assert out.ok
    assert len(calls) == 1  # 副作用接口不做性能重复采样


async def test_execute_with_retry_retryable_repeats_sampling(monkeypatch):
    """retryable=True（默认）+ 性能维度开启：成功重复采样 repeat 次。"""
    calls: list[int] = []

    async def fake_execute(adapter, client, case, timeout_s):
        calls.append(1)
        return _ok()

    monkeypatch.setattr("app.runner.orchestrator.execute_case", fake_execute)
    orch = RunOrchestrator()

    async def fake_get_interface(interface_id):
        return _make_interface(retryable=True)

    def fake_is_cancelled(run_id):
        return False

    async def fake_save(*args, **kwargs):
        pass

    monkeypatch.setattr(orch, "_get_interface", fake_get_interface)
    monkeypatch.setattr(orch, "_is_cancelled", fake_is_cancelled)
    monkeypatch.setattr(orch, "_save_result", fake_save)

    out = await orch._execute_with_retry(1, None, _Agent(), _Case(), {}, None, 30, 3, 2)
    assert out.ok
    assert len(calls) == 3  # repeat=3 采样（成功才继续）
