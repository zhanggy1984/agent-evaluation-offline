"""7.2 scanner 回收验证数据一致性（容器真库）。

覆盖 §15.1 生命周期兜底：
- 租约/硬超时回收 running → timeout（并置 orchestrator 取消标志）
- scoring 超时 → scoring_failed（保留采集数据，不误标执行失败）
- 终态 run 不回收（completed 保持）
- 崩溃现场 salvage 出分 + 缺 case 对账回填 + 不新建 judge 任务

仅回收 running：scoring 是终态（心跳已停），lease 过期会误覆盖成 timeout——
scoring 崩溃兜底由评分恢复机制负责，scanner 不碰终态。
"""
import pytest

pytest.importorskip("aiomysql")

from datetime import datetime, timedelta

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import CaseVersion, EvalResult, EvalRun, JudgeTask
from app.runner.orchestrator import orchestrator
from app.runner.scanner import _reap_one_pass
from helpers import create_chain, make_run


def _utcnow():
    return datetime.utcnow()


def _patch_cancel(monkeypatch):
    cancelled: list[int] = []
    monkeypatch.setattr(orchestrator, "cancel_run", lambda rid: cancelled.append(rid))
    return cancelled


async def _get_run(run_id):
    async with SessionLocal() as s:
        return await s.get(EvalRun, run_id)


async def _seed_chain(env, db, **kw):
    """create_chain 造数并记入 env（本文件多数测试只关心 agent/suite，case 可空）。"""
    ch = await create_chain(db, **kw)
    env.agents.append(ch["agent"])
    env.interfaces.append(ch["iface"])
    env.suites.append(ch["suite"])
    env.cases.extend(ch["cases"])
    await db.commit()
    return ch


@pytest.mark.asyncio(loop_scope="session")
async def test_reap_lease_timeout(db, env, monkeypatch):
    """running 且租约过期（超出 10s grace）→ timeout + 取消标志置位。"""
    ch = await _seed_chain(env, db, case_names=[])
    now = _utcnow()
    run = make_run(ch["agent"].id, ch["suite"].id)
    run.status = "running"
    run.lease_until = now - timedelta(seconds=60)  # 租约过期
    run.hard_deadline = now + timedelta(seconds=3600)
    db.add(run); await db.flush(); env.runs.append(run); await db.commit()

    cancelled = _patch_cancel(monkeypatch)
    reaped = await _reap_one_pass()
    assert reaped == 1
    r = await _get_run(run.id)
    assert r.status == "timeout"
    assert r.finished_at is not None
    assert run.id in cancelled  # 回收时同步置取消，防止已标 timeout 的 run 仍在执行


@pytest.mark.asyncio(loop_scope="session")
async def test_reap_hard_deadline(db, env, monkeypatch):
    """running 且硬超时过期（不随心跳续期）→ timeout。"""
    ch = await _seed_chain(env, db, case_names=[])
    now = _utcnow()
    run = make_run(ch["agent"].id, ch["suite"].id)
    run.status = "running"
    run.lease_until = now + timedelta(seconds=60)  # 租约未过
    run.hard_deadline = now - timedelta(seconds=1)  # 硬超时已过
    db.add(run); await db.flush(); env.runs.append(run); await db.commit()

    cancelled = _patch_cancel(monkeypatch)
    reaped = await _reap_one_pass()
    assert reaped == 1
    r = await _get_run(run.id)
    assert r.status == "timeout"
    assert run.id in cancelled


@pytest.mark.asyncio(loop_scope="session")
async def test_scoring_timeout_to_scoring_failed(db, env, monkeypatch):
    """scoring 超时（评分应在限内完成）→ scoring_failed，保留采集数据。"""
    ch = await _seed_chain(env, db, case_names=[])
    now = _utcnow()
    run = make_run(ch["agent"].id, ch["suite"].id, {"scoring_timeout": 30})
    run.status = "scoring"
    run.finished_at = now - timedelta(seconds=100)  # 超过 scoring_timeout
    db.add(run); await db.flush(); env.runs.append(run); await db.commit()

    _patch_cancel(monkeypatch)
    reaped = await _reap_one_pass()
    assert reaped == 0  # scoring_failed 不计入租约回收返回值
    r = await _get_run(run.id)
    assert r.status == "scoring_failed"


