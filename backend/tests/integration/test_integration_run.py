"""7.2 orchestrator 完整 run 数据一致性（容器真库，mock 探测/执行/judge）。

覆盖 §15.1 状态机 + §8 落库 + 3.3 评分数据流 + §15.4 熔断：
- 全 pass → SCORING → score_run 一次到终态（judge 未配置，零 token）→ completed，
  验证 eval_result 评分字段（score_total/score_per_dimension/pass_fail/usage/timing）与
  run 聚合（agent_score/pass_case/env_snapshot）真实落库
- 混 error → partial_failed（不评分），error_type/error_detail 落库
- 幂等重跑：_save_result existing 跳过，eval_result 不重复
- 熔断跨 run 持久：breaker open 后下次 run 用例直接 circuit_open，不实际调用执行层

mock 策略（见 7.2 方案）：
- orchestrator._probe_before_run → True（跳过 B.5 网络探测）
- orchestrator._call_once → 按 case.name 返回固定 CaseOutcome（绕过真网络）
- scorer._judge_configured → False（judge 未配置路径，断言评分一次到终态）
"""
import asyncio

import pytest

pytest.importorskip("aiomysql")

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import CaseVersion, EvalResult, EvalRun, JudgeTask
from app.runner.executor import ERROR_HTTP, CaseOutcome
from app.runner.orchestrator import orchestrator
from app.runner.scorer import score_run
from helpers import create_chain, make_run

_PASS_OUTCOME = CaseOutcome(
    unified={"answer": "你好", "reasoning": "",
             "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
             "meta": {}},
    timing={"start_ts": 0.1, "first_token_ts": 0.2, "end_ts": 0.5},
    status_code=200,
)


async def fake_probe(*args, **kwargs):
    return True


async def fake_judge_configured(db):
    return False


def make_call_once(results: dict):
    """按 case.name 返回固定 CaseOutcome 的 _call_once 替身。"""

    async def _fake(adapter, client, case, timeout_s, max_retries, **kwargs):
        return results[case.name]

    return _fake


async def _load_results(run_id):
    async with SessionLocal() as s:
        return (await s.execute(select(EvalResult).where(EvalResult.run_id == run_id))).scalars().all()


async def _load_run(run_id):
    async with SessionLocal() as s:
        return await s.get(EvalRun, run_id)


def _patch_orchestrator(monkeypatch, call_once):
    monkeypatch.setattr(orchestrator, "_probe_before_run", fake_probe)
    monkeypatch.setattr(orchestrator, "_call_once", call_once)
    monkeypatch.setattr("app.runner.scorer._judge_configured", fake_judge_configured)


async def _seed(env, db, *, run_cfg=None, **kw):
    """create_chain + run 一次性造数并记入 env（自动清理）。run_cfg 透传给 make_run。"""
    ch = await create_chain(db, **kw)
    run = make_run(ch["agent"].id, ch["suite"].id, run_cfg)
    db.add(run)
    await db.flush()
    env.agents.append(ch["agent"]); env.interfaces.append(ch["iface"])
    env.suites.append(ch["suite"]); env.cases.extend(ch["cases"]); env.runs.append(run)
    await db.commit()
    return ch, run


