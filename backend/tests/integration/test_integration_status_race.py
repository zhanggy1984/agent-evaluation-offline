"""P2-D6 status 并发写锁测试（真库）。

覆盖 _finish / scanner / score_run 三处 status 写入方在 REPEATABLE READ 下的并发语义：
- _finish 锁定读：scanner 已标 timeout → 不覆盖（外部终态保护，只对账）
- scanner 条件 UPDATE：快照选中（running/lease 过期）期间 run 被推进 completed
  → rowcount=0 不覆盖（防旧快照无条件 UPDATE 吞掉新状态）
- _reap_one_pass 端到端：lease 过期正常回收标 timeout（rowcount 收集进 reaped）；
  completed run 不误标（SELECT 条件 + 条件 UPDATE 双重保护）
- score_run 锁定读：scoring_failed 的 run 幂等返回，不评分不翻转

条件 UPDATE 与 _reap_one_pass 中同语句（WHERE 追加 status 前置校验）；SELECT 快照
与写入交错通过两个独立 session 模拟（同 D2 mutex 测试模式）。
"""
import pytest

pytest.importorskip("aiomysql")

from datetime import datetime, timedelta

from sqlalchemy import select, update

from app.core.db import SessionLocal
from app.models import EvalRun
from app.runner import scanner
from app.runner.orchestrator import orchestrator
from app.runner.scanner import _LEASE_GRACE
from app.runner.scorer import score_run
from helpers import create_chain, make_run


def _noop(*args, **kwargs):
    """monkeypatch 同步副作用（cancel_run）：本测试只关注状态机。"""
    return None


async def _anoop(*args, **kwargs):
    """monkeypatch 协程副作用（score_run_salvage/score_run 在 scanner 中被 await）。"""
    return None


async def _get_status(run_id: int) -> str:
    async with SessionLocal() as s:
        r = await s.get(EvalRun, run_id)
        return r.status


async def _reg(env, db, ch, run) -> int:
    """登记 env + commit，返回 run_id。"""
    env.agents.append(ch["agent"])
    env.interfaces.append(ch["iface"])
    env.suites.append(ch["suite"])
    env.cases.extend(ch["cases"])
    db.add(run)
    await db.commit()
    env.runs.append(run)
    return run.id


# ---------------- _finish：外部终态保护 ----------------
@pytest.mark.asyncio(loop_scope="session")
async def test_finish_keeps_scanner_timeout(env, db):
    """scanner 已标 timeout commit 后，_finish 锁定读读到最新，不覆盖 status（只对账统计）。

    普通读（修复前）在本事务快照里读到旧值 running → external_terminal=False → 覆盖成
    scoring/completed，scanner 的 timeout 被吞。锁定读永远读最新已提交。
    """
    ch = await create_chain(db)
    run = make_run(ch["agent"].id, ch["suite"].id)
    run.status = "running"
    run.lease_until = datetime.utcnow() - timedelta(hours=1)
    run.hard_deadline = datetime.utcnow() - timedelta(minutes=1)
    run.total_case = len(ch["cases"])
    run_id = await _reg(env, db, ch, run)

    # 模拟 scanner 已回收：独立 session 标 timeout 并提交
    async with SessionLocal() as s:
        r = await s.get(EvalRun, run_id)
        r.status = "timeout"
        r.finished_at = datetime.utcnow()
        await s.commit()

    await orchestrator._finish(run_id)

    assert await _get_status(run_id) == "timeout"  # 不覆盖
    async with SessionLocal() as s:
        r = await s.get(EvalRun, run_id)
        assert r.total_case == len(ch["cases"])  # 只对账统计，status 保持


