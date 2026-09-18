"""7.1 两层并发限流单测（app/core/limiter.py）。

WeightedLimiter：先 per-agent 后全局（防环形等待），同 agent 并发受 per_agent_limit
约束。async 用 asyncio.run + wait_for 超时断言阻塞与释放。
"""
import asyncio
from asyncio_util import run_in_isolated_loop

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
    run_in_isolated_loop(main())


def test_agents_isolated():
    async def main():
        lm = WeightedLimiter(global_limit=16, per_agent_limit=1)
        await lm.acquire("a")
        await asyncio.wait_for(lm.acquire("b"), 0.1)  # 不同 agent 独立信号量，不被 a 挡
    run_in_isolated_loop(main())


def test_global_cap():
    async def main():
        lm = WeightedLimiter(global_limit=1, per_agent_limit=2)
        await lm.acquire("a")
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(lm.acquire("b"), 0.1)  # 全局已被 a 占满
        lm.release("a")
        await asyncio.wait_for(lm.acquire("b"), 0.1)  # 全局归还后 b 放行
    run_in_isolated_loop(main())


def test_release_order_no_leak():
    # 反复 acquire/release 不因漏归还而饿死（release 先全局后 agent 的逆序归还）
    async def main():
        lm = WeightedLimiter(global_limit=16, per_agent_limit=2)
        for _ in range(3):
            await lm.acquire("a")
            lm.release("a")
        await asyncio.wait_for(lm.acquire("a"), 0.1)
    run_in_isolated_loop(main())


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
    run_in_isolated_loop(main())


def test_keyed_drop_run_falls_back_to_default():
    """drop_run 后该 run 回默认桶（16/3），不残留旧限制。"""
    async def main():
        kl = KeyedLimiter()
        kl.set_run(1, global_limit=1, per_agent_limit=1)
        kl.drop_run(1)
        await asyncio.wait_for(kl.acquire(1, "a"), 0.1)   # 默认桶可放行
        await asyncio.wait_for(kl.acquire(1, "b"), 0.1)   # 全局 16 未占满
    run_in_isolated_loop(main())


def test_keyed_unset_run_uses_default():
    """未 set_run 的 run 直接走默认桶，不抛错（测试直用 / 异常路径兜底）。"""
    async def main():
        kl = KeyedLimiter()
        await asyncio.wait_for(kl.acquire(999, "a"), 0.1)
        kl.release(999, "a")
    run_in_isolated_loop(main())


def test_keyed_set_run_updates_bucket():
    """set_run 重复调用替换该 run 桶（新限制立即生效，旧桶丢弃）。"""
    async def main():
        kl = KeyedLimiter()
        kl.set_run(1, global_limit=1, per_agent_limit=1)
        kl.set_run(1, global_limit=16, per_agent_limit=16)
        await asyncio.wait_for(kl.acquire(1, "a"), 0.1)
        await asyncio.wait_for(kl.acquire(1, "b"), 0.1)  # 新限制 16 未占满
    run_in_isolated_loop(main())


# ---------------- §8.3 进程级共享 per-agent 槽池（批 C4a） ----------------
#
# 这组测试的断言口径：**峰值在途数**。判「共享池生效」不能只看「跑没跑完」——两路信号量
# 各跑各的也都能跑完，只有峰值能把「不叠加超限」与「叠加超限」区分开。为此另立一条
# **判别力对照**（test_old_bucket_isolation_would_exceed_pool）：同样的夹具在改造前的
# 旧语义（桶隔离）下峰值必须为 4，否则这组断言是伪断言（怎么实现都绿）。

MANUAL, ERROR = 1, 2          # manual 桶 / error 桶的 run_id
AGENT = "a"


def _manual_bucket(kl):
    """manual 桶：global=16 / per_agent=3（同 `_run` 的 run_config 默认）。"""
    kl.set_run(MANUAL, global_limit=16, per_agent_limit=3)


def _error_bucket(kl):
    """error 桶：global=1 / per_agent=1（§8.3 error 侧逐条复现）。"""
    kl.set_run(ERROR, global_limit=1, per_agent_limit=1)


async def _hold(kl, t, run_id, seconds):
    """占一槽 → 记录峰值 → 持有 seconds → 归还；起跑顺序记入 t['order']。"""
    await kl.acquire(run_id, AGENT)
    t["inflight"] += 1
    t["peak"] = max(t["peak"], t["inflight"])
    t["order"].append(run_id)
    try:
        await asyncio.sleep(seconds)
    finally:
        t["inflight"] -= 1
        kl.release(run_id, AGENT)


def _tracker():
    return {"inflight": 0, "peak": 0, "order": []}


async def _wait_inflight(t, want: int, tries: int = 200) -> None:
    """等到在途数达 want；超时**断言失败**（不写成无界 while，夹具坏了要报错不要挂住）。"""
    for _ in range(tries):
        if t["inflight"] >= want:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"等待在途数 {want} 超时（当前 {t['inflight']}）")


