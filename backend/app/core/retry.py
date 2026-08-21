"""指数退避重试（§15.4）。judge 调用与用例请求共用。"""
import asyncio
import random


class RetryExhausted(Exception):
    """所有重试均失败。"""


async def retry_with_backoff(fn, *, attempts: int = 2, base_delay: float = 0.5,
                             max_delay: float = 10.0, jitter: bool = True):
    """fn 为 async callable；最后一次失败原样抛出（由调用方归类）。

    attempts=总尝试次数（含首次）；delay = base * 2^i，上限 max_delay，默认带 ±50% 抖动。
    """
    if attempts < 1:
        raise ValueError("attempts 必须 ≥1")
    for i in range(attempts):
        try:
            return await fn()
        except Exception:
            if i == attempts - 1:
                raise
            delay = min(max_delay, base_delay * (2 ** i))
            if jitter:
                delay *= 0.5 + random.random() * 0.5
            await asyncio.sleep(delay)
    # 不可达（循环内必然 return 或抛），仅为类型满足
    raise RetryExhausted("unreachable")
