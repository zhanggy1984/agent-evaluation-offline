"""跨 worker 行锁工具（多 worker 化，§15）。

单进程部署下内存锁（orchestrator._breakers 等）成立；多 worker 后内存不共享，
DB 行锁（SELECT ... FOR UPDATE）是唯一可靠的跨进程互斥（设计文档定案：
「agent 行 SELECT ... FOR UPDATE，不用 GET_LOCK」——GET_LOCK 只适合秒级短锁，
本模块 agent_mutex 持锁期间可能做秒级网络调用，但事务窗口受控）。

用途：
- C1 run 创建互斥（runs.py create_run/rerun）：锁 agent 行 → 查 active run → 插 run → commit
- B3 reset(seed) per-agent 串行（orchestrator）：reset 网络调用在锁内，execute 才并发
"""
from contextlib import asynccontextmanager

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import Agent


@asynccontextmanager
async def agent_mutex(agent_id: int, db=None):
    """持 agent 行 FOR UPDATE 锁串行化同一 agent 的临界区（跨 worker）。

    - db 传入：用调用方事务（调用方负责 commit，锁随其提交释放），适用于
      create_run 这类「锁 + 校验 + 写库」需同事务的场景。
    - db 缺省：自开 SessionLocal，yield 后显式 commit 释放锁，适用于
      reset(seed) 这类「只需串行、无需同事务读写」的场景。
    """
    if db is not None:
        await db.execute(select(Agent.id).where(Agent.id == agent_id).with_for_update())
        yield
        return
    async with SessionLocal() as s:
        await s.execute(select(Agent.id).where(Agent.id == agent_id).with_for_update())
        yield
        await s.commit()
