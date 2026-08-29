"""run 触发与管理（§4.3）：创建触发 / 列表 / 详情 / cancel / rerun。

创建时互斥：同一 agent 同时只允许一个 in_progress run（§15.4）。
run_config 快照 scope=run 的 system_config（冻结值，执行期不读热配置）。
触发采用后台 asyncio task（orchestrator 内部自管 DB session）。
"""
import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, is_held_out_visible, require_role, viewer_sees_evidence
from app.core.audit import write_audit
from app.core.constants import ACCURACY_DIMENSIONS
from app.core.dashboard_rules import PF_ORDER, resolve_targets
from app.core.db import get_db
from app.core.errors import ApiError, E_NO_PERMISSION, E_NOT_FOUND, E_RUN_MUTEX, E_VALIDATION
from app.core.lock import agent_mutex
from app.core.response import ok
from app.models import (
    Agent, AgentInterface, BaselineTarget, EvalResult, EvalRun, SystemConfig, TestCase,
    TestSuite,
)
from app.models.user import User
from app.runner.orchestrator import orchestrator

router = APIRouter(prefix="/runs", tags=["runs"])

Admin = Depends(require_role("admin"))
Staff = Depends(require_role("admin", "evaluator"))

# P2-C4：必须锚定结尾——原 `^\d+\.\d+\.\d+` 只验前缀，`1.2.3<script>` 能入库，
# 其值会被 Dashboard 图表 tooltip 当 HTML 渲染成 XSS。\Z 拒绝尾部任何字符（含换行）。
_SEMVER = re.compile(r"^\d+\.\d+\.\d+\Z")
_ACTIVE_STATUS = ("pending", "running", "scoring")

logger = logging.getLogger(__name__)


class RunCreate(BaseModel):
    agent_id: int
    suite_id: int
    version: str = Field(min_length=1, max_length=64)
    # 6.4 留出集复测：trigger_type=held_out 只跑 is_held_out 用例（QA 触发）
    trigger_type: Literal["manual", "held_out"] = "manual"


async def _snapshot_run_config(db: AsyncSession) -> dict:
    """快照 scope=run 的 system_config（创建时冻结，执行期不再读热配置）。"""
    rows = (await db.execute(select(SystemConfig).where(SystemConfig.scope == "run"))).scalars().all()
    return {r.key: r.value for r in rows}


async def _is_held_out_hidden(db: AsyncSession, run: EvalRun, user: User) -> bool:
    """7.8 前置⑤：留出集 owner 隐藏。owner 不可见 held-out run 的用例结果
    （deps.is_held_out_visible 同语义；与 annotations.py 对 held-out case 的处理一致）。"""
    agent = await db.get(Agent, run.agent_id)
    return not is_held_out_visible(run.trigger_type, user, agent.owner_id if agent else None)


def _run_out(r: EvalRun, *, redact: bool = False) -> dict:
    """run 摘要。redact=True：裁剪结果型字段（P2-D8 owner 对 held_out run 隐藏聚合结果）。

    owner 不可见留出集复测的分数/通过数/延迟，但 run 记录本身保留——它占用 agent
    执行槽位（create_run 互斥 409），owner 需知情。状态型元数据不裁剪。
    """
    return {
        "id": r.id, "agent_id": r.agent_id, "suite_id": r.suite_id, "version": r.version,
        "trigger_type": r.trigger_type, "status": r.status, "generation": r.generation,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "total_case": r.total_case,
        "pass_case": None if redact else r.pass_case,
        "fail_case": None if redact else r.fail_case,
        "error_case": None if redact else r.error_case,
        "na_case": None if redact else r.na_case,
        "agent_score": None if redact else (float(r.agent_score) if r.agent_score is not None else None),
        "judge_incomplete": None if redact else r.judge_incomplete,
        "ttft_p50": None if redact else (float(r.ttft_p50) if r.ttft_p50 is not None else None),
        "e2e_p50": None if redact else (float(r.e2e_p50) if r.e2e_p50 is not None else None),
    }


