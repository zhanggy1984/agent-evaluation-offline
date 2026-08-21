"""5.3 看板聚合：L0 门禁墙 / L1 趋势 / L2 版本对比（viewer 可读）。

数据源：eval_run（run 聚合）+ eval_result（用例级 score_per_dimension）。
聚合纯逻辑在 app/core/dashboard_rules.py（宿主单测直接跑），本模块只做 DB 编排。
"""
import logging
from statistics import mean

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.constants import ACCURACY_DIMENSIONS
from app.core.dashboard_rules import (
    BASELINE_RUN_STATUS, TERMINAL_STATUS, build_baseline, build_coverage, build_gate_cards,
    significance,
)
from app.core.db import get_db
from app.core.errors import ApiError, E_NOT_FOUND, E_VALIDATION
from app.core.response import ok
from app.models import (
    Agent, AgentInterface, BaselineTarget, CaseScene, EvalResult, EvalRun, SceneCatalog,
    TestCase, TestSuite,
)
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

ACCURACY = tuple(ACCURACY_DIMENSIONS)


@router.get("/gate")
async def gate(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """L0 门禁墙：逐 agent 最新版本跨 suite 汇总 + 陈旧 suite 单独标注。"""
    logger.debug("gate in: user=%s", user.username)
    agents = (await db.execute(select(Agent).order_by(Agent.id))).scalars().all()
    runs = (await db.execute(select(EvalRun))).scalars().all()
    suites = (await db.execute(select(TestSuite))).scalars().all()
    case_cnt = {sid: n for sid, n in (await db.execute(
        select(TestCase.suite_id, func.count()).group_by(TestCase.suite_id))).all()}
    out = build_gate_cards(agents, runs, suites, case_cnt)
    logger.debug("gate out: count=%s", len(out))
    return ok(out)


@router.get("/trend")
async def trend(agent_id: int, user: User = Depends(get_current_user),
                db: AsyncSession = Depends(get_db)):
    """L1 趋势：该 agent 全部终态 run 时间序列（viewer 可读）。"""
    logger.debug("trend in: agent_id=%s user=%s", agent_id, user.username)
    rows = (await db.execute(select(EvalRun).where(
        EvalRun.agent_id == agent_id, EvalRun.status.in_(TERMINAL_STATUS),
    ).order_by(EvalRun.started_at))).scalars().all()
    out = [{
        "run_id": r.id, "version": r.version, "status": r.status,
        "agent_score": float(r.agent_score) if r.agent_score is not None else None,
        "pass_rate": (r.pass_case / r.total_case) if r.total_case else None,
        "ttft_p50": float(r.ttft_p50) if r.ttft_p50 is not None else None,
        "e2e_p50": float(r.e2e_p50) if r.e2e_p50 is not None else None,
        "total_case": r.total_case, "pass_case": r.pass_case, "fail_case": r.fail_case,
        "error_case": r.error_case,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
    } for r in rows]
    logger.debug("trend out: count=%s", len(out))
    return ok(out)


@router.get("/compare")
async def compare(a: int, b: int, user: User = Depends(get_current_user),
                  db: AsyncSession = Depends(get_db)):
    """L2 版本对比：两 run 各 accuracy 维度均值对比，|Δ| > 2σ 才标显著。"""
    logger.debug("compare in: a=%s b=%s user=%s", a, b, user.username)
    run_a = await db.get(EvalRun, a)
    run_b = await db.get(EvalRun, b)
    if run_a is None or run_b is None:
        raise ApiError(E_NOT_FOUND, "run 不存在", 404)
    if run_a.agent_id != run_b.agent_id:
        raise ApiError(E_VALIDATION, "对比的两 run 必须属于同一 agent", 400)

    history = await _agent_dim_series(db, run_a.agent_id)  # {dim: [run 级均值]}
    dims_a = await _run_dim_means(db, a)
    dims_b = await _run_dim_means(db, b)
    dims = []
    for dim in ACCURACY:
        ma, mb = dims_a.get(dim), dims_b.get(dim)
        series = history.get(dim, [])
        sigma, significant = significance(series, ma, mb)
        delta = round(mb - ma, 2) if ma is not None and mb is not None else None
        dims.append({
            "code": dim, "mean_a": ma, "mean_b": mb, "delta": delta,
            "sigma": round(sigma, 4) if sigma is not None else None,
            "significant": significant,
        })
    out = {
        "a": _compare_head(run_a), "b": _compare_head(run_b), "dims": dims,
    }
    logger.debug("compare out: a_version=%s b_version=%s", run_a.version, run_b.version)
    return ok(out)


def _compare_head(run: EvalRun) -> dict:
    return {
        "run_id": run.id, "version": run.version, "status": run.status,
        "agent_score": float(run.agent_score) if run.agent_score is not None else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
    }


async def _run_dim_means(db: AsyncSession, run_id: int) -> dict[str, float]:
    """run 内各 accuracy 维度分数均值（na/None 剔除）；无分数维度 → 缺省。"""
    results = (await db.execute(select(EvalResult).where(
        EvalResult.run_id == run_id))).scalars().all()
    buf: dict[str, list[float]] = {}
    for r in results:
        for entry in r.score_per_dimension or []:
            code = entry.get("code")
            if code not in ACCURACY or entry.get("na") or entry.get("value") is None:
                continue
            buf.setdefault(code, []).append(float(entry["value"]))
    return {code: round(mean(vals), 4) for code, vals in buf.items()}


async def _agent_dim_series(db: AsyncSession, agent_id: int) -> dict[str, list[float]]:
    """同 agent 全部终态 run 各维度 run 级均值序列（2σ 显著性用）。"""
    rows = (await db.execute(select(EvalRun).where(
        EvalRun.agent_id == agent_id, EvalRun.status.in_(TERMINAL_STATUS),
    ))).scalars().all()
    history: dict[str, list[float]] = {}
    for r in rows:
        results = (await db.execute(select(EvalResult).where(
            EvalResult.run_id == r.id))).scalars().all()
        buf: dict[str, list[float]] = {}
        for res in results:
            for entry in res.score_per_dimension or []:
                code = entry.get("code")
                if code not in ACCURACY or entry.get("na") or entry.get("value") is None:
                    continue
                buf.setdefault(code, []).append(float(entry["value"]))
        for code, vals in buf.items():
            history.setdefault(code, []).append(mean(vals))
    return history


def _num(v: float | None) -> float | None:
    return float(v) if v is not None else None


@router.get("/perf")
async def perf(agent_id: int, user: User = Depends(get_current_user),
               db: AsyncSession = Depends(get_db)):
    """性能面板：该 agent 全部终态 run 的 ttft/e2e 延迟序列（viewer 可读）。"""
    logger.debug("perf in: agent_id=%s user=%s", agent_id, user.username)
    rows = (await db.execute(select(EvalRun).where(
        EvalRun.agent_id == agent_id, EvalRun.status.in_(TERMINAL_STATUS),
    ).order_by(EvalRun.started_at))).scalars().all()
    out = [{
        "run_id": r.id, "version": r.version, "status": r.status,
        "ttft_p50": _num(r.ttft_p50), "ttft_p95": _num(r.ttft_p95),
        "e2e_p50": _num(r.e2e_p50), "e2e_p95": _num(r.e2e_p95),
        "started_at": r.started_at.isoformat() if r.started_at else None,
    } for r in rows]
    logger.debug("perf out: count=%s", len(out))
    return ok(out)


@router.get("/cost")
async def cost(agent_id: int, user: User = Depends(get_current_user),
               db: AsyncSession = Depends(get_db)):
    """成本面板：该 agent 全部终态 run 的 token/费用（viewer 可读）。

    model 按 run 内 eval_result.model 去重（单 agent 单模型场景基本单一）。
    """
    logger.debug("cost in: agent_id=%s user=%s", agent_id, user.username)
    runs = (await db.execute(select(EvalRun).where(
        EvalRun.agent_id == agent_id, EvalRun.status.in_(TERMINAL_STATUS),
    ).order_by(EvalRun.started_at))).scalars().all()
    models: dict[int, list[str]] = {}
    if runs:
        rows = (await db.execute(select(EvalResult.run_id, EvalResult.model).where(
            EvalResult.run_id.in_([r.id for r in runs]),
            EvalResult.model.is_not(None),
        ).distinct())).all()
        for rid, m in rows:
            models.setdefault(rid, []).append(m)
    out = [{
        "run_id": r.id, "version": r.version, "status": r.status,
        "model": "/".join(models.get(r.id, [])),
        "total_tokens": r.total_tokens, "total_cost": _num(r.total_cost),
        "started_at": r.started_at.isoformat() if r.started_at else None,
    } for r in runs]
    logger.debug("cost out: count=%s", len(out))
    return ok(out)


@router.get("/coverage")
async def coverage(agent_id: int, user: User = Depends(get_current_user),
                   db: AsyncSession = Depends(get_db)):
    """覆盖率页：接口覆盖 + 场景覆盖（viewer 可读），盲区单独列出。"""
    logger.debug("coverage in: agent_id=%s user=%s", agent_id, user.username)
    interfaces = (await db.execute(select(AgentInterface).where(
        AgentInterface.agent_id == agent_id, AgentInterface.enabled == True,
    ))).scalars().all()
    used_ids: set[int] = set()
    if interfaces:
        used_ids = {iid for (iid,) in (await db.execute(select(TestCase.interface_id).where(
            TestCase.interface_id.in_([i.id for i in interfaces])))).all()}
    scenes = (await db.execute(select(SceneCatalog).where(
        SceneCatalog.agent_id == agent_id))).scalars().all()
    tagged: set[str] = set()
    if interfaces:
        tagged = {tag for (tag,) in (await db.execute(select(CaseScene.scene_tag).where(
            CaseScene.case_id.in_(
                select(TestCase.id).where(TestCase.interface_id.in_([i.id for i in interfaces]))
            )))).all()}
    out = build_coverage(interfaces, used_ids, scenes, tagged)
    logger.debug("coverage out: if=%s/%s scene=%s/%s",
                 out["interface_covered"], out["interface_total"],
                 out["scene_covered"], out["scene_total"])
    return ok(out)


@router.get("/baseline")
async def baseline(agent_id: int, user: User = Depends(get_current_user),
                   db: AsyncSession = Depends(get_db)):
    """6.3 基线对比：最近有评分 run 逐接口 × accuracy 维度得分 vs 达标分（viewer 可读）。

    target 接口级优先 + agent 默认（0）回退（与 scorer._resolve_targets 同口径）；
    gap = score - target（盈余正 / 缺口负），met = score >= target。
    """
    logger.debug("baseline in: agent_id=%s user=%s", agent_id, user.username)
    run = (await db.execute(select(EvalRun).where(
        EvalRun.agent_id == agent_id, EvalRun.status.in_(BASELINE_RUN_STATUS),
    ).order_by(EvalRun.id.desc()).limit(1))).scalar_one_or_none()
    out = {"run": None, "interfaces": [], "is_gold_count": 0}
    if run is not None:
        results = (await db.execute(select(EvalResult).where(
            EvalResult.run_id == run.id))).scalars().all()
        case_interface: dict[int, int] = {}
        if results:
            rows = (await db.execute(select(TestCase.id, TestCase.interface_id).where(
                TestCase.id.in_({r.case_id for r in results})))).all()
            case_interface = {cid: iid for cid, iid in rows}
        target_rows = (await db.execute(select(BaselineTarget).where(
            BaselineTarget.agent_id == agent_id))).scalars().all()
        # 7.7 双签：pending_approval（第一人改完待第二人确认）是暂态不参与对比；
        # auto（seed 初始标定）与 approved（双签通过）均为生效值。
        targets = {(t.interface_id, t.dimension_code): float(t.target_score)
                   for t in target_rows if t.approval_status != "pending_approval"}
        interfaces = (await db.execute(select(AgentInterface).where(
            AgentInterface.agent_id == agent_id))).scalars().all()
        out["interfaces"] = build_baseline(results, case_interface, targets, interfaces)
        # 6.3 金标准统计：该 agent 已标 is_gold 的用例数（金标准集规模，6.4 judge 漂移用）
        iface_ids = [i.id for i in interfaces]
        out["is_gold_count"] = ((await db.execute(select(func.count()).select_from(TestCase).where(
            TestCase.interface_id.in_(iface_ids), TestCase.is_gold == True))).scalar() or 0) \
            if iface_ids else 0
        out["run"] = {
            "run_id": run.id, "version": run.version, "status": run.status,
            "agent_score": float(run.agent_score) if run.agent_score is not None else None,
            "total_case": run.total_case, "pass_case": run.pass_case, "fail_case": run.fail_case,
            "error_case": run.error_case,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        }
    logger.debug("baseline out: run=%s if=%d",
                 out["run"]["run_id"] if out["run"] else None, len(out["interfaces"]))
    return ok(out)