# ---------------- scanner：条件 UPDATE 不覆盖已推进状态 ----------------
@pytest.mark.asyncio(loop_scope="session")
async def test_scanner_conditional_update_no_overwrite(env, db):
    """scanner 快照选中 running（lease 过期）期间 run 被推进 completed → 条件 UPDATE rowcount=0。

    修复前对象赋值（UPDATE WHERE id 无状态条件）会用旧快照把 completed 覆盖回 timeout；
    条件 UPDATE（仍为 pending/running 才标）让后写方不覆盖先写方。
    """
    ch = await create_chain(db)
    run = make_run(ch["agent"].id, ch["suite"].id)
    run.status = "running"
    run.lease_until = datetime.utcnow() - timedelta(hours=1)
    run_id = await _reg(env, db, ch, run)
    now = datetime.utcnow()

    # session A：模拟 scanner 事务快照（SELECT 选中，不 commit，快照已建立）
    async with SessionLocal() as s_a:
        rows = (await s_a.execute(select(EvalRun).where(
            EvalRun.status.in_(("pending", "running")),
            EvalRun.lease_until.isnot(None),
            EvalRun.lease_until < now - timedelta(seconds=_LEASE_GRACE),
        ))).scalars().all()
        assert run_id in {r.id for r in rows}  # 快照里确实选中

        # session B：_finish 推进 completed（独立事务）
        async with SessionLocal() as s_b:
            r2 = await s_b.get(EvalRun, run_id)
            r2.status = "completed"
            r2.finished_at = now
            await s_b.commit()

        # A 执行与 _reap_one_pass 相同的条件 UPDATE → 当前读读到 completed，条件不满足
        res = await s_a.execute(
            update(EvalRun)
            .where(EvalRun.id == run_id,
                   EvalRun.status.in_(("pending", "running")))
            .values(status="timeout", finished_at=now))
        assert res.rowcount == 0  # 不覆盖

    assert await _get_status(run_id) == "completed"


# ---------------- _reap_one_pass 端到端 ----------------
@pytest.mark.asyncio(loop_scope="session")
async def test_reap_pass_marks_expired_timeout(env, db, monkeypatch):
    """lease 过期 running run 被正常回收标 timeout，rowcount 精确收集进 reaped。"""
    ch = await create_chain(db)
    run = make_run(ch["agent"].id, ch["suite"].id)
    run.status = "running"
    run.lease_until = datetime.utcnow() - timedelta(hours=1)
    run_id = await _reg(env, db, ch, run)

    monkeypatch.setattr(scanner, "score_run_salvage", _anoop)
    monkeypatch.setattr(scanner, "score_run", _anoop)
    monkeypatch.setattr(orchestrator, "cancel_run", _noop)

    reaped_count = await scanner._reap_one_pass()
    assert reaped_count == 1  # 本文件此场景仅此一个过期 run 被回收
    async with SessionLocal() as s:
        r = await s.get(EvalRun, run_id)
        assert r.status == "timeout"
        assert r.finished_at is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_reap_pass_skips_completed(env, db, monkeypatch):
    """completed run（lease 过期）不被回收：SELECT 状态条件排除 + 条件 UPDATE 双重保护。"""
    ch = await create_chain(db)
    run = make_run(ch["agent"].id, ch["suite"].id)
    run.status = "completed"
    run.finished_at = datetime.utcnow()
    run.lease_until = datetime.utcnow() - timedelta(hours=1)  # 已过期但已终态
    run_id = await _reg(env, db, ch, run)

    monkeypatch.setattr(scanner, "score_run_salvage", _anoop)
    monkeypatch.setattr(scanner, "score_run", _anoop)

    reaped_count = await scanner._reap_one_pass()
    assert reaped_count == 0  # completed 不在回收范围（SELECT 条件排除 + 条件 UPDATE 双保险）
    assert await _get_status(run_id) == "completed"


# ---------------- score_run：锁定读幂等 ----------------
@pytest.mark.asyncio(loop_scope="session")
async def test_score_run_skips_scoring_failed(env, db):
    """scanner 已标 scoring_failed 的 run：score_run 锁定读读到最新，幂等返回不评分不翻转。"""
    ch = await create_chain(db)
    run = make_run(ch["agent"].id, ch["suite"].id)
    run.status = "scoring_failed"
    run.finished_at = datetime.utcnow()
    run_id = await _reg(env, db, ch, run)

    await score_run(run_id)

    assert await _get_status(run_id) == "scoring_failed"  # 不翻转回 completed
    async with SessionLocal() as s:
        r = await s.get(EvalRun, run_id)
        assert r.agent_score is None  # 未评分