@pytest.mark.asyncio(loop_scope="session")
async def test_completed_not_reaped(db, env, monkeypatch):
    """终态 run（completed）即使租约过期也不回收。"""
    ch = await _seed_chain(env, db, case_names=[])
    now = _utcnow()
    run = make_run(ch["agent"].id, ch["suite"].id)
    run.status = "completed"
    run.lease_until = now - timedelta(seconds=100)
    run.hard_deadline = now - timedelta(seconds=100)
    db.add(run); await db.flush(); env.runs.append(run); await db.commit()

    _patch_cancel(monkeypatch)
    reaped = await _reap_one_pass()
    assert reaped == 0
    r = await _get_run(run.id)
    assert r.status == "completed"


# ---------------- 7.5a timeout salvage + pending 回收 ----------------

async def _make_result(db, run_id, case, *, pass_fail="pass", error_type=None):
    """给 case 造一条已采集 eval_result（snapshot 用 case 当前内容，评分可算分）。"""
    cv = (await db.execute(select(CaseVersion).where(
        CaseVersion.case_id == case.id).order_by(CaseVersion.version_no.desc()).limit(1))).scalars().first()
    if cv is None:
        cv = CaseVersion(
            case_id=case.id, version_no=1, content_hash="it75",
            snapshot={"input": case.input, "input_turns": case.input_turns,
                      "file_ref": case.file_ref, "expected": case.expected,
                      "assertions": case.assertions, "metrics": case.metrics},
        )
        db.add(cv)
        await db.flush()
    er = EvalResult(run_id=run_id, case_id=case.id, case_version_id=cv.id,
                    pass_fail=pass_fail, error_type=error_type, answer="ok",
                    score_per_dimension=[], ttft_p50=0.5, ttft_p95=0.6,
                    e2e_p50=1.0, e2e_p95=1.2)
    db.add(er)
    await db.flush()
    return er


@pytest.mark.asyncio(loop_scope="session")
async def test_reap_pending_frees_mutex(db, env, monkeypatch):
    """pending 且租约过期（orchestrator 未接管/任务丢失）→ timeout，释放同 agent 互斥槽。"""
    ch = await _seed_chain(env, db, case_names=[])
    now = _utcnow()
    run = make_run(ch["agent"].id, ch["suite"].id)
    run.status = "pending"
    run.lease_until = now - timedelta(seconds=60)  # 租约过期
    db.add(run); await db.flush(); env.runs.append(run); await db.commit()

    cancelled = _patch_cancel(monkeypatch)
    reaped = await _reap_one_pass()
    assert reaped == 1
    r = await _get_run(run.id)
    assert r.status == "timeout"
    assert run.id in cancelled
    # 互斥槽释放：timeout 不在 active 状态，同 agent 可再触发
    async with SessionLocal() as s:
        active = (await s.execute(select(EvalRun.id).where(
            EvalRun.agent_id == ch["agent"].id,
            EvalRun.status.in_(("pending", "running", "scoring"))))).scalars().all()
    assert run.id not in active


