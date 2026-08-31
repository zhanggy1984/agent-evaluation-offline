"""P2-D2 run 触发互斥 + agent_mutex 行锁测试（真库，FOR UPDATE 仅 InnoDB 生效）。

覆盖 app/core/lock.py（零测试）与 create_run 触发互斥分支：
- agent_mutex(db) 跨事务并发互斥：task A 持锁，task B 的 FOR UPDATE 被阻塞至 A 提交
- agent_mutex() 无 db 自开 session：内部 commit 释放锁，并发同样串行
- create_run：有 active run → 409 E_RUN_MUTEX；无 active → 创建成功 pending 落库
- create_run 并发触发同 agent（各自独立 session）→ 恰一个成功一个 409，且仅 1 条 run

关键设计：
- 持锁操作一律走 `async with SessionLocal()` 显式块：409 等异常路径退出即回滚释放锁，
  避免在共享 db fixture 上残留 FOR UPDATE 锁（否则 env 清理的独立 session 会被锁卡死）。
- create_run 内部 create_task(orchestrator.start_run) monkeypatch 成 no-op，
  隔离「触发创建」互斥逻辑（真实执行已由 test_integration_run 覆盖）。
"""
import asyncio
import time
import types

import pytest

pytest.importorskip("aiomysql")

from sqlalchemy import delete, select

from app.api import runs as runs_mod
from app.core.db import SessionLocal
from app.core.errors import ApiError, E_RUN_MUTEX, E_VALIDATION
from app.core.lock import agent_mutex
from app.models import EvalRun
from app.models.misc import AuditLog
from app.runner.orchestrator import orchestrator
from helpers import create_chain, make_agent, make_run

_user = types.SimpleNamespace(role="admin")

# rerun 成功路径走 write_audit：需要 user.id/username + request.headers（X-Forwarded-For，None 兜底 client.host）
_staff = types.SimpleNamespace(id=1, username="it-d2-staff", role="admin")


def _audit_req(ip: str):
    """fake Request：rerun 审计读 headers 的 x-forwarded-for；无则 client.host。"""
    return types.SimpleNamespace(
        headers={"x-forwarded-for": None},
        client=types.SimpleNamespace(host=ip),
    )


async def _noop_start(*args, **kwargs):
    """屏蔽 create_run 内 create_task(orchestrator.start_run)：本测试只关注触发互斥。"""
    return None


# ---------------- agent_mutex 行锁 ----------------
@pytest.mark.asyncio(loop_scope="session")
async def test_agent_mutex_with_db_serializes(env):
    async with SessionLocal() as s:
        ag = make_agent()
        s.add(ag)
        await s.commit()
        env.agents.append(ag)
        agent_id = ag.id

    ready = asyncio.Event()
    entered_a = 0.0
    entered_b = 0.0

    async def task_a():
        nonlocal entered_a
        async with SessionLocal() as s:
            async with agent_mutex(agent_id, s):
                entered_a = time.monotonic()
                ready.set()  # A 已持锁，放行 B（B 的 FOR UPDATE 必然被阻塞）
                await asyncio.sleep(0.3)  # 持锁窗口（模拟锁内网络调用）
            await s.commit()  # 调用方提交才释放锁

    async def task_b():
        nonlocal entered_b
        await ready.wait()  # 确保 A 先持锁，避免 gather 调度竞态翻转断言时序
        async with SessionLocal() as s:
            async with agent_mutex(agent_id, s):
                entered_b = time.monotonic()
            await s.commit()

    await asyncio.gather(task_a(), task_b())
    # B 被 A 的 FOR UPDATE 阻塞约 0.3s（db 传入路径：锁随调用方事务释放）
    assert entered_b - entered_a >= 0.25, f"B 应被阻塞，实际间隔 {entered_b - entered_a:.3f}s"