@router.post("")
async def create_run(body: RunCreate, user: User = Staff, db: AsyncSession = Depends(get_db)):
    logger.debug("create_run in: agent_id=%s suite_id=%s version=%s trigger_type=%s",
                 body.agent_id, body.suite_id, body.version, body.trigger_type)
    # 权限分级：manual（常规评测）与 held_out（留出集复测）均 admin/evaluator（Staff）。
    # evaluator 作为评测师可触发评测（阶段 4 走查决策 #9）。
    if not _SEMVER.match(body.version):
        raise ApiError(E_VALIDATION, "version 需符合 semver（如 1.2.3）", 400)
    agent = await db.get(Agent, body.agent_id)
    if agent is None or not agent.enabled:
        raise ApiError(E_NOT_FOUND, "agent 不存在或已禁用", 404)
    suite = await db.get(TestSuite, body.suite_id)
    if suite is None or suite.agent_id != body.agent_id:
        raise ApiError(E_VALIDATION, "suite 不存在或不属于该 agent", 400)
    # 互斥：同 agent 单 in_progress run（§15.4）。7.6 C1 多 worker 化：
    # 持 agent 行 FOR UPDATE 锁跨 worker 串行「查 active + 插 run + commit」，
    # 防止两 worker 同时过互斥检查各建一个 run（内存态互斥不跨进程）。
    # P2-D2 修复：活跃检查必须锁定读（with_for_update）——REPEATABLE READ 下本事务
    # 首个一致性读（上方 db.get(Agent)）已建立快照，普通读查 active 会读到旧快照
    # 漏掉并发 worker 刚提交的 run（实测双插）；锁定读永远读最新已提交数据。
    async with agent_mutex(body.agent_id, db):
        active = (await db.execute(select(EvalRun.id).with_for_update().where(
            EvalRun.agent_id == body.agent_id, EvalRun.status.in_(_ACTIVE_STATUS)))).first()
        if active:
            raise ApiError(E_RUN_MUTEX, "该 agent 已有进行中的 run，请等待完成或取消", 409)

        run = EvalRun(
            agent_id=body.agent_id, suite_id=body.suite_id, version=body.version,
            trigger_type=body.trigger_type, status="pending", generation=1,
            run_config=await _snapshot_run_config(db),
            # 7.5a pending 回收兜底：创建即写初始租约（start_run 心跳立即覆盖为计算值，正常 run 零影响）；
            # 若 orchestrator 未接管（进程崩溃/任务丢失），scanner ① 回收 pending 释放互斥槽
            lease_until=datetime.utcnow() + timedelta(seconds=90),
        )
        db.add(run)
        await db.commit()
        run_id = run.id
    # 异步触发执行（不阻塞请求）
    asyncio.get_running_loop().create_task(orchestrator.start_run(run_id))
    logger.info("run %s 已创建并触发 agent=%s version=%s trigger=%s",
                run_id, body.agent_id, body.version, body.trigger_type)
    return ok({"id": run_id, "status": "pending"})


