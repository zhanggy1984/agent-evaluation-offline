"""两层并发限流（§15.4）：先 per-agent 后全局（加权信号量，防饿死）。

acquire 顺序：agent 信号量 → 全局信号量。
- 若先拿全局再拿 agent：agent A 占满全局、agent B 占满全局时互相等对方 agent 信号量 → 环形等待；
- 先 agent 后全局：同一 agent 的并发先被 agent 信号量收窄，全局只是总量兜底，无环。
release 逆序归还（先全局后 agent），两者都必须归还。
"""
import asyncio
import logging

logger = logging.getLogger(__name__)


def _running_loop() -> asyncio.AbstractEventLoop | None:
    """当前 running loop；不在协程内返回 None（release 的兜底判定用）。"""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


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

    §8.3 进程级共享 per-agent 层（M5 前置，2026-09-07 裁决 A）：桶内 per-agent 信号量
    **桶间不共享**——manual 桶 3 路 + error 桶 1 路 = 同 agent 实际 4 路，与 phase2 R2
    「同 agent 并发总数 ≤ per-agent 上限、不叠加超限」冲突。故在**桶之外**再加一层
    per-agent 池：acquire 序 = 共享池 → 桶内（per-agent → global），release 逆序。
    共享池容量取该 agent 的 `per_agent_concurrency` 配置值（**不是**传入的桶内限额：
    error 桶 per_agent=1，拿它当池容量会把 manual 拖成串行）。
    """
    def __init__(self, default_global: int = 16, default_per_agent: int = 3) -> None:
        self._default = WeightedLimiter(default_global, default_per_agent)
        self._buckets: dict[int, WeightedLimiter] = {}
        self._default_quota = default_per_agent
        self._agent_quota: dict[str, int] = {}
        # agent_key -> (绑定它的 event loop, 信号量)。loop 随条目一起存见 _agent_sem。
        self._agent_sems: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Semaphore]] = {}

    def register_agent(self, agent_key: str, quota: int) -> None:
        """登记该 agent 的共享池容量（幂等：**首次生效**，§8.3 第 4 条——运行期改配置需重启）。"""
        self._agent_quota.setdefault(agent_key, max(1, int(quota)))

    def _agent_sem(self, agent_key: str) -> asyncio.Semaphore:
        """取（懒建）该 agent 的共享槽池信号量。

        ⚠️ 存 loop 不是洁癖：`asyncio.Semaphore` 在**首次 await 时**绑定当时的 running loop，
        跨 loop 复用抛 RuntimeError。per-run 桶靠「每 run 建、收尾删」躲过了这点，而共享池是
        **长生命周期对象**（进程级、随 agent 存活），必然跨 loop（pytest 每用例一个 loop、
        探针各起 asyncio.run）。loop 变了就重建——旧 loop 已结束，其上无在途等待者。
        """
        loop = asyncio.get_running_loop()
        entry = self._agent_sems.get(agent_key)
        if entry is None or entry[0] is not loop:
            entry = (loop, asyncio.Semaphore(
                self._agent_quota.get(agent_key, self._default_quota)))
            self._agent_sems[agent_key] = entry
        return entry[1]

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
        """先取共享池槽，再取桶内（per-agent → global）；桶内没占到位就不算占位。"""
        sem = self._agent_sem(agent_id)
        await sem.acquire()
        try:
            await self._bucket(run_id).acquire(agent_id)
        except BaseException:
            # 含 CancelledError（wait_for 超时取消）：桶内未占位成功 ⇒ 归还共享槽，
            # 否则每取消一次就漏一个池额度，最终把该 agent 饿死。
            sem.release()
            raise

    def release(self, run_id: int, agent_id: str) -> None:
        self._bucket(run_id).release(agent_id)   # 桶内逆序：先全局后 per-agent
        entry = self._agent_sems.get(agent_id)
        loop = _running_loop()
        if entry is None or entry[0] is not loop:
            # 只可能来自「acquire 与 release 不在同一 loop」的调用方缺陷（正常路径
            # try/finally 同协程）。此处**不新建**信号量顶替：新建即超发（归还到容量
            # 之外的新 sem，计数 > 容量），是静默放行；宁可少归还并留一条告警。
            logger.warning("共享槽归还失败：agent=%s 无本 loop 的共享信号量（acquire/release 跨 loop？）",
                           agent_id)
            return
        entry[1].release()
