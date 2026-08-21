"""7.6 C3 熔断器跨 worker 持久化存取（agent_circuit 表，读-改-写无锁）。

设计要点：
- load：读快照到 CircuitBreaker 实例（threshold/open_duration 由调用方按 run 配置构造）；
  无行时 INSERT 默认 closed 行并立即 commit（并发首次访问用 IntegrityError 兜底重查，
  两 worker 同时建行只会留下一个）。
- save：to_dict() 快照写回 + commit。last-write-wins，不加 FOR UPDATE——
  熔断状态机的丢失更新最坏是计数偏差 ±1（熔断延迟/提前一档触发），
  不破坏「open 拦截失败流量 / 冷却后放行探针 / 探针成败决定恢复」关键不变量；
  且同一 agent 的并发用例本就受 per_agent 限流约束，高频写放行可接受。
- 状态行随 agent 软删 CASCADE 清理。
"""
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.circuit_breaker import CircuitBreaker
from app.models.misc import AgentCircuit


async def load(db, agent_id: int, breaker: CircuitBreaker | None = None) -> CircuitBreaker:
    """读 agent 熔断快照；无行则建默认 closed 行（独立 commit，幂等）。"""
    b = breaker or CircuitBreaker()
    row = (await db.execute(select(AgentCircuit).where(
        AgentCircuit.agent_id == agent_id))).scalar_one_or_none()
    if row is None:
        db.add(AgentCircuit(agent_id=agent_id, state="closed",
                            failures=0, opened_at=None, probe_inflight=0))
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()  # 并发首次访问：另一 worker 已建行，重查即可
        row = (await db.execute(select(AgentCircuit).where(
            AgentCircuit.agent_id == agent_id))).scalar_one()
    return b.from_dict({
        "state": row.state,
        "failures": row.failures,
        "opened_at": row.opened_at,
        "probe_inflight": row.probe_inflight,
    })


async def save(db, agent_id: int, breaker: CircuitBreaker) -> None:
    """快照写回 + commit（last-write-wins；调用方持同一 db session）。"""
    d = breaker.to_dict()
    row = (await db.execute(select(AgentCircuit).where(
        AgentCircuit.agent_id == agent_id))).scalar_one_or_none()
    if row is None:
        db.add(AgentCircuit(agent_id=agent_id, **d))
    else:
        row.state = d["state"]
        row.failures = d["failures"]
        row.opened_at = d["opened_at"]
        row.probe_inflight = d["probe_inflight"]
    await db.commit()
