"""两层并发限流（§15.4）：先 per-agent 后全局（加权信号量，防饿死）。

acquire 顺序：agent 信号量 → 全局信号量。
- 若先拿全局再拿 agent：agent A 占满全局、agent B 占满全局时互相等对方 agent 信号量 → 环形等待；
- 先 agent 后全局：同一 agent 的并发先被 agent 信号量收窄，全局只是总量兜底，无环。
release 逆序归还（先全局后 agent），两者都必须归还。
"""
import asyncio


class WeightedLimiter:
    def __init__(self, global_limit: int = 16, per_agent_limit: int = 3) -> None:
        if global_limit < 1 or per_agent_limit < 1:
            raise ValueError("并发限制必须 ≥1")
        self._global = asyncio.Semaphore(global_limit)
        self._per_agent_limit = per_agent_limit
        self._per_agent: dict[str, asyncio.Semaphore] = {}

    def _agent_sem(self, agent_id: str) -> asyncio.Semaphore:
        sem = self._per_agent.get(agent_id)
        if sem is None:
            sem = asyncio.Semaphore(self._per_agent_limit)
            self._per_agent[agent_id] = sem
        return sem

    async def acquire(self, agent_id: str) -> None:
        await self._agent_sem(agent_id).acquire()
        await self._global.acquire()

    def release(self, agent_id: str) -> None:
        self._global.release()
        self._agent_sem(agent_id).release()


class KeyedLimiter:
    """per-run 并发限流（7.8 前置④）：每个 run 一个独立桶，run 间并发互不干扰。

    背景：旧 set_limits 是 last-write-wins 替换进程内共享单例——多 run 并发时后创建的
    run 会覆盖先创建 run 的 global/per_agent 参数，先 run 实际用错限流。
    KeyedLimiter 以 run_id 分桶，set_run 建/更新该 run 桶、drop_run 收尾清理防泄漏、
    未设置 run（含测试/兜底）用默认桶。

    acquire/release 语义与 WeightedLimiter 一致（先 per-agent 后全局，防环形等待）。
    """
    def __init__(self, default_global: int = 16, default_per_agent: int = 3) -> None:
        self._default = WeightedLimiter(default_global, default_per_agent)
        self._buckets: dict[int, WeightedLimiter] = {}

    def set_run(self, run_id: int, global_limit: int, per_agent_limit: int) -> None:
        """为该 run 建/更新限流桶（last-write-wins；旧桶在途信号量随对象丢弃不再归还）。
        调用方保证在 run 执行前调用、执行结束后 drop_run，acquire/release 落在同一桶。"""
        self._buckets[run_id] = WeightedLimiter(global_limit, per_agent_limit)

    def drop_run(self, run_id: int) -> None:
        """run 收尾清理桶（run 全部 case 已归还信号量后才可调用）。"""
        self._buckets.pop(run_id, None)

    def _bucket(self, run_id: int) -> WeightedLimiter:
        # acquire/release 均在 set_run..drop_run 窗口内，必命中该 run 桶；
        # 兜底默认桶仅覆盖未 set_run 的调用（测试直用 / 异常路径）。
        return self._buckets.get(run_id) or self._default

    async def acquire(self, run_id: int, agent_id: str) -> None:
        await self._bucket(run_id).acquire(agent_id)

    def release(self, run_id: int, agent_id: str) -> None:
        self._bucket(run_id).release(agent_id)
