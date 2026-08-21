"""7.1 两层并发限流单测（app/core/limiter.py）。

WeightedLimiter：先 per-agent 后全局（防环形等待），同 agent 并发受 per_agent_limit
约束。async 用 asyncio.run + wait_for 超时断言阻塞与释放。
"""
import asyncio

import pytest

from app.core.limiter import KeyedLimiter, WeightedLimiter


def test_invalid_params():
    with pytest.raises(ValueError):
        WeightedLimiter(global_limit=0)
    with pytest.raises(ValueError):
        WeightedLimiter(per_agent_limit=0)


def test_per_agent_blocking_and_release():
    async def main():
        lm = WeightedLimiter(global_limit=16, per_agent_limit=2)
        await lm.acquire("a")
        await lm.acquire("a")
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(lm.acquire("a"), 0.1)  # 第 3 个并发被 per-agent 挡住
        lm.release("a")
        await asyncio.wait_for(lm.acquire("a"), 0.1)  # 释放后放行
    asyncio.run(main())


def test_agents_isolated():
    async def main():
        lm = WeightedLimiter(global_limit=16, per_agent_limit=1)
        await lm.acquire("a")
        await asyncio.wait_for(lm.acquire("b"), 0.1)  # 不同 agent 独立信号量，不被 a 挡
    asyncio.run(main())


def test_global_cap():
    async def main():
        lm = WeightedLimiter(global_limit=1, per_agent_limit=2)
        await lm.acquire("a")
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(lm.acquire("b"), 0.1)  # 全局已被 a 占满
        lm.release("a")
        await asyncio.wait_for(lm.acquire("b"), 0.1)  # 全局归还后 b 放行
    asyncio.run(main())


def test_release_order_no_leak():
    # 反复 acquire/release 不因漏归还而饿死（release 先全局后 agent 的逆序归还）
    async def main():
        lm = WeightedLimiter(global_limit=16, per_agent_limit=2)
        for _ in range(3):
            await lm.acquire("a")
            lm.release("a")
        await asyncio.wait_for(lm.acquire("a"), 0.1)
    asyncio.run(main())


def test_keyed_run_buckets_isolated():
    """per-run 桶隔离：run1 全局占满不挡 run2（旧 set_limits last-write-wins 会互相覆盖）。"""
    async def main():
        kl = KeyedLimiter()
        kl.set_run(1, global_limit=1, per_agent_limit=2)
        kl.set_run(2, global_limit=16, per_agent_limit=16)
        await kl.acquire(1, "a")          # run1 全局被 a 占满
        await asyncio.wait_for(kl.acquire(2, "b"), 0.1)  # run2 独立桶，不受 run1 影响
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(kl.acquire(1, "b"), 0.1)  # run1 全局仍满
        kl.release(1, "a")
        await asyncio.wait_for(kl.acquire(1, "b"), 0.1)  # 归还后 run1 放行
    asyncio.run(main())


def test_keyed_drop_run_falls_back_to_default():
    """drop_run 后该 run 回默认桶（16/3），不残留旧限制。"""
    async def main():
        kl = KeyedLimiter()
        kl.set_run(1, global_limit=1, per_agent_limit=1)
        kl.drop_run(1)
        await asyncio.wait_for(kl.acquire(1, "a"), 0.1)   # 默认桶可放行
        await asyncio.wait_for(kl.acquire(1, "b"), 0.1)   # 全局 16 未占满
    asyncio.run(main())


def test_keyed_unset_run_uses_default():
    """未 set_run 的 run 直接走默认桶，不抛错（测试直用 / 异常路径兜底）。"""
    async def main():
        kl = KeyedLimiter()
        await asyncio.wait_for(kl.acquire(999, "a"), 0.1)
        kl.release(999, "a")
    asyncio.run(main())


def test_keyed_set_run_updates_bucket():
    """set_run 重复调用替换该 run 桶（新限制立即生效，旧桶丢弃）。"""
    async def main():
        kl = KeyedLimiter()
        kl.set_run(1, global_limit=1, per_agent_limit=1)
        kl.set_run(1, global_limit=16, per_agent_limit=16)
        await asyncio.wait_for(kl.acquire(1, "a"), 0.1)
        await asyncio.wait_for(kl.acquire(1, "b"), 0.1)  # 新限制 16 未占满
    asyncio.run(main())
