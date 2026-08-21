"""6.4 judge 漂移检测：重判 is_gold 用例，比对 judge_gold_scores，写 JudgeDriftHistory。

手动触发（POST /meta/drift/check）——重判烧 judge LLM token，低频高成本操作，
不做 scanner 周期任务（与数据清理 ⑦ 区分）。judge 未配置 → 明确报错。
支持粒度限定（可选 dimension_code / agent_id）：只重判指定维度或指定 agent 的 gold 用例，
避免全量重判浪费 token。一致率口径：|重判分 − gold分| ≤ 10 计一致（等级严格一致），按维度聚合；
drift_flag = 一致率 < judge_drift_consistency_threshold（global 热生效）。
超阈值维度写 issue 告警（复用 6.2 问题闭环；同维度未关闭告警幂等跳过）。
judge 调用单点失败只剔除该用例维度，不中断整体。
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from app.core.errors import ApiError, E_VALIDATION
from app.judge.drift import DRIFT_SCORE_TOLERANCE, dim_consistency, is_drift

logger = logging.getLogger(__name__)

DRIFT_ISSUE_TITLE = "judge 漂移告警-{dim}"
# 参与复现验证的未关闭状态（与 issue_rules.ISSUE_ACTIVE_STATUS 同口径，防导入环）
_DRIFT_ACTIVE = ("open", "fixing", "fixed", "verified")


async def _judge_cfg(db) -> dict:
    """judge 全局配置（system_config，is_hot 热生效）。timeout 无 run 快照 → 默认 120。"""
    from app.models import SystemConfig
    keys = {"judge_llm.base_url", "judge_llm.model_name", "llm_allowlist"}
    rows = (await db.execute(select(SystemConfig).where(SystemConfig.key.in_(keys)))).scalars().all()
    cfg = {r.key: r.value for r in rows}
    return {
        "base_url": (cfg.get("judge_llm.base_url") or "").strip(),
        "model": (cfg.get("judge_llm.model_name") or "").strip(),
        "allowlist": cfg.get("llm_allowlist") or [],
        "timeout": 120.0,
    }


async def _gold_cases(db, agent_id: int | None = None) -> list[dict]:
    """is_gold 用例 + 所属 agent + 最近 answer + 金标准判分维度（无 gold_scores 跳过）。

    agent_id 非空 → 只取该 agent 的 gold 用例（漂移检测粒度限定）。
    """
    from app.models import Agent, CaseVersion, EvalResult, TestCase, TestSuite
    stmt = (select(TestCase, Agent.id).select_from(TestCase)
            .join(TestSuite, TestCase.suite_id == TestSuite.id)
            .join(Agent, TestSuite.agent_id == Agent.id)
            .where(TestCase.is_gold == True))
    if agent_id is not None:
        stmt = stmt.where(Agent.id == agent_id)
    rows = (await db.execute(stmt)).all()
    out = []
    for tc, agent_id in rows:
        expected = tc.expected or {}
        gold = expected.get("judge_gold_scores") or {}
        if not gold:
            continue
        answer_row = (await db.execute(select(EvalResult.answer).where(
            EvalResult.case_id == tc.id, EvalResult.answer.isnot(None),
        ).order_by(EvalResult.run_id.desc()).limit(1))).first()
        cv = (await db.execute(select(CaseVersion).where(
            CaseVersion.case_id == tc.id).order_by(CaseVersion.version_no.desc()).limit(1))).scalars().first()
        snapshot = cv.snapshot if cv else {}
        out.append({
            "case": tc, "agent_id": agent_id, "gold": gold,
            "answer": answer_row[0] if answer_row else None,
            "input": snapshot.get("input") or tc.input,
            "golden_answer": (snapshot.get("expected") or {}).get("golden_answer"),
            "reference_docs": (snapshot.get("expected") or {}).get("reference_docs"),
        })
    return out


async def _rubric_version(db, dimension_code: str, interface_id: int) -> str:
    """rubric 版本号（与 worker._rubric_version 同口径：interface 优先 + 通用兜底）。"""
    from app.judge.rubric import BUILTIN_RUBRIC_VERSION
    from app.models import JudgeRubric
    row = (await db.execute(select(JudgeRubric.version).where(
        JudgeRubric.dimension_code == dimension_code,
        JudgeRubric.interface_id.in_((0, interface_id)),
    ).order_by(JudgeRubric.interface_id.desc(), JudgeRubric.id.desc()).limit(1))).first()
    return row[0] if row else BUILTIN_RUBRIC_VERSION


async def _drift_threshold(db) -> float:
    from app.models import SystemConfig
    row = await db.get(SystemConfig, "judge_drift_consistency_threshold")
    return float((row.value if row else None) or 0.8)


async def _write_drift_issue(db, dim: str, rate: float, cnt: int, golds: list[dict]) -> None:
    """超阈值写 issue 告警：agent_id 取该维度 gold 用例所属 agent（FK 需真实 id）；幂等去重。"""
    from app.models import Issue
    agent_ids = {g["agent_id"] for g in golds if dim in g["gold"]}
    if not agent_ids:
        return
    # 同维度未关闭告警已存在 → 幂等跳过（避免每次检测都重复建 issue）
    exists = (await db.execute(select(Issue.id).where(
        Issue.title == DRIFT_ISSUE_TITLE.format(dim=dim),
        Issue.status.in_(_DRIFT_ACTIVE)))).scalars().first()
    if exists:
        return
    db.add(Issue(
        agent_id=sorted(agent_ids)[0],
        title=DRIFT_ISSUE_TITLE.format(dim=dim),
        description=f"金标准重判一致率 {rate}%（{cnt} 例），低于阈值，judge 评分可能漂移",
        related_dimension=dim, severity="medium", status="open",
    ))
    logger.warning("judge 漂移告警：维度 %s 一致率 %s（%d 例）", dim, rate, cnt)


async def _notify_drift(dim: str, rate: float, threshold: float, cnt: int, active: bool) -> None:
    """6.6 通知（统一入口；异常隔离——通知失败绝不影响检测流程）。"""
    try:
        from app.core.alarm import notify_alarm
        if active:
            summary = (f"维度 {dim} judge 一致性率 {rate}% < 阈值 {threshold}%"
                       f"（{cnt} 条金标准用例重判），judge 评分可能漂移")
        else:
            summary = (f"维度 {dim} judge 一致性率 {rate}% 回到阈值 {threshold}% 之上，漂移告警解除")
        await notify_alarm("drift", f"drift-{dim}", None, active, summary)
    except Exception:
        logger.exception("drift 通知异常（不影响检测流程）")


async def run_drift_check(dimension_code: str | None = None,
                          agent_id: int | None = None) -> dict:
    """重判 is_gold 用例（可限定维度/agent），写漂移历史 + 超阈值告警，返回本次明细。

    dimension_code 非空 → 只重判该维度的金标准（省 token）；
    agent_id 非空 → 只重判该 agent 的 gold 用例。
    纯 DB 编排；judge 未配置 / 无金标准数据（按 agent/维度区分）/
    金标准无采集 answer / 全部重判失败 → ApiError（错误信息区分根因）。
    """
    from app.core.config import settings
    from app.core.constants import SEMANTIC_DIMENSIONS
    from app.core.db import SessionLocal
    from app.judge.client import JudgeClient, JudgeError, is_configured
    from app.judge.rubric import load_rubric
    from app.models import JudgeDriftHistory

    async with SessionLocal() as db:
        cfg = await _judge_cfg(db)
        if not is_configured(settings.judge_api_key, cfg["base_url"], cfg["model"]):
            raise ApiError(E_VALIDATION,
                           "judge 未配置，无法执行漂移检测（配置中心 judge_llm.* + env judge_api_key）", 400)
        threshold = await _drift_threshold(db)
        golds = await _gold_cases(db, agent_id)
        if dimension_code:
            golds = [g for g in golds if dimension_code in g["gold"]]
        if not golds:
            # 区分根因：agent 无 gold / 维度无 gold / 同传（agent 有 gold 但该维度没有）/
            # 全局无 gold（seed 可补齐提示仅全局场景有意义）
            if agent_id is not None and dimension_code is not None:
                raise ApiError(E_VALIDATION,
                               f"agent {agent_id} 在维度 {dimension_code} 下无金标准用例"
                               "（is_gold + judge_gold_scores），无法检测漂移", 400)
            if agent_id is not None:
                raise ApiError(E_VALIDATION,
                               f"agent {agent_id} 无金标准用例（is_gold + judge_gold_scores），无法检测漂移", 400)
            if dimension_code is not None:
                raise ApiError(E_VALIDATION,
                               f"维度 {dimension_code} 无金标准用例（is_gold + judge_gold_scores），无法检测漂移", 400)
            raise ApiError(E_VALIDATION,
                           "无金标准用例（is_gold + judge_gold_scores），无法检测漂移（seed 可补齐）", 400)

        pairs: dict[str, list[tuple[float, float]]] = {}
        details: list[dict] = []
        client = JudgeClient(base_url=cfg["base_url"], model=cfg["model"],
                             api_key=settings.judge_api_key, allowlist=cfg["allowlist"],
                             timeout=cfg["timeout"])
        for g in golds:
            tc = g["case"]
            # answer 为空（该 case 最近 run 采集失败）→ 重判空输出无意义，跳过
            if not g["answer"]:
                continue
            for dim, spec in g["gold"].items():
                # 只重判 LLM 语义维度；completeness 等由断言确定性给出、无 rubric，
                # 标进 judge_gold_scores 也不进 LLM 重判（否则 load_rubric 抛 ValueError）
                if dim not in SEMANTIC_DIMENSIONS:
                    continue
                if dimension_code and dim != dimension_code:
                    continue
                gold_score = float(spec.get("score") or 0)
                try:
                    template = await load_rubric(db, dim, tc.interface_id)
                    version = await _rubric_version(db, dim, tc.interface_id)
                    verdict = await client.judge(dimension=dim, template=template,
                                                 case_input=g["input"],
                                                 golden_answer=g["golden_answer"],
                                                 reference_docs=g["reference_docs"],
                                                 agent_output=g["answer"],
                                                 rubric_version=version)
                    agree = abs(verdict.score - gold_score) <= DRIFT_SCORE_TOLERANCE
                    pairs.setdefault(dim, []).append((verdict.score, gold_score))
                    details.append({
                        "case_id": tc.id, "case_name": tc.name, "dimension": dim,
                        "gold_score": gold_score, "rejudge_score": verdict.score,
                        "agree": agree, "reason": verdict.reason,
                    })
                except (JudgeError, ValueError) as e:
                    # ValueError：load_rubric 对无 rubric 维度兜底（防御未来误标）
                    logger.warning("漂移检测 case=%s dim=%s 重判失败：%s", tc.id, dim, e)
        if not pairs:
            # 区分根因：全部无采集 answer vs 部分无 answer vs 全重判失败（judge 异常）
            no_answer = sum(1 for g in golds if not g["answer"])
            if no_answer == len(golds):
                raise ApiError(E_VALIDATION,
                               "金标准用例暂无有效采集 answer（最近 run 未采集到输出），无法重判；"
                               "请先跑一次评测采集输出", 400)
            if no_answer:
                raise ApiError(E_VALIDATION,
                               f"{no_answer}/{len(golds)} 个金标准用例无有效采集 answer，"
                               "其余用例重判失败，本次未产出漂移历史", 400)
            raise ApiError(E_VALIDATION,
                           "全部金标准重判失败（judge 调用异常），本次未产出漂移历史", 400)

        history: list[dict] = []
        for dim, ds in pairs.items():
            rate = dim_consistency(ds)
            flag = is_drift(rate, threshold)
            db.add(JudgeDriftHistory(dimension_code=dim, consistency_rate=rate,
                                     judged_case_cnt=len(ds), drift_flag=flag))
            history.append({"dimension_code": dim, "consistency_rate": rate,
                            "judged_case_cnt": len(ds), "drift_flag": flag})
            if flag:
                await _write_drift_issue(db, dim, rate, len(ds), golds)
                await _notify_drift(dim, rate, threshold, len(ds), True)
            else:
                # 不漂移 → 通知恢复（未处于告警态时内部静默）
                await _notify_drift(dim, rate, threshold, len(ds), False)
        await db.commit()
        logger.info("漂移检测完成：%d 维度，告警 %s", len(history),
                    sum(1 for h in history if h["drift_flag"]))
        return {"threshold": threshold, "history": history, "details": details}
