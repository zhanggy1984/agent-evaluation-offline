"""7.2 scanner 回收 + issue 复现验证数据一致性（容器真库）。

覆盖 §15.1 生命周期兜底 + 6.2 issue 闭环：
- 租约/硬超时回收 running → timeout（并置 orchestrator 取消标志）
- scoring 超时 → scoring_failed（保留采集数据，不误标执行失败）
- 终态 run 不回收（completed 保持）
- scanner ⑤ _verify_issues_pass 兜底补验（mock verify 返回计数）
- issue 复现验证真库闭环：复现（open+fail→reproduced）、幂等（同 run 跳过）、
  回归重开（fixed+reproduced→open + audit_log）、修复（fixing+pass→fixed）

仅回收 running：scoring 是终态（心跳已停），lease 过期会误覆盖成 timeout——
scoring 崩溃兜底由评分恢复机制负责，scanner 不碰终态。
"""
import pytest

pytest.importorskip("aiomysql")

from datetime import datetime, timedelta

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import AuditLog, CaseVersion, EvalResult, EvalRun, Issue, JudgeTask
from app.runner.orchestrator import orchestrator
from app.runner.scanner import _reap_one_pass, _verify_issues_pass
from app.runner.scorer import score_run
from app.runner.issue_verify import verify_issues_for_run
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


@pytest.mark.asyncio(loop_scope="session")
async def test_verify_issues_pass_calls_verify(db, env, monkeypatch):
    """scanner ⑤ 兜底：对最近终态 run 补验（mock verify 计数，不依赖真实 issue）。"""
    ch = await _seed_chain(env, db, case_names=[])
    now = _utcnow()
    for _ in range(2):
        r = make_run(ch["agent"].id, ch["suite"].id)
        r.status = "completed"
        r.finished_at = now
        db.add(r)
        env.runs.append(r)
    await db.flush(); await db.commit()

    calls: list[int] = []

    async def fake_verify(run_id):
        calls.append(run_id)
        return 1

    monkeypatch.setattr("app.runner.issue_verify.verify_issues_for_run", fake_verify)
    updated = await _verify_issues_pass()
    assert updated == len(calls)
    assert len(calls) >= 2  # 含本次 2 个终态 run（库里可能还有其他终态 run 一并补验）


async def _seed_result_run(env, db, ch, *, pass_fail="fail", status="completed"):
    """在 create_chain 基础上补一个终态 run + eval_result（verify 按 run 幂等，各环节须新 run）。

    同一 case 多 run 共享同一份 case 快照（case_version 唯一约束 (case_id, version_no)，
    首个 run 建 v1，后续复用最新版本）。
    """
    agent, case = ch["agent"], ch["cases"][0]
    run = make_run(agent.id, ch["suite"].id)
    run.status = status
    db.add(run)
    cv = (await db.execute(select(CaseVersion).where(
        CaseVersion.case_id == case.id).order_by(CaseVersion.version_no.desc()).limit(1))).scalars().first()
    if cv is None:
        cv = CaseVersion(case_id=case.id, version_no=1, content_hash="it72", snapshot={})
        db.add(cv)
        await db.flush()
    er = EvalResult(run_id=run.id, case_id=case.id, case_version_id=cv.id,
                    pass_fail=pass_fail, score_per_dimension=[])
    db.add(er)
    env.runs.append(run)
    await db.commit()
    return run


