"""阶段三评分：逐 case 维度评分 + N/A 归一化 + 门禁 + run 聚合（§15.2 / 3.3）。

流程（orchestrator._finish 对账后调用）：
    加载 run/权重/阈值/价格 → 逐 eval_result：
        组装 unified → 3.1 断言 → 3.2 各维度 compute → N/A 归一化加权 →
        门禁判定 → 落 score_total/score_per_dimension/pass_fail/total_cost
    → run 聚合（agent_score/通过率/预聚合列/judge_incomplete/状态）。

score_case 为纯函数（单测核心）；score_run 做 DB 读写编排。
评分失败不阻塞 run 生命周期：异常 → 标 partial_failed 收尾（不留在 scoring）。
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from statistics import mean

from sqlalchemy import select

from app.assertions import run_assertions
from app.core.constants import (
    ACCURACY_DIMENSIONS, DEFAULT_WEIGHTS, SEMANTIC_DIMENSIONS,
)
from app.metrics import MetricContext, get_metric

# 注意：SessionLocal / models 在 score_run 内延迟导入 —— 保持本模块顶层零 DB 依赖，
# 纯函数（score_case 等）在宿主单测可直接跑（无需 aiomysql）。

logger = logging.getLogger(__name__)

# judge_incomplete 门槛：语义维度 N/A 权重占比阈值（§15.2，>30% 标评分不完整）
JUDGE_INCOMPLETE_THRESHOLD = 0.3


@dataclass
class CaseScore:
    """单 case 评分产物（score_case 返回，score_run 落库）。"""

    score_total: float | None = None
    score_per_dimension: list[dict] = field(default_factory=list)
    pass_fail: str = "na"          # pass / fail / na（error 由 executor 标，不经过本模块）
    na_reason: str | None = None
    total_cost: float | None = None
    semantic_na_weight: float = 0.0
    total_accuracy_weight: float = 0.0
    gate_failed: bool = False


def score_case(
    *,
    case_metrics: dict | None,
    assertion_results: list[dict],
    judge_results: list[dict] | None,
    ttft_p50: float | None, ttft_p95: float | None,
    e2e_p50: float | None, e2e_p95: float | None,
    usage: list[dict] | None,
    model: str | None = None,
    model_price: dict | None = None,
    weights: dict, targets: dict,
) -> CaseScore:
    """纯函数评分：一个 case 的 accuracy 加权分 + 门禁 + 成本。

    - enabled：case_metrics 声明启用的维度（未配置视为 accuracy 四维全启用）
    - N/A 维度剔除后按剩余权重重归一化（§15.2）
    - 门禁：baseline_target 配置了 target 的 accuracy 维度，任一 score < target → fail
    - judge_incomplete：语义维度 N/A 权重（返回供 run 级聚合）
    - total_cost：无条件算（成本独立呈现，有 price 才算）
    """
    enabled = _enabled_dims(case_metrics)
    per_dim: list[dict] = []
    acc: list[tuple[str, float, float]] = []   # (dim, score, weight)
    semantic_na_weight = 0.0
    for dim in ACCURACY_DIMENSIONS:
        if dim not in enabled:
            continue
        weight = weights.get(dim, DEFAULT_WEIGHTS[dim])
        ctx = MetricContext(
            assertion_results=assertion_results,
            judge_results=judge_results or [],
            ttft_p50=ttft_p50, ttft_p95=ttft_p95,
            e2e_p50=e2e_p50, e2e_p95=e2e_p95,
            usage=usage or [], model=model, model_price=model_price,
        )
        mr = get_metric(dim).compute(ctx)
        per_dim.append({
            "code": dim, "value": mr.score, "na": mr.na,
            "na_reason": mr.na_reason, "detail": mr.detail,
        })
        if mr.na:
            if dim in SEMANTIC_DIMENSIONS:
                semantic_na_weight += weight
            continue
        acc.append((dim, mr.score, weight))

    total_w = sum(w for _, _, w in acc)
    total_accuracy_weight = sum(weights.get(d, DEFAULT_WEIGHTS[d]) for d in ACCURACY_DIMENSIONS)
    score_total = sum(s * w for _, s, w in acc) / total_w if acc else None

    # 门禁：配置了 target 的 accuracy 维度，任一不达标 → fail（N/A 维度不判）
    gate_failed = False
    if score_total is not None:
        for dim, target in targets.items():
            if dim not in ACCURACY_DIMENSIONS:
                continue
            entry = next((p for p in per_dim if p["code"] == dim and not p["na"]), None)
            if entry is not None and entry["value"] < target:
                gate_failed = True
                break

    if score_total is None:
        pass_fail = "na"
        na_reason = next((p["na_reason"] for p in per_dim if p["na"]), "metric_na")
    elif gate_failed:
        pass_fail = "fail"
        na_reason = None
    else:
        pass_fail = "pass"
        na_reason = None

    return CaseScore(
        score_total=round(score_total, 2) if score_total is not None else None,
        score_per_dimension=per_dim,
        pass_fail=pass_fail,
        na_reason=na_reason,
        total_cost=_total_cost(usage, model_price),
        semantic_na_weight=semantic_na_weight,
        total_accuracy_weight=total_accuracy_weight,
        gate_failed=gate_failed,
    )


def _enabled_dims(case_metrics: dict | None) -> set[str]:
    """case_metrics={dimension:{enabled}} 提取启用维度；未配置视为 accuracy 全启用。"""
    if not case_metrics:
        return set(ACCURACY_DIMENSIONS)
    return {dim for dim, cfg in case_metrics.items()
            if isinstance(cfg, dict) and cfg.get("enabled")}


def _enabled_semantic_dims(case_metrics: dict | None) -> list[str]:
    """启用维度中的语义维度（需 judge 判分）。未配置视为 accuracy 全启用 → 两语义维度。"""
    return [d for d in SEMANTIC_DIMENSIONS if d in _enabled_dims(case_metrics)]


def _total_cost(usage: list[dict] | None, price: dict | None) -> float | None:
    """成本独立呈现：usage × 单价（与 TokenCostMetric 同口径，缺价格返回 None）。

    单价单位 = 元/百万 token → 金额(元) = tokens × 单价 / 1e6。
    7.4 cache 口径：命中缓存的部分按 cache_hit_price（未配 → 回退 input，兼容既有数据零变化），
    未命中部分按 input_price；completion 按 output_price（对齐 DeepSeek 上下文缓存账单）。
    """
    if not usage or not price:
        return None
    input_price = price.get("input", 0)
    cache_price = price.get("cache_hit")
    if cache_price is None:
        cache_price = input_price
    total = 0.0
    for u in usage:
        if not u:
            continue
        prompt = u.get("prompt_tokens") or 0
        cache_hit = min(u.get("prompt_cache_hit_tokens") or 0, prompt)  # 防异常数据 hit>prompt
        total += (cache_hit * cache_price
                  + (prompt - cache_hit) * input_price
                  + (u.get("completion_tokens") or 0) * price.get("output", 0))
    return round(total / 1e6, 6)


# ---------------- run 级编排 ----------------
async def score_run(run_id: int) -> None:
    """对已完成执行的 run 评分：逐 case 落库 + run 聚合 + 最终状态。

    仅对 pass_fail != error 的结果评分（error 为技术失败，executor 已标）。
    两阶段幂等（3.4）：先为 enabled 语义维度建 judge 任务并保持 scoring 等 worker；
    全部任务终态（done/failed）后回填 judge_results 完整评分。judge 未配置 → 一次到终态。
    状态：有 error → partial_failed，否则 completed；judge_incomplete 单独置位
    （评分不完整 ≠ 技术失败，run 仍按执行成功收尾）。
    """
    # 延迟导入：宿主单测只测纯函数，不拉 DB 驱动（容器内运行才建引擎）
    from app.core.db import SessionLocal
    from app.models import CaseVersion, EvalResult, EvalRun, JudgeTask

    async with SessionLocal() as db:
        run = await db.get(EvalRun, run_id)
        if run is None:
            return
        if run.status != "scoring":
            # 3.4 幂等：worker 与 _finish 并发触发；非 scoring（已终态/未进入评分）不重复处理
            return
        weights = await _load_weights(db, run.agent_id)
        targets = await _load_targets(db, run.agent_id)
        results = (await db.execute(select(EvalResult).where(
            EvalResult.run_id == run_id))).scalars().all()
        interface_by_case = await _interface_by_case(db, [r.case_id for r in results])

        # ---- 3.4 建 judge 任务（enabled 语义维度；judge 已配置才建，复合主键幂等）----
        judge_by_case: dict[int, list[dict]] = {}
        if await _judge_configured(db):
            tasks = (await db.execute(select(JudgeTask).where(
                JudgeTask.run_id == run_id))).scalars().all()
            keys = {(t.run_id, t.case_id, t.dimension_code) for t in tasks}
            for r in results:
                if r.pass_fail == "error":
                    continue
                cv = await db.get(CaseVersion, r.case_version_id)
                snapshot = cv.snapshot if cv else {}
                for dim in _enabled_semantic_dims(snapshot.get("metrics")):
                    if (run_id, r.case_id, dim) not in keys:
                        db.add(JudgeTask(run_id=run_id, case_id=r.case_id,
                                         dimension_code=dim, status="pending"))
            await db.commit()
            tasks = (await db.execute(select(JudgeTask).where(
                JudgeTask.run_id == run_id))).scalars().all()
            # 存在 pending/processing → 保持 scoring 等 worker（批后触发评分）
            if any(t.status in ("pending", "processing") for t in tasks):
                logger.info("run %s judge 任务未完成，保持 scoring（%d 个）", run_id, len(tasks))
                return
            judge_by_case = _judge_results_by_case(tasks)

        await _score_executed_results(
            db, run, results, weights, targets, interface_by_case, judge_by_case)


async def _score_executed_results(
    db, run, results, weights, targets, interface_by_case, judge_by_case,
    *, keep_terminal: str | None = None,
) -> bool:
    """对已采集结果评分 + run 聚合 + 状态落库（score_run 与 score_run_salvage 共用）。

    keep_terminal=None：正常收尾（completed / partial_failed，写 finished_at）。
    keep_terminal="timeout"/"scoring_failed"（7.5a salvage 兜底）：保持终态不翻转，
    只回填 agent_score/counts/judge_incomplete/precomputed（故障 run 语义保留）。
    返回 True=已评分落库；False=评分异常（salvage 下保持终态，不抛）。
    """
    from app.models import CaseVersion
    na_threshold = float((run.run_config or {}).get(
        "judge_na_threshold", JUDGE_INCOMPLETE_THRESHOLD))
    semantic_na, total_acc_w = 0.0, 0.0
    try:
        for r in results:
            if r.pass_fail == "error":
                continue
            cv = await db.get(CaseVersion, r.case_version_id)
            snapshot = cv.snapshot if cv else {}
            assertion_results = run_assertions(
                _unified(r), snapshot.get("assertions") or [])
            judge_results = judge_by_case.get(r.case_id, [])
            cs = score_case(
                case_metrics=snapshot.get("metrics"),
                assertion_results=assertion_results,
                judge_results=judge_results,
                ttft_p50=_f(r.ttft_p50), ttft_p95=_f(r.ttft_p95),
                e2e_p50=_f(r.e2e_p50), e2e_p95=_f(r.e2e_p95),
                usage=r.usage,
                model=r.model,
                model_price=await _load_price(db, r.model),
                weights=_resolve_weights(weights, interface_by_case.get(r.case_id, 0)),
                targets=_resolve_targets(targets, interface_by_case.get(r.case_id, 0)),
            )
            r.assertion_results = assertion_results
            r.judge_results = judge_results or None
            r.score_total = cs.score_total
            r.score_per_dimension = cs.score_per_dimension
            r.pass_fail = cs.pass_fail
            r.total_cost = cs.total_cost
            semantic_na += cs.semantic_na_weight
            total_acc_w += cs.total_accuracy_weight
    except Exception:
        if keep_terminal is None:
            # 评分异常不阻塞 run 生命周期：标 partial_failed 收尾，结果保留执行态
            logger.exception("run %s 评分失败，标 partial_failed 收尾", run.id)
            run.status = "partial_failed"
            run.finished_at = _now()
            await db.commit()
        else:
            logger.exception("run %s salvage 评分异常，保持终态 %s", run.id, keep_terminal)
        return False

    scored = [r.score_total for r in results
              if r.pass_fail in ("pass", "fail") and r.score_total is not None]
    passed = sum(1 for r in results if r.pass_fail == "pass")
    failed = sum(1 for r in results if r.pass_fail == "fail")
    na = sum(1 for r in results if r.pass_fail == "na")
    error = sum(1 for r in results if r.pass_fail == "error")
    run.agent_score = round(mean(scored), 2) if scored else None
    run.pass_case, run.fail_case, run.na_case, run.error_case = passed, failed, na, error
    run.judge_incomplete = (total_acc_w > 0 and semantic_na / total_acc_w > na_threshold)
    _aggregate_precomputed(run, results)
    if keep_terminal is None:
        run.finished_at = _now()
        run.status = "partial_failed" if error else "completed"
    await db.commit()
    logger.info("run %s 评分完成 status=%s agent_score=%s pass=%s fail=%s na=%s err=%s",
                run.id, run.status, run.agent_score, passed, failed, na, error)
    return True


async def score_run_salvage(run_id: int) -> None:
    """7.5a timeout salvage：scanner 兜底对 timeout/scoring_failed 的 run 出分。

    原链路 scanner 置 timeout 后只 cancel、从不评分 → agent_score 恒 NULL（验收
    「故障后 run 能出分」）。salvage 只评已采集结果（含规则维度 + 已 done 的 judge），
    不新建 judge 任务（故障现场不放大外部请求）；残留 pending/processing 任务废弃为
    failed（judge 未判维度 → N/A + judge_incomplete 兜底）；保持终态
    timeout/scoring_failed 不翻转，只回填评分数据。幂等：已出分或非目标态直接返回。
    """
    from app.core.db import SessionLocal
    from app.models import EvalResult, EvalRun, JudgeTask, TestCase

    async with SessionLocal() as db:
        run = await db.get(EvalRun, run_id)
        if run is None or run.status not in ("timeout", "scoring_failed"):
            return
        if run.agent_score is not None:
            return  # 已 salvage 过（scanner 崩溃窗口内重复触发）
        # 对账：total_case 与结果条数差 → 缺失 case 回填 error（本地复制 orchestrator._finish
        # 的对账语义；scorer 不得 import orchestrator（循环依赖），逻辑自洽）。
        results = (await db.execute(select(EvalResult).where(
            EvalResult.run_id == run_id))).scalars().all()
        if len(results) < run.total_case:
            for case in (await db.execute(select(TestCase).where(
                    TestCase.suite_id == run.suite_id, TestCase.status == "active",
                    TestCase.is_held_out == (run.trigger_type == "held_out")))).scalars():
                if any(r.case_id == case.id for r in results):
                    continue
                db.add(EvalResult(
                    run_id=run_id, case_id=case.id,
                    case_version_id=await _ensure_salvage_version(db, case),
                    pass_fail="error", error_type="timeout",
                    error_detail="未执行（超时回收）", finished_at=_now()))
            await db.commit()
            results = (await db.execute(select(EvalResult).where(
                EvalResult.run_id == run_id))).scalars().all()
        # 残留 judge 任务废弃：pending/processing → failed（done 保留结果），
        # 不再新建任务——故障现场不放大外部请求。
        abandoned = False
        tasks = (await db.execute(select(JudgeTask).where(
            JudgeTask.run_id == run_id))).scalars().all()
        for t in tasks:
            if t.status in ("pending", "processing"):
                t.status = "failed"
                t.claim_id = None
                t.lease_until = None
                abandoned = True
        if abandoned:
            await db.commit()
        weights = await _load_weights(db, run.agent_id)
        targets = await _load_targets(db, run.agent_id)
        interface_by_case = await _interface_by_case(db, [r.case_id for r in results])
        judge_by_case = _judge_results_by_case(tasks)  # 仅采纳 done；failed 维度 → N/A
        await _score_executed_results(
            db, run, results, weights, targets, interface_by_case, judge_by_case,
            keep_terminal=run.status)


async def _ensure_salvage_version(db, case) -> int:
    """取 case 最新版本；无版本则建 version_no=1（salvage 对账回填用）。

    快照口径与 orchestrator._ensure_case_version 一致（回填的 error 行不参与评分，
    复用最新版本即可；新 case 才建 version）。
    """
    from app.models import CaseVersion
    ver = (await db.execute(select(CaseVersion).where(
        CaseVersion.case_id == case.id).order_by(CaseVersion.version_no.desc()))).first()
    if ver is not None:
        return ver[0].id
    snapshot = {
        "input": case.input, "input_turns": case.input_turns, "file_ref": case.file_ref,
        "expected": case.expected, "assertions": case.assertions, "metrics": case.metrics,
    }
    v = CaseVersion(case_id=case.id, version_no=1,
                    content_hash=hashlib.sha256(
                        repr(sorted(snapshot.items())).encode("utf-8")).hexdigest(),
                    snapshot=snapshot)
    db.add(v)
    await db.commit()
    return v.id


def _unified(r: EvalResult) -> dict:
    """从 eval_result 反组装统一结果对象（3.1 断言的输入）。"""
    usage = (r.usage or [{}])[-1] if r.usage else None
    return {
        "answer": r.answer or "",
        "reasoning": r.reasoning,
        "tool_calls": r.tool_calls or [],
        "usage": usage,
        "meta": {},
    }


def _f(v) -> float | None:
    """Decimal/None → float|None（Numeric 列统一转 float 再参与算术）。"""
    return float(v) if v is not None else None


async def _load_weights(db, agent_id: int) -> dict:
    """维度权重 → {(interface_id, dimension_code): weight}（接口级 + 默认，评分按接口解析）。"""
    from app.models import AgentDimensionWeight
    rows = (await db.execute(select(AgentDimensionWeight).where(
        AgentDimensionWeight.agent_id == agent_id))).scalars().all()
    return {(r.interface_id, r.dimension_code): float(r.weight) for r in rows}


async def _load_targets(db, agent_id: int) -> dict:
    """baseline_target → {(interface_id, dimension_code): target}。"""
    from app.models import BaselineTarget
    rows = (await db.execute(select(BaselineTarget).where(
        BaselineTarget.agent_id == agent_id))).scalars().all()
    return {(r.interface_id, r.dimension_code): float(r.target_score) for r in rows}


async def _interface_by_case(db, case_ids: list[int]) -> dict:
    """用例 → interface_id 映射（test_case.interface_id，门禁 target 按接口解析）。"""
    if not case_ids:
        return {}
    from app.models import TestCase
    rows = (await db.execute(select(TestCase.id, TestCase.interface_id).where(
        TestCase.id.in_(set(case_ids))))).all()
    return {row[0]: row[1] for row in rows}


def _resolve_targets(targets: dict, interface_id: int) -> dict:
    """interface 级 target 优先，缺省回退 agent 默认（interface_id=0 哨兵）。"""
    resolved = {dcode: t for (iid, dcode), t in targets.items() if iid == interface_id}
    for (iid, dcode), t in targets.items():
        if iid == 0:
            resolved.setdefault(dcode, t)
    return resolved


def _resolve_weights(weights: dict, interface_id: int) -> dict:
    """interface 级权重优先，缺省回退 agent 默认（interface_id=0 哨兵），与 _resolve_targets 同口径。"""
    resolved = {dcode: w for (iid, dcode), w in weights.items() if iid == interface_id}
    for (iid, dcode), w in weights.items():
        if iid == 0:
            resolved.setdefault(dcode, w)
    return resolved


async def _load_price(db, model: str | None) -> dict | None:
    """model_price：按 model 取最近 effective_from 单价。无 model/无记录 → None。"""
    if not model:
        return None
    from app.models import ModelPrice
    row = (await db.execute(select(ModelPrice).where(
        ModelPrice.model == model).order_by(ModelPrice.effective_from.desc()))).scalars().first()
    if row is None:
        return None
    return {"input": float(row.input_price), "output": float(row.output_price),
            "cache_hit": float(row.cache_hit_price) if row.cache_hit_price is not None else None}


async def _judge_configured(db) -> bool:
    """judge 可用判定：system_config judge_llm.base_url/model_name + env judge_api_key。"""
    from app.core.config import settings
    from app.judge.client import is_configured
    from app.models import SystemConfig

    rows = (await db.execute(select(SystemConfig).where(
        SystemConfig.key.in_(("judge_llm.base_url", "judge_llm.model_name"))))).scalars().all()
    cfg = {r.key: r.value for r in rows}
    return is_configured(settings.judge_api_key,
                         (cfg.get("judge_llm.base_url") or "").strip(),
                         (cfg.get("judge_llm.model_name") or "").strip())


def _judge_results_by_case(tasks) -> dict[int, list[dict]]:
    """done 任务的 result → {case_id: [{dimension, score, reason, rubric_version}]}。

    failed 任务不传 → 该 case 语义维度 N/A（judge_fail），judge_incomplete 机制兜底。
    """
    out: dict[int, list[dict]] = {}
    for t in tasks:
        if t.status != "done" or not t.result:
            continue
        out.setdefault(t.case_id, []).append({
            "dimension": t.dimension_code,
            "score": t.result.get("score"),
            "reason": t.result.get("reason"),
            "rubric_version": t.result.get("rubric_version"),
        })
    return out


def _aggregate_precomputed(run: EvalRun, results: list[EvalResult]) -> None:
    """run 级预聚合列（§15.5）：性能取各用例 p50/p95 均值，tokens/成本求和。"""
    for attr in ("ttft_p50", "ttft_p95", "e2e_p50", "e2e_p95"):
        vals = [_f(getattr(r, attr)) for r in results if getattr(r, attr) is not None]
        setattr(run, attr, round(mean(vals), 3) if vals else None)
    run.total_tokens = sum(r.total_tokens or 0 for r in results)
    costs = [float(r.total_cost) for r in results if r.total_cost is not None]
    run.total_cost = round(sum(costs), 6) if costs else None


def _now() -> datetime:
    """naive UTC（与 orchestrator 同口径，防 aware/naive 比较异常）。"""
    return datetime.utcnow()