@router.get("")
async def list_runs(agent_id: int | None = None, limit: int = 50, offset: int = 0,
                    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    stmt = select(EvalRun).order_by(EvalRun.id.desc())
    if agent_id is not None:
        stmt = stmt.where(EvalRun.agent_id == agent_id)
    rows = (await db.execute(stmt.limit(min(limit, 200)).offset(max(offset, 0)))).scalars().all()
    # P2-D8：批量查 agent owner → owner 对 held_out run 裁剪聚合结果。列表不能逐条 404
    #（会破坏分页语义），run 记录保留、结果型字段置 None。
    owner_map = {}
    if rows:
        owner_map = {a.id: a.owner_id for a in (await db.execute(
            select(Agent.id, Agent.owner_id).where(
                Agent.id.in_({r.agent_id for r in rows})))).all()}
    return ok([_run_out(r, redact=not is_held_out_visible(r.trigger_type, user, owner_map.get(r.agent_id)))
               for r in rows])


@router.get("/{run_id}")
async def get_run(run_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    run = await db.get(EvalRun, run_id)
    if run is None:
        raise ApiError(E_NOT_FOUND, "run 不存在", 404)
    # P2-D8：owner 对 held_out run 裁剪聚合结果（记录可见、结果不可见；run_results 同源判定）
    return ok(_run_out(run, redact=await _is_held_out_hidden(db, run, user)))


@router.post("/{run_id}/cancel")
async def cancel_run(run_id: int, request: Request, _: User = Staff,
                     db: AsyncSession = Depends(get_db)):
    run = await db.get(EvalRun, run_id)
    if run is None:
        raise ApiError(E_NOT_FOUND, "run 不存在", 404)
    if run.status not in _ACTIVE_STATUS:
        raise ApiError(E_VALIDATION, f"run 已终态（{run.status}），不可取消", 400)
    run.generation += 1  # fencing 记录
    run.status = "cancelled"
    run.finished_at = datetime.utcnow()  # naive UTC（与 orchestrator 一致）
    orchestrator.cancel_run(run_id)
    # 7.7 cancel 审计：与取消同事务，只 add 不单独 commit（write_audit 约定）
    await write_audit(db, _, request, "run.cancel", "run", run_id)
    await db.commit()
    logger.info("run %s 已取消", run_id)
    return ok()


@router.post("/{run_id}/rerun")
async def rerun_run(run_id: int, request: Request, _: User = Staff,
                    db: AsyncSession = Depends(get_db)):
    src = await db.get(EvalRun, run_id)
    if src is None:
        raise ApiError(E_NOT_FOUND, "run 不存在", 404)
    # 7.6 C1 同 create_run：跨 worker 串行「查 active + 插 run + commit」
    # P2-D2：同 create_run，活跃检查锁定读，避免 REPEATABLE READ 旧快照漏看并发 run。
    async with agent_mutex(src.agent_id, db):
        active = (await db.execute(select(EvalRun.id).with_for_update().where(
            EvalRun.agent_id == src.agent_id, EvalRun.status.in_(_ACTIVE_STATUS)))).first()
        if active:
            raise ApiError(E_RUN_MUTEX, "该 agent 已有进行中的 run，请等待完成或取消", 409)
        run = EvalRun(
            agent_id=src.agent_id, suite_id=src.suite_id, version=src.version,
            trigger_type=src.trigger_type, status="pending", generation=1,
            run_config=src.run_config,  # 复用冻结配置
            lease_until=datetime.utcnow() + timedelta(seconds=90),  # 7.5a 同 create_run
        )
        db.add(run)
        # 7.7 rerun 审计：target 源 run，detail 带新 run id（与创建同事务）
        await write_audit(db, _, request, "run.rerun", "run", src.id,
                          detail={"new_run_id": run.id})
        await db.commit()
    asyncio.get_running_loop().create_task(orchestrator.start_run(run.id))
    logger.info("run %s rerun 创建为 %s", run_id, run.id)
    return ok({"id": run.id, "status": "pending"})


# ---------------- 5.3 看板数据：L3 用例明细 / L3.5 门禁失败摘要 / L4 证据 ----------------


@router.get("/{run_id}/results")
async def run_results(run_id: int, user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_db)):
    """L3 用例明细：run 内全部用例结果（error/fail 靠前，其余按分数降序）。"""
    logger.debug("run_results in: run_id=%s user=%s", run_id, user.username)
    run = await db.get(EvalRun, run_id)
    if run is None:
        raise ApiError(E_NOT_FOUND, "run 不存在", 404)
    # 7.8 前置⑤：留出集 owner 视为不存在（不泄露 held-out 结果）
    if await _is_held_out_hidden(db, run, user):
        raise ApiError(E_NOT_FOUND, "run 不存在", 404)
    results = (await db.execute(select(EvalResult).where(
        EvalResult.run_id == run_id))).scalars().all()
    if not results:
        return ok([])
    cases = {c.id: c for c in (await db.execute(select(TestCase).where(
        TestCase.id.in_([r.case_id for r in results])))).scalars().all()}
    ifaces = {i.id: i.name for i in (await db.execute(
        select(AgentInterface).where(AgentInterface.agent_id == run.agent_id))).scalars().all()}
    out = [{
        "id": r.id, "case_id": r.case_id,
        "case_name": cases[r.case_id].name if r.case_id in cases else "",
        "interface_name": ifaces.get(cases[r.case_id].interface_id, "")
        if r.case_id in cases else "",
        "input_type": cases[r.case_id].input_type if r.case_id in cases else "",
        "pass_fail": r.pass_fail,
        "score_total": float(r.score_total) if r.score_total is not None else None,
        "score_per_dimension": r.score_per_dimension or [],
        "error_type": r.error_type, "error_detail": r.error_detail,
    } for r in results]
    out.sort(key=lambda x: (PF_ORDER.get(x["pass_fail"], 9),
                            -(x["score_total"] or -1)))
    logger.debug("run_results out: count=%s", len(out))
    return ok(out)


@router.get("/{run_id}/failures")
async def run_failures(run_id: int, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_db)):
    """L3.5 门禁失败摘要：fail 用例 + 不达标维度/断言/judge 失败（viewer 可达）。
    judge 失败理由属证据级（L4），viewer 不可见（7.6 A2 与 result_evidence 403 同语义）。
    """
    logger.debug("run_failures in: run_id=%s user=%s", run_id, user.username)
    run = await db.get(EvalRun, run_id)
    if run is None:
        raise ApiError(E_NOT_FOUND, "run 不存在", 404)
    # 7.8 前置⑤：留出集 owner 视为不存在
    if await _is_held_out_hidden(db, run, user):
        raise ApiError(E_NOT_FOUND, "run 不存在", 404)
    results = (await db.execute(select(EvalResult).where(
        EvalResult.run_id == run_id, EvalResult.pass_fail == "fail"))).scalars().all()
    if not results:
        return ok([])
    cases = {c.id: c for c in (await db.execute(select(TestCase).where(
        TestCase.id.in_([r.case_id for r in results])))).scalars().all()}
    ifaces = {i.id: i.name for i in (await db.execute(
        select(AgentInterface).where(AgentInterface.agent_id == run.agent_id))).scalars().all()}
    targets = await _load_agent_targets(db, run.agent_id)
    sees_evidence = viewer_sees_evidence(user)

    out = []
    for r in results:
        case = cases.get(r.case_id)
        resolved = resolve_targets(targets, case.interface_id if case else 0)
        fail_dims = []
        for entry in r.score_per_dimension or []:
            code = entry.get("code")
            if code not in ACCURACY_DIMENSIONS or entry.get("na") or entry.get("value") is None:
                continue
            value = float(entry["value"])
            target = resolved.get(code)
            if target is not None and value < target:
                fail_dims.append({"code": code, "value": value, "target": target})
        fail_codes = {d["code"] for d in fail_dims}
        judge_failures = [j for j in (r.judge_results or [])
                          if j.get("dimension") in fail_codes]
        out.append({
            "case_id": r.case_id,
            "case_name": case.name if case else "",
            "interface_name": ifaces.get(case.interface_id, "") if case else "",
            "score_total": float(r.score_total) if r.score_total is not None else None,
            "fail_dims": fail_dims,
            "assertion_failures": [a for a in (r.assertion_results or []) if not a.get("pass")],
            "judge_failures": judge_failures if sees_evidence else [],
        })
    logger.debug("run_failures out: count=%s", len(out))
    return ok(out)