@pytest.mark.asyncio(loop_scope="session")
async def test_full_run_all_pass_score_flow(db, env, monkeypatch):
    """全 pass run：SCORING → 评分 → completed，评分数据流完整落库。"""
    ch, run = await _seed(env, db)
    (case,) = ch["cases"]

    _patch_orchestrator(monkeypatch, make_call_once({"it72-case": _PASS_OUTCOME}))
    await orchestrator.start_run(run.id)

    r = await _load_run(run.id)
    results = await _load_results(run.id)
    # run 状态机与聚合
    assert r.status == "completed"
    assert r.pass_case == 1 and r.error_case == 0 and r.fail_case == 0 and r.na_case == 0
    assert r.agent_score == 100.0
    assert r.total_case == 1
    assert r.started_at is not None and r.finished_at is not None
    assert r.env_snapshot["contract_version"] == "1.0"  # env_snapshot 快照落库
    assert r.total_tokens == 15

    # 单条 eval_result：评分字段真实落库（completeness 断言 field_nonempty answer → 100 分）
    assert len(results) == 1
    er = results[0]
    assert er.pass_fail == "pass"
    assert er.score_total == 100.0
    assert er.error_type is None
    assert er.total_tokens == 15
    assert er.usage == [{"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}]
    # Numeric 列读回是 Decimal（0.200 ≠ 0.2 精确浮点），统一 float 比较
    assert float(er.ttft_p50) == 0.2 and float(er.e2e_p50) == 0.5
    assert er.assertion_results and er.assertion_results[0]["pass"] is True
    assert er.score_per_dimension and er.score_per_dimension[0]["code"] == "completeness"
    assert er.score_per_dimension[0]["value"] == 100.0

    # CaseVersion 自动建 v1 快照（含输入/断言/metrics，评分数据源）
    async with SessionLocal() as s:
        cv = await s.get(CaseVersion, er.case_version_id)
    assert cv is not None and cv.version_no == 1
    assert cv.snapshot["input"] == case.input
    assert cv.snapshot["metrics"] == {"completeness": {"enabled": True}}


@pytest.mark.asyncio(loop_scope="session")
async def test_run_with_error_partial_failed(db, env, monkeypatch):
    """混 error run：partial_failed，error_type/error_detail 落库，不进入评分。"""
    ch, run = await _seed(env, db, case_names=["it72-pass", "it72-error"])
    _patch_orchestrator(monkeypatch, make_call_once({
        "it72-pass": _PASS_OUTCOME,
        "it72-error": CaseOutcome(error_type="mock_error", error_detail="模拟技术失败"),
    }))
    await orchestrator.start_run(run.id)

    r = await _load_run(run.id)
    results = await _load_results(run.id)
    assert r.status == "partial_failed"
    assert r.pass_case == 1 and r.error_case == 1
    assert len(results) == 2

    ok_er = next(er for er in results if er.error_type is None)
    err_er = next(er for er in results if er.error_type == "mock_error")
    # pass 侧：executor 落库暂标 pass，_finish 判定 passed>0 → SCORING → score_run 评分重算，
    # 有 error 终态仍 partial_failed，但 pass 用例已落分（100，completeness 断言通过）
    assert ok_er.pass_fail == "pass" and float(ok_er.score_total) == 100.0
    assert ok_er.answer == "你好" and ok_er.reasoning == ""
    # error 侧：技术失败归类 + 明细落库，不评分
    assert err_er.pass_fail == "error"
    assert err_er.error_type == "mock_error"
    assert err_er.error_detail == "模拟技术失败"
    assert err_er.answer is None and err_er.score_total is None


@pytest.mark.asyncio(loop_scope="session")
async def test_run_idempotent_rerun(db, env, monkeypatch):
    """幂等：同一 run 二次 start_run，eval_result 不重复（_save_result existing 跳过）。"""
    _, run = await _seed(env, db)
    _patch_orchestrator(monkeypatch, make_call_once({"it72-case": _PASS_OUTCOME}))
    await orchestrator.start_run(run.id)
    assert len(await _load_results(run.id)) == 1

    # 二次重跑：状态重初始化 + 执行循环重走，但落库幂等
    await orchestrator.start_run(run.id)
    assert len(await _load_results(run.id)) == 1  # 不重复

    r = await _load_run(run.id)
    assert r.status == "completed" and r.agent_score == 100.0


@pytest.mark.asyncio(loop_scope="session")
async def test_run_breaker_open_circuit_open(db, env, monkeypatch):
    """熔断跨 run 持久：breaker open 后二次 run 用例直接 circuit_open，不实际调用执行层。"""
    # run1/run2 同配置：threshold=1 使 1 次可重试失败即 open（orchestrator.start_run 用 run_config 覆盖 breaker 阈值）
    run_cfg = {"breaker_failure_threshold": 1, "breaker_open_duration": 30}
    _, run1 = await _seed(env, db, case_names=["it72-fail"], run_cfg=run_cfg)

    calls: list[str] = []

    async def fake_call(adapter, client, case, timeout_s, max_retries, **kwargs):
        calls.append(case.name)
        return CaseOutcome(error_type=ERROR_HTTP, error_detail="模拟 500")  # RETRYABLE → 熔断累计

    _patch_orchestrator(monkeypatch, fake_call)
    await orchestrator.start_run(run1.id)
    assert len(calls) == 1  # run1 实际执行 1 次 → breaker 累计 1 ≥ 阈值 → open

    # 二次 run（同 agent）→ 熔断期直接拦截
    agent_id = run1.agent_id
    run2 = make_run(agent_id, run1.suite_id, run_cfg)
    db.add(run2); await db.flush(); env.runs.append(run2); await db.commit()
    await orchestrator.start_run(run2.id)

    assert len(calls) == 1  # run2 未调用执行层（熔断拦截）
    r2 = await _load_run(run2.id)
    assert r2.status == "partial_failed" and r2.error_case == 1
    (res,) = await _load_results(run2.id)
    assert res.error_type == "circuit_open"
    assert res.pass_fail == "error"


@pytest.mark.asyncio(loop_scope="session")
async def test_case_version_rotation_on_change(db, env):
    """case 内容变更 → 建新 case_version（version_no+1）；内容不变 → 复用最新。

    7.3 修复回归：原实现无条件复用最新版本，case metrics 开启语义判分后 scorer
    仍读旧快照（cv.snapshot 的 metrics），judge_task 建不出来、维度分永远 None。
    修复后内容哈希不同即建新版本，语义判分随快照同步生效。
    """
    ch, _ = await _seed(env, db)
    (case,) = ch["cases"]
    v1 = await orchestrator._ensure_case_version(case)
    # 内容未变 → 复用 v1（幂等，不重复建快照）
    assert await orchestrator._ensure_case_version(case) == v1

    # 变更 metrics（开启语义判分维度）→ 内容哈希不同 → 建 v2，评分数据源随快照更新
    case.metrics = {"completeness": {"enabled": True}, "factuality": {"enabled": True}}
    v2 = await orchestrator._ensure_case_version(case)
    assert v2 != v1

    async with SessionLocal() as s:
        cvs = (await s.execute(select(CaseVersion).where(CaseVersion.case_id == case.id)
                               .order_by(CaseVersion.version_no))).scalars().all()
    assert [cv.version_no for cv in cvs] == [1, 2]
    assert cvs[1].snapshot["metrics"] == case.metrics
    # 不变复用最新版本，无第三条
    assert await orchestrator._ensure_case_version(case) == v2


# ---------------- P2-D4 score_run judge 已配置分支（未配置路径见上方 full_run） ----------------

async def fake_judge_configured_true(db):
    """judge 已配置（否则上例 monkeypatch False 已覆盖未配置一次到终态）。"""
    return True


async def _seed_scoring(env, db, *, metrics=None):
    """run=scoring + 单 case 结果 + case_version 快照（metrics 含 enabled 语义维度）。"""
    metrics = metrics or {"factuality": {"enabled": True}}
    ch = await create_chain(db, case_kw={"metrics": metrics})
    run = make_run(ch["agent"].id, ch["suite"].id)
    run.status = "scoring"
    db.add(run)
    await db.flush()
    cv = CaseVersion(case_id=ch["cases"][0].id, version_no=1, content_hash="0" * 64,
                     snapshot={"input": ch["cases"][0].input,
                               "expected": {"golden_answer": "A 售价 100 元"}, "metrics": metrics})
    db.add(cv)
    await db.flush()
    res = EvalResult(run_id=run.id, case_id=ch["cases"][0].id, case_version_id=cv.id,
                     pass_fail="pass", answer="A 售价 100 元")
    db.add(res)
    await db.commit()
    env.agents.append(ch["agent"]); env.interfaces.append(ch["iface"])
    env.suites.append(ch["suite"]); env.cases.extend(ch["cases"]); env.runs.append(run)
    return ch, run


@pytest.mark.asyncio(loop_scope="session")
async def test_score_run_judge_configured_keeps_scoring(db, env, monkeypatch):
    """judge 已配置 + enabled 语义维度 → 建 JudgeTask(pending)，run 保持 scoring 等 worker。"""
    monkeypatch.setattr("app.runner.scorer._judge_configured", fake_judge_configured_true)
    _, run = await _seed_scoring(env, db)
    await score_run(run.id)
    r = await _load_run(run.id)
    (er,) = await _load_results(run.id)
    assert r.status == "scoring"        # 等 worker，不落终态
    assert er.score_total is None       # 未评分
    async with SessionLocal() as s:
        tasks = (await s.execute(select(JudgeTask).where(JudgeTask.run_id == run.id))).scalars().all()
    assert [t.dimension_code for t in tasks] == ["factuality"]
    assert tasks[0].status == "pending"


@pytest.mark.asyncio(loop_scope="session")
async def test_score_run_judge_done_backfills(db, env, monkeypatch):
    """judge 已配置 + 任务全 done → 回填 judge_results 完整评分 → completed。"""
    monkeypatch.setattr("app.runner.scorer._judge_configured", fake_judge_configured_true)
    ch, run = await _seed_scoring(env, db)
    async with SessionLocal() as s:
        s.add(JudgeTask(run_id=run.id, case_id=ch["cases"][0].id, dimension_code="factuality",
                        status="done",
                        result={"dimension": "factuality", "level": 4, "score": 80.0,
                                "reason": "事实准确", "rubric_version": "1.2"}))
        await s.commit()
    await score_run(run.id)
    r = await _load_run(run.id)
    (er,) = await _load_results(run.id)
    assert r.status == "completed"
    assert er.judge_results and er.judge_results[0]["score"] == 80.0
    assert er.score_total is not None


# ---------------- P2-D7 score_run 并发插 JudgeTask 幂等（IntegrityError 兜底） ----------------
@pytest.mark.asyncio(loop_scope="session")
async def test_score_run_preseeded_judge_tasks_idempotent(db, env, monkeypatch):
    """并发另一 score_run 已预插 pending 任务 → 本次幂等不重复插、不报 IntegrityError。"""
    monkeypatch.setattr("app.runner.scorer._judge_configured", fake_judge_configured_true)
    ch, run = await _seed_scoring(env, db)
    async with SessionLocal() as s:
        s.add(JudgeTask(run_id=run.id, case_id=ch["cases"][0].id, dimension_code="factuality",
                        status="pending"))
        await s.commit()
    await score_run(run.id)
    async with SessionLocal() as s:
        tasks = (await s.execute(select(JudgeTask).where(JudgeTask.run_id == run.id))).scalars().all()
    assert [t.dimension_code for t in tasks] == ["factuality"]  # 数量不增
    assert tasks[0].status == "pending"  # 仍 pending 等 worker，未翻终态


@pytest.mark.asyncio(loop_scope="session")
async def test_score_run_parallel_no_duplicate(db, env, monkeypatch):
    """两个 score_run 并发（D6 行锁串行化 + D7 兜底）→ 无异常、JudgeTask 精确只建一份。"""
    monkeypatch.setattr("app.runner.scorer._judge_configured", fake_judge_configured_true)
    _, run = await _seed_scoring(env, db)
    await asyncio.gather(score_run(run.id), score_run(run.id))
    async with SessionLocal() as s:
        tasks = (await s.execute(select(JudgeTask).where(JudgeTask.run_id == run.id))).scalars().all()
    assert [t.dimension_code for t in tasks] == ["factuality"]  # 并发不重复
    assert tasks[0].status == "pending"