@pytest.mark.asyncio(loop_scope="session")
async def test_issue_verify_reproduce_reopen_fix(db, env):
    """issue 复现验证真库闭环：复现 → 幂等 → 回归重开 → 修复（各环节独立 run 触发）。"""
    ch = await _seed_chain(env, db, case_names=["it72-case"])
    agent, case = ch["agent"], ch["cases"][0]
    issue = Issue(agent_id=agent.id, title="it72-issue", related_case_id=case.id,
                  related_dimension="completeness", severity="medium", status="open")
    db.add(issue)
    await db.flush()
    env.issues.append(issue)
    await db.commit()

    # ① 复现验证：open + 整体 fail → reproduced（记录 last_verify_*，状态不变）
    run1 = await _seed_result_run(env, db, ch, pass_fail="fail")
    updated = await verify_issues_for_run(run1.id)
    assert updated == 1
    async with SessionLocal() as s:
        it = await s.get(Issue, issue.id)
        assert it.status == "open"
        assert it.last_verify_run_id == run1.id
        assert it.last_verify_result == "reproduced"

    # ② 幂等：同 run1 再验 → 跳过（last_verify_run_id == run1.id）
    assert await verify_issues_for_run(run1.id) == 0

    # ③ 回归自动重开：新 run 复现 + fixed → open + audit_log
    run2 = await _seed_result_run(env, db, ch, pass_fail="fail")
    async with SessionLocal() as s:
        it = await s.get(Issue, issue.id)
        it.status = "fixed"
        await s.commit()
    assert await verify_issues_for_run(run2.id) == 1
    async with SessionLocal() as s:
        it = await s.get(Issue, issue.id)
        assert it.status == "open"
        assert it.last_verify_result == "reproduced"
        audit = (await s.execute(select(AuditLog).where(
            AuditLog.action == "issue.reopen",
            AuditLog.target_id == str(issue.id)))).scalars().first()
        assert audit is not None
        assert audit.detail.get("old_status") == "fixed"

    # ④ 修复验证：新 run 整体 pass + fixing → fixed（记录 last_verify_result，状态由人工流转）
    run3 = await _seed_result_run(env, db, ch, pass_fail="pass")
    async with SessionLocal() as s:
        it = await s.get(Issue, issue.id)
        it.status = "fixing"
        await s.commit()
    assert await verify_issues_for_run(run3.id) == 1
    async with SessionLocal() as s:
        it = await s.get(Issue, issue.id)
        assert it.status == "fixing"  # 修复态由人工流转，验证只记录结果
        assert it.last_verify_result == "fixed"


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


# ---------------- 7.5e pending_human 降级（scanner ③.5 + score_run ④） ----------------

@pytest.mark.asyncio(loop_scope="session")
async def test_pending_human_blocks_score(db, env, monkeypatch):
    """7.5e：pending_human 纳入 score_run 阻塞集——有 pending_human 任务时不提前打终态。"""
    ch = await _seed_chain(env, db, case_names=["it75-ph"])
    run = make_run(ch["agent"].id, ch["suite"].id)
    run.status = "scoring"
    db.add(run); await db.flush(); env.runs.append(run)
    await _make_result(db, run.id, ch["cases"][0])  # judge_task.run_id FK → eval_result 须先存在
    db.add(JudgeTask(run_id=run.id, case_id=ch["cases"][0].id, dimension_code="completeness",
                     status="pending_human"))
    await db.commit()

    async def _configured(db):
        return True  # 强制走 judge 段（阻塞集判定依赖建任务段）

    monkeypatch.setattr("app.runner.scorer._judge_configured", _configured)
    await score_run(run.id)
    r = await _get_run(run.id)
    assert r.status == "scoring"  # pending_human 阻塞，不收敛为终态


@pytest.mark.asyncio(loop_scope="session")
async def test_pending_human_timeout_degrades(db, env, monkeypatch):
    """7.5e：pending_human 超时（human_review_timeout）→ ③.5 降级 failed + ④ score_run 兜底收敛。"""
    ch = await _seed_chain(env, db, case_names=["it75-ph2"])
    now = _utcnow()
    run = make_run(ch["agent"].id, ch["suite"].id, {"human_review_timeout": 1})
    run.status = "scoring"
    run.finished_at = now  # 已进入评分阶段（未超 scoring_timeout，不触发 ③）
    db.add(run); await db.flush(); env.runs.append(run)
    await _make_result(db, run.id, ch["cases"][0])  # case A 已采集
    db.add(JudgeTask(run_id=run.id, case_id=ch["cases"][0].id, dimension_code="completeness",
                     status="pending_human", updated_at=now - timedelta(seconds=100)))
    await db.commit()

    _patch_cancel(monkeypatch)
    await _reap_one_pass()
    r = await _get_run(run.id)
    assert r.agent_score is not None  # ④ score_run 兜底收敛（降级后无活跃任务）
    async with SessionLocal() as s:
        tasks = (await s.execute(select(JudgeTask).where(JudgeTask.run_id == run.id))).scalars().all()
        assert all(t.status == "failed" for t in tasks)  # ③.5 降级