@router.get("/{run_id}/results/{result_id}/evidence")
async def result_evidence(run_id: int, result_id: int, user: User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)):
    """L4 证据：reasoning 全文 / judge 理由 / 断言详情 / 工具调用（viewer 403）。"""
    logger.debug("result_evidence in: run_id=%s result_id=%s user=%s",
                 run_id, result_id, user.username)
    if not viewer_sees_evidence(user):
        raise ApiError(E_NO_PERMISSION, "viewer 无权查看评测证据", 403)
    # 7.8 前置⑤：留出集 owner 视为不存在（先于结果校验，不泄露 held-out 存在性）
    run = await db.get(EvalRun, run_id)
    if run is not None and await _is_held_out_hidden(db, run, user):
        raise ApiError(E_NOT_FOUND, "结果不存在", 404)
    result = await db.get(EvalResult, result_id)
    if result is None or result.run_id != run_id:
        raise ApiError(E_NOT_FOUND, "结果不存在", 404)
    out = {
        "id": result.id, "case_id": result.case_id,
        "answer": result.answer, "reasoning": result.reasoning,
        "tool_calls": result.tool_calls,
        "usage": result.usage, "timing": result.timing,
        "assertion_results": result.assertion_results,
        "judge_results": result.judge_results,
        "error_type": result.error_type, "error_detail": result.error_detail,
    }
    logger.debug("result_evidence out: case_id=%s", out["case_id"])
    return ok(out)


async def _load_agent_targets(db: AsyncSession, agent_id: int) -> dict:
    """{(interface_id, dimension_code): target}，与 scorer._load_targets 同口径。"""
    rows = (await db.execute(select(BaselineTarget).where(
        BaselineTarget.agent_id == agent_id))).scalars().all()
    return {(r.interface_id, r.dimension_code): float(r.target_score) for r in rows}