@pytest.mark.asyncio(loop_scope="session")
async def test_agent_mutex_self_session_serializes(env):
    async with SessionLocal() as s:
        ag = make_agent()
        s.add(ag)
        await s.commit()
        env.agents.append(ag)
        agent_id = ag.id

    ready = asyncio.Event()
    entered_a = 0.0
    entered_b = 0.0

    async def task_a():
        nonlocal entered_a
        async with agent_mutex(agent_id):  # 无 db：自开 session，yield 后内部 commit 释放锁
            entered_a = time.monotonic()
            ready.set()  # A 已持锁，放行 B（B 的 FOR UPDATE 必然被阻塞）
            await asyncio.sleep(0.3)

    async def task_b():
        nonlocal entered_b
        await ready.wait()  # 确保 A 先持锁，避免 gather 调度竞态翻转断言时序
        async with agent_mutex(agent_id):
            entered_b = time.monotonic()

    await asyncio.gather(task_a(), task_b())
    assert entered_b - entered_a >= 0.25, f"B 应被阻塞，实际间隔 {entered_b - entered_a:.3f}s"


# ---------------- create_run 触发互斥 ----------------
@pytest.mark.asyncio(loop_scope="session")
async def test_create_run_active_run_409(env, monkeypatch):
    monkeypatch.setattr(orchestrator, "start_run", _noop_start)
    async with SessionLocal() as s:
        ch = await create_chain(s)
        run = make_run(ch["agent"].id, ch["suite"].id)  # status=pending 属于 _ACTIVE_STATUS
        s.add(run)
        await s.commit()
        env.agents.append(ch["agent"]); env.interfaces.append(ch["iface"])
        env.suites.append(ch["suite"]); env.cases.extend(ch["cases"]); env.runs.append(run)
        with pytest.raises(ApiError) as ei:
            await runs_mod.create_run(
                runs_mod.RunCreate(agent_id=ch["agent"].id, suite_id=ch["suite"].id, version="1.2.3"),
                _user, s)
        assert ei.value.status_code == 409
        assert ei.value.code == E_RUN_MUTEX
    # 409 后 with 块退出回滚释放锁，env 清理的独立 session 可正常删行


@pytest.mark.asyncio(loop_scope="session")
async def test_create_run_no_active_ok(env, monkeypatch):
    monkeypatch.setattr(orchestrator, "start_run", _noop_start)
    async with SessionLocal() as s:
        ch = await create_chain(s)
        await s.commit()
        env.agents.append(ch["agent"]); env.interfaces.append(ch["iface"])
        env.suites.append(ch["suite"]); env.cases.extend(ch["cases"])
        resp = await runs_mod.create_run(
            runs_mod.RunCreate(agent_id=ch["agent"].id, suite_id=ch["suite"].id, version="1.2.3"),
            _user, s)
        run_id = resp["data"]["id"]
        created = await s.get(EvalRun, run_id)
        assert created is not None
        env.runs.append(created)  # 供 env 清理
    async with SessionLocal() as s2:
        r = await s2.get(EvalRun, run_id)
        assert r is not None and r.status == "pending"


@pytest.mark.asyncio(loop_scope="session")
async def test_create_run_concurrent_one_wins(env, monkeypatch):
    """并发触发同 agent（各自独立 session）：跨事务 FOR UPDATE 串行 → 恰一个成功一个 409，不双插。"""
    monkeypatch.setattr(orchestrator, "start_run", _noop_start)
    async with SessionLocal() as s:
        ch = await create_chain(s)
        await s.commit()
        env.agents.append(ch["agent"]); env.interfaces.append(ch["iface"])
        env.suites.append(ch["suite"]); env.cases.extend(ch["cases"])
        agent_id = ch["agent"].id
        suite_id = ch["suite"].id

    async def _create():
        async with SessionLocal() as s2:
            try:
                r = await runs_mod.create_run(
                    runs_mod.RunCreate(agent_id=agent_id, suite_id=suite_id, version="1.2.3"),
                    _user, s2)
                return ("ok", r["data"]["id"])
            except ApiError as e:
                return ("err", e.status_code)

    results = await asyncio.gather(_create(), _create())
    outcomes = sorted(r[0] for r in results)
    assert outcomes == ["err", "ok"], f"并发触发应恰一个成功一个互斥拒绝，实际 {results}"
    async with SessionLocal() as s3:
        rows = (await s3.execute(select(EvalRun).where(EvalRun.agent_id == agent_id))).scalars().all()
        env.runs.extend(rows)  # 含并发创建的那条，供 env 清理
        assert len(rows) == 1, f"跨事务 FOR UPDATE 串行应保证不双插，实际 {len(rows)} 条"


