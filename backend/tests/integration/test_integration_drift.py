"""7.2 judge 漂移检测数据一致性（容器真库，mock judge 重判，零 token）。

覆盖 6.4 漂移检测编排（run_drift_check 纯 DB 编排）：
- 一致（重判分 ≈ 金标准）：JudgeDriftHistory 落库 + 不写 issue + 通知恢复
- 漂移（一致率低于阈值）：issue 告警写入 + 幂等去重（同维度未关闭告警不重复建）
- 无采集 answer：跳过重判，无历史产出 → ApiError（区分根因）

mock 策略：patch JudgeClient.judge 返回固定分（一致 90 / 漂移 20），
patch is_configured=True（不依赖 judge_llm 配置），patch _notify_drift（不真发通知）。
"""
import pytest

pytest.importorskip("aiomysql")

import app.judge.client as judge_client_mod
import app.judge.drift_check as drift_mod
from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.errors import ApiError
from app.judge.client import JudgeClient, JudgeVerdict
from app.judge.drift_check import DRIFT_ISSUE_TITLE, run_drift_check
from app.models import CaseVersion, EvalResult, EvalRun, Issue, JudgeDriftHistory
from helpers import create_chain, make_run

_GOLD_SCORE = 90.0


def _patch_judge(monkeypatch, verdict_score: float):
    """mock judge：返回固定分；is_configured=True；通知 noop。"""
    async def fake_judge(self, **kwargs):
        dim = kwargs["dimension"]
        return JudgeVerdict(dimension=dim, level=0, score=verdict_score,
                            reason="mock 判定", rubric_version="v1")

    async def fake_notify(*args, **kwargs):
        return None

    monkeypatch.setattr(judge_client_mod, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(JudgeClient, "judge", fake_judge)
    monkeypatch.setattr(drift_mod, "_notify_drift", fake_notify)


async def _make_gold_env(db, env, *, with_answer=True, gold_scores=None):
    """gold case + 最近采集 answer 的完整环境（agent/suite/interface/case/cv/run/result）。"""
    scores = gold_scores or {"factuality": {"score": _GOLD_SCORE}}
    ch = await create_chain(
        db, case_names=["it72-gold"],
        case_kw={"metrics": {"factuality": {"enabled": True}},
                 "expected": {"judge_gold_scores": scores},
                 "is_gold": True})
    agent, case = ch["agent"], ch["cases"][0]
    run = make_run(agent.id, ch["suite"].id)
    run.status = "completed"
    db.add(run)
    cv = CaseVersion(case_id=case.id, version_no=1, content_hash="it72",
                     snapshot={"input": case.input, "expected": case.expected,
                               "metrics": case.metrics})
    db.add(cv)
    await db.flush()
    er = EvalResult(run_id=run.id, case_id=case.id, case_version_id=cv.id,
                    pass_fail="pass", answer="真实采集答案" if with_answer else None)
    db.add(er)
    env.agents.append(agent); env.interfaces.append(ch["iface"]); env.suites.append(ch["suite"])
    env.cases.append(case); env.runs.append(run)
    await db.commit()
    return agent, case


@pytest.mark.asyncio(loop_scope="session")
async def test_drift_no_drift_writes_history(db, env, monkeypatch):
    """一致：重判分与金标准一致 → 写历史（不漂移），不建 issue。"""
    agent, _ = await _make_gold_env(db, env)
    _patch_judge(monkeypatch, verdict_score=_GOLD_SCORE)

    result = await run_drift_check(dimension_code="factuality", agent_id=agent.id)

    assert len(result["history"]) == 1
    h = result["history"][0]
    assert h["dimension_code"] == "factuality"
    assert h["consistency_rate"] == 1.0  # |90-90|<=10 → 一致
    assert h["drift_flag"] is False
    assert h["judged_case_cnt"] == 1
    assert result["details"][0]["agree"] is True
    assert result["threshold"] >= 0.8

    # 历史落库（最新一条）
    async with SessionLocal() as s:
        row = (await s.execute(select(JudgeDriftHistory).where(
            JudgeDriftHistory.dimension_code == "factuality",
        ).order_by(JudgeDriftHistory.id.desc()).limit(1))).scalars().first()
        assert row is not None and row.drift_flag is False
        assert row.judged_case_cnt == 1
        env.drift_history_ids.append(row.id)
        # 不漂移 → 无告警 issue
        issues = (await s.execute(select(Issue).where(
            Issue.agent_id == agent.id,
            Issue.title == DRIFT_ISSUE_TITLE.format(dim="factuality")))).scalars().all()
        assert len(issues) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_drift_above_threshold_writes_issue(db, env, monkeypatch):
    """漂移：一致率 0 < 0.8 → issue 告警写入 + 幂等去重。"""
    agent, _ = await _make_gold_env(db, env)
    _patch_judge(monkeypatch, verdict_score=20.0)  # |20-90|=70 > 10 → 不一致

    result = await run_drift_check(dimension_code="factuality", agent_id=agent.id)
    assert result["history"][0]["drift_flag"] is True
    assert result["history"][0]["consistency_rate"] == 0.0

    async with SessionLocal() as s:
        rows = (await s.execute(select(JudgeDriftHistory).where(
            JudgeDriftHistory.dimension_code == "factuality",
        ).order_by(JudgeDriftHistory.id.desc()).limit(1))).scalars().first()
        assert rows is not None and rows.drift_flag is True
        env.drift_history_ids.append(rows.id)

        issues = (await s.execute(select(Issue).where(
            Issue.agent_id == agent.id,
            Issue.title == DRIFT_ISSUE_TITLE.format(dim="factuality")))).scalars().all()
        assert len(issues) == 1
        it = issues[0]
        assert it.agent_id == agent.id
        assert it.related_dimension == "factuality"
        assert it.status == "open" and it.severity == "medium"
        env.issues.append(it)

    # 幂等：同维度未关闭告警已存在 → 再检测不重复建 issue
    await run_drift_check(dimension_code="factuality", agent_id=agent.id)
    async with SessionLocal() as s:
        issues = (await s.execute(select(Issue).where(
            Issue.agent_id == agent.id,
            Issue.title == DRIFT_ISSUE_TITLE.format(dim="factuality")))).scalars().all()
        assert len(issues) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_drift_skips_non_semantic_dimension(db, env, monkeypatch):
    """gold 误标非语义维度（completeness 无 rubric）→ 收敛跳过不崩 500，只重判语义维度。

    回归 #233-④：修复前 load_rubric(completeness) 抛 ValueError 且不被捕获 → 500。
    不传 dimension_code，让 completeness 真正进入循环，验证 SEMANTIC_DIMENSIONS 收敛生效。
    """
    agent, _ = await _make_gold_env(
        db, env,
        gold_scores={"factuality": {"score": _GOLD_SCORE},
                     "completeness": {"score": 100.0}})

    called_dims: list[str] = []

    async def fake_judge(self, **kwargs):
        called_dims.append(kwargs["dimension"])
        return JudgeVerdict(dimension=kwargs["dimension"], level=0,
                            score=_GOLD_SCORE, reason="mock 判定", rubric_version="v1")

    async def fake_notify(*args, **kwargs):
        return None

    monkeypatch.setattr(judge_client_mod, "is_configured", lambda *a, **k: True)
    monkeypatch.setattr(JudgeClient, "judge", fake_judge)
    monkeypatch.setattr(drift_mod, "_notify_drift", fake_notify)

    # 不崩、completeness 未进 LLM、仅 factuality 产出历史
    result = await run_drift_check(agent_id=agent.id)
    assert called_dims == ["factuality"]
    assert len(result["history"]) == 1
    assert result["history"][0]["dimension_code"] == "factuality"
    assert result["history"][0]["consistency_rate"] == 1.0


@pytest.mark.asyncio(loop_scope="session")
async def test_drift_no_answer_raises(db, env, monkeypatch):
    """无采集 answer：该 gold 跳过重判 → 无历史产出，ApiError 区分根因。"""
    agent, _ = await _make_gold_env(db, env, with_answer=False)
    _patch_judge(monkeypatch, verdict_score=20.0)

    with pytest.raises(ApiError) as exc:
        await run_drift_check(dimension_code="factuality", agent_id=agent.id)
    assert "answer" in str(exc.value)

    # 无历史落库
    async with SessionLocal() as s:
        rows = (await s.execute(select(JudgeDriftHistory).where(
            JudgeDriftHistory.dimension_code == "factuality",
        ).order_by(JudgeDriftHistory.id.desc()).limit(1))).scalars().first()
        assert rows is None or rows.judged_case_cnt == 0 or rows.id not in env.drift_history_ids