@pytest.mark.asyncio(loop_scope="session")
async def test_timeout_salvage_scores(db, env, monkeypatch):
    """崩溃现场（running+过期租约）：reap → timeout，且 salvage 出分——
    agent_score 非 NULL、缺 case error 回填、不新建 judge 任务。"""
    ch = await _seed_chain(env, db, case_names=["it75-a", "it75-b"])
    now = _utcnow()
    run = make_run(ch["agent"].id, ch["suite"].id)
    run.status = "running"
    run.lease_until = now - timedelta(seconds=60)
    run.total_case = 2
    db.add(run); await db.flush(); env.runs.append(run)
    await _make_result(db, run.id, ch["cases"][0])  # case A 已采集（pass）
    await db.commit()

    _patch_cancel(monkeypatch)
    await _reap_one_pass()
    r = await _get_run(run.id)
    assert r.status == "timeout"        # 终态保持，不误翻 completed
    assert r.agent_score is not None    # salvage 出分
    assert r.pass_case == 1
    async with SessionLocal() as s:
        ers = (await s.execute(select(EvalResult).where(EvalResult.run_id == run.id))).scalars().all()
        missing = [e for e in ers if e.case_id == ch["cases"][1].id]
        assert len(missing) == 1 and missing[0].error_type == "timeout"  # 缺 case 对账回填
        tasks = (await s.execute(select(JudgeTask).where(JudgeTask.run_id == run.id))).scalars().all()
        assert len(tasks) == 0  # 不新建 judge 任务


@pytest.mark.asyncio(loop_scope="session")
async def test_scoring_failed_salvage_scores(db, env, monkeypatch):
    """scoring 超时 → scoring_failed，且 salvage 出分（不丢已采集结果）。"""
    ch = await _seed_chain(env, db, case_names=["it75-c"])
    now = _utcnow()
    run = make_run(ch["agent"].id, ch["suite"].id, {"scoring_timeout": 30})
    run.status = "scoring"
    run.finished_at = now - timedelta(seconds=100)  # 超过 scoring_timeout
    run.total_case = 1
    db.add(run); await db.flush(); env.runs.append(run)
    await _make_result(db, run.id, ch["cases"][0])
    await db.commit()

    _patch_cancel(monkeypatch)
    await _reap_one_pass()
    r = await _get_run(run.id)
    assert r.status == "scoring_failed"
    assert r.agent_score is not None
    assert r.pass_case == 1


# ---------------- 7.5d scoring 慢 judge 不误杀 ----------------

@pytest.mark.asyncio(loop_scope="session")
async def test_scoring_slow_judge_not_reaped(db, env, monkeypatch):
    """scoring 超时但有活跃 judge 任务（慢 judge）→ 不误杀，仍保持 scoring。"""
    ch = await _seed_chain(env, db, case_names=["it75-judge"])
    now = _utcnow()
    run = make_run(ch["agent"].id, ch["suite"].id, {"scoring_timeout": 30})
    run.status = "scoring"
    run.finished_at = now - timedelta(seconds=100)  # 超过 scoring_timeout
    db.add(run); await db.flush(); env.runs.append(run)
    await _make_result(db, run.id, ch["cases"][0])  # judge_task.run_id FK → eval_result 须先存在
    db.add(JudgeTask(run_id=run.id, case_id=ch["cases"][0].id, dimension_code="completeness",
                     status="pending"))
    await db.commit()

    _patch_cancel(monkeypatch)
    await _reap_one_pass()
    r = await _get_run(run.id)
    assert r.status == "scoring"  # 慢 judge 不误杀


@pytest.mark.asyncio(loop_scope="session")
async def test_scoring_stuck_reaped_when_no_active_task(db, env, monkeypatch):
    """scoring 超时且无活跃 judge 任务（真卡死）→ scoring_failed（7.5d 不误杀的反面）。"""
    ch = await _seed_chain(env, db, case_names=[])
    now = _utcnow()
    run = make_run(ch["agent"].id, ch["suite"].id, {"scoring_timeout": 30})
    run.status = "scoring"
    run.finished_at = now - timedelta(seconds=100)
    db.add(run); await db.flush(); env.runs.append(run); await db.commit()

    _patch_cancel(monkeypatch)
    await _reap_one_pass()
    r = await _get_run(run.id)
    assert r.status == "scoring_failed"