# ---------------- rerun_run 触发互斥（P2-D2 同修 with_for_update，需覆盖） ----------------
@pytest.mark.asyncio(loop_scope="session")
async def test_rerun_active_400(env, monkeypatch):
    """源 run 自身 pending（active）即自锁：rerun 同 run 应 400（#4 护栏，单测已定 400）。"""
    monkeypatch.setattr(orchestrator, "start_run", _noop_start)
    async with SessionLocal() as s:
        ch = await create_chain(s)
        src = make_run(ch["agent"].id, ch["suite"].id)  # status=pending 属 _ACTIVE_STATUS
        s.add(src)
        await s.commit()
        env.agents.append(ch["agent"]); env.interfaces.append(ch["iface"])
        env.suites.append(ch["suite"]); env.cases.extend(ch["cases"]); env.runs.append(src)
        with pytest.raises(ApiError) as ei:
            await runs_mod.rerun_run(src.id, _audit_req("10.9.9.1"), _staff, s)
        assert ei.value.status_code == 400
        assert ei.value.code == E_VALIDATION


@pytest.mark.asyncio(loop_scope="session")
async def test_rerun_no_active_ok(env, monkeypatch):
    """源 run 终态（completed，非 active）→ rerun 创建新 run：pending、同 agent/suite，审计落库。"""
    monkeypatch.setattr(orchestrator, "start_run", _noop_start)
    ip = "10.9.9.2"  # 测试专属 IP，便于清审计行
    async with SessionLocal() as s:
        ch = await create_chain(s)
        src = make_run(ch["agent"].id, ch["suite"].id)
        src.status = "completed"  # 终态，允许 rerun
        s.add(src)
        await s.commit()
        env.agents.append(ch["agent"]); env.interfaces.append(ch["iface"])
        env.suites.append(ch["suite"]); env.cases.extend(ch["cases"]); env.runs.append(src)
        resp = await runs_mod.rerun_run(src.id, _audit_req(ip), _staff, s)
        new_id = resp["data"]["id"]
        new = await s.get(EvalRun, new_id)
        assert new is not None and new.status == "pending"
        assert new.agent_id == src.agent_id and new.suite_id == src.suite_id
        env.runs.append(new)
    async with SessionLocal() as s2:
        await s2.execute(delete(AuditLog).where(AuditLog.ip == ip))  # 清本测试审计行
        await s2.commit()


@pytest.mark.asyncio(loop_scope="session")
async def test_rerun_concurrent_one_wins(env, monkeypatch):
    """并发 rerun 同源 run（各自独立 session）：跨事务 FOR UPDATE 串行 → 恰一成功一 409，不双插。"""
    monkeypatch.setattr(orchestrator, "start_run", _noop_start)
    ip = "10.9.9.3"
    async with SessionLocal() as s:
        ch = await create_chain(s)
        src = make_run(ch["agent"].id, ch["suite"].id)
        src.status = "completed"
        s.add(src)
        await s.commit()
        env.agents.append(ch["agent"]); env.interfaces.append(ch["iface"])
        env.suites.append(ch["suite"]); env.cases.extend(ch["cases"]); env.runs.append(src)
        src_id = src.id

    async def _rerun():
        async with SessionLocal() as s2:
            try:
                r = await runs_mod.rerun_run(src_id, _audit_req(ip), _staff, s2)
                return ("ok", r["data"]["id"])
            except ApiError as e:
                return ("err", e.status_code)

    results = await asyncio.gather(_rerun(), _rerun())
    outcomes = sorted(r[0] for r in results)
    assert outcomes == ["err", "ok"], f"并发 rerun 应恰一成功一互斥拒绝，实际 {results}"
    async with SessionLocal() as s3:
        rows = (await s3.execute(select(EvalRun).where(EvalRun.agent_id == src.agent_id))).scalars().all()
        env.runs.extend(r for r in rows if r.id != src_id)  # 新创建的 run 供 env 清理
        assert len(rows) == 2, f"源 run + 恰 1 新 run，实际 {len(rows)} 条"  # 锁定读防并发双插
        await s3.execute(delete(AuditLog).where(AuditLog.ip == ip))  # 清赢家落库的审计行
        await s3.commit()