def test_shared_pool_single_run_keeps_per_agent_cap():
    """等价性：只有 manual 时同 agent 峰值仍 == per_agent（既不是 1，也不是任务数 5）。"""
    async def main():
        kl = KeyedLimiter()
        _manual_bucket(kl)
        kl.register_agent(AGENT, 3)
        t = _tracker()
        await asyncio.gather(*[_hold(kl, t, MANUAL, 0.05) for _ in range(5)])
        assert t["peak"] == 3
    run_in_isolated_loop(main())


def test_shared_pool_across_runs_error_queues_behind_manual():
    """§8.3 核心：manual 占满 3 路时 error 的 case 排队，同 agent 峰值恒 == 3（不是 4）。"""
    async def main():
        kl = KeyedLimiter()
        _manual_bucket(kl)
        _error_bucket(kl)
        kl.register_agent(AGENT, 3)          # 池容量 = 配置值 3，**不是** error 桶的 1
        t = _tracker()
        manual = [asyncio.create_task(_hold(kl, t, MANUAL, 0.2)) for _ in range(3)]
        await _wait_inflight(t, 3)           # 等 manual 真的占满（否则下面断言无判别力）
        err = asyncio.create_task(_hold(kl, t, ERROR, 0.05))
        await asyncio.sleep(0.05)            # 排队窗口内 error 必须还没起跑
        assert not err.done()
        assert ERROR not in t["order"]
        assert t["inflight"] == 3
        await asyncio.gather(*manual, err)
        assert t["peak"] == 3
        assert t["order"][-1] == ERROR       # error 排在 manual 之后（FIFO 进槽）
        assert t["order"].count(ERROR) == 1
    run_in_isolated_loop(main())


def test_error_bucket_is_serial():
    """error 桶内 per_agent=1：同 run 的 error case 逐条复现（与共享池无关，防回归）。"""
    async def main():
        kl = KeyedLimiter()
        _error_bucket(kl)
        kl.register_agent(AGENT, 3)
        t = _tracker()
        await asyncio.gather(*[_hold(kl, t, ERROR, 0.05) for _ in range(3)])
        assert t["peak"] == 1
    run_in_isolated_loop(main())


def test_old_bucket_isolation_would_exceed_pool():
    """**判别力对照**：改造前的语义 = 每个 KeyedLimiter 各持一个 per-agent 池（桶间不共享）。

    两份独立实例下同 agent 峰值 == 4（manual 3 + error 1）——这正是 §8.3 要消除的叠加。
    这条测试不覆盖生产代码，它证明上面那组断言**能翻出**该偏离：若哪天共享层被改没、而
    峰值断言仍绿，就说明夹具失效了，而不是实现正确。
    """
    async def main():
        kl_manual, kl_error = KeyedLimiter(), KeyedLimiter()
        _manual_bucket(kl_manual)
        _error_bucket(kl_error)
        kl_manual.register_agent(AGENT, 3)
        kl_error.register_agent(AGENT, 3)
        t = _tracker()
        tasks = [asyncio.create_task(_hold(kl_manual, t, MANUAL, 0.2)) for _ in range(3)]
        await _wait_inflight(t, 3)
        tasks.append(asyncio.create_task(_hold(kl_error, t, ERROR, 0.05)))
        await asyncio.gather(*tasks)
        assert t["peak"] == 4
    run_in_isolated_loop(main())


def test_register_agent_quota_first_wins():
    """容量首次生效：同 agent 二次登记不改变池容量（§8.3 第 4 条，运行期改配置需重启）。"""
    async def main():
        kl = KeyedLimiter()
        _manual_bucket(kl)
        kl.register_agent(AGENT, 3)
        kl.register_agent(AGENT, 5)          # 后登记的值必须被忽略
        t = _tracker()
        await asyncio.gather(*[_hold(kl, t, MANUAL, 0.05) for _ in range(6)])
        assert t["peak"] == 3
    run_in_isolated_loop(main())


def test_shared_pool_survives_new_event_loop():
    """跨 event loop 复用同一实例不炸：`asyncio.Semaphore` 首次 await 即绑定 loop，
    共享池是长生命周期对象（pytest 每用例一个 loop）⇒ 必须能在新 loop 上重建。"""
    kl = KeyedLimiter()
    _manual_bucket(kl)
    kl.register_agent(AGENT, 3)

    async def one_round():
        t = _tracker()
        await asyncio.gather(*[_hold(kl, t, MANUAL, 0.02) for _ in range(4)])
        return t["peak"]

    assert run_in_isolated_loop(one_round()) == 3
    assert run_in_isolated_loop(one_round()) == 3   # 第二个 loop：重建后容量仍是 3
    assert run_in_isolated_loop(one_round()) == 3

