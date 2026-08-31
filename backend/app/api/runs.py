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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.assertions.run import _truncate  # D3 answer 截断与断言 actual 同口径（500）
from app.api.deps import get_current_user, is_held_out_visible, require_role, sees_evidence_full
from app.core.audit import write_audit
from app.core.constants import ACCURACY_DIMENSIONS
from app.core.dashboard_rules import PF_ORDER, resolve_targets
from app.core.db import get_db
from app.core.errors import ApiError, E_NOT_FOUND, E_RUN_MUTEX, E_VALIDATION
from app.core.lock import agent_mutex
from app.core.response import ok
from app.models import (
    Agent, AgentInterface, BaselineTarget, EvalResult, EvalRun, JudgeTask, SystemConfig,
    TestCase, TestSuite,
)
from app.models.user import User
from app.runner.orchestrator import orchestrator
from app.runner.scorer import _gate_met  # #2 门禁判定同源（fail_dims 与 run 门禁同一口径）

router = APIRouter(prefix="/runs", tags=["runs"])

Admin = Depends(require_role("admin"))
Staff = Depends(require_role("admin", "evaluator"))

# P2-C4：必须锚定结尾——原 `^\d+\.\d+\.\d+` 只验前缀，`1.2.3<script>` 能入库，
# 其值会被 Dashboard 图表 tooltip 当 HTML 渲染成 XSS。\Z 拒绝尾部任何字符（含换行）。
_SEMVER = re.compile(r"^\d+\.\d+\.\d+\Z")
# #4 放开并发：互斥槽（执行中）与可取消槽拆开——scoring 是「采集完成待评分」终态，
# 不占用 agent 执行能力，同 agent 可在判分期间开新 run；cancel 仍允许 scoring（判分期可放弃）。
_ACTIVE_STATUS = ("pending", "running", "scoring")   # 可取消状态（cancel 判断，含 scoring）
_MUTEX_STATUS = ("pending", "running")               # 互斥槽（create/rerun 计数，scoring 不占）
_DEFAULT_MAX_ACTIVE_RUNS = 1  # 未配置/配置缺失兜底（N=1 = 与旧「单活跃 run」一致）
_ANSWER_PREVIEW = 500  # D3 answer 截断预览：与断言 actual 同口径（assertions.run._ACTUAL_TRUNCATE）
_TOP_REASONS_N = 5     # L3.5 top 扣分原因条数上限
_TOP_REASONS_SAMPLE = 3  # 每个 top 维度展示的代表 reason 条数（去重保序）

logger = logging.getLogger(__name__)


class RunCreate(BaseModel):
    agent_id: int
    suite_id: int
    version: str = Field(min_length=1, max_length=64)
    # 6.4 留出集复测：trigger_type=held_out 只跑 is_held_out 用例（QA 触发）
    trigger_type: Literal["manual", "held_out"] = "manual"
    # #3 定向重跑：None=全量；非空=只跑指定 case 子集（去重/归属/留出集匹配由创建接口校验）
    case_ids: list[int] | None = None


class RerunBody(BaseModel):
    # #3：None=继承源 run 子集（重跑同一批）；非空=定向改批
    case_ids: list[int] | None = None


async def _snapshot_run_config(db: AsyncSession) -> dict:
    """快照 scope=run 的 system_config（创建时冻结，执行期不再读热配置）。"""
    rows = (await db.execute(select(SystemConfig).where(SystemConfig.scope == "run"))).scalars().all()
    return {r.key: r.value for r in rows}


async def _max_active_runs(db: AsyncSession) -> int:
    """#4 并发上限：同 agent 允许的执行中 run 数（互斥计数阈值）。热读 scope=global 配置。

    缺行（未 seed 库/集成测试 sqlite）或非法值 → 兜底 _DEFAULT_MAX_ACTIVE_RUNS=1，
    保证 N=1 时与旧「任一活跃即 409」行为一致。非法值兜底而非抛错：配置坏不阻塞创建。
    """
    row = (await db.execute(select(SystemConfig).where(
        SystemConfig.scope == "global",
        SystemConfig.key == "max_active_runs_per_agent"))).scalars().first()
    if row is None or row.value is None:
        return _DEFAULT_MAX_ACTIVE_RUNS
    try:
        return max(1, int(row.value))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_ACTIVE_RUNS


async def _validate_case_ids(db: AsyncSession, suite_id: int, trigger_type: str,
                             case_ids: list[int] | None) -> list[int] | None:
    """#3 定向重跑：case_ids 校验，返回有序去重列表（None/空=全量）。

    校验项：set 去重保序、id 属于 suite 且 active、is_held_out 与 trigger_type 匹配。
    任一无效 → 400（拒绝静默剔除，避免 run 实际执行数与请求子集不一致的困惑）。
    """
    if not case_ids:
        return None
    ids = list(dict.fromkeys(case_ids))  # 去重保序
    rows = (await db.execute(select(TestCase).where(
        TestCase.id.in_(ids), TestCase.suite_id == suite_id,
        TestCase.status == "active",
        TestCase.is_held_out == (trigger_type == "held_out")))).scalars().all()
    missing = set(ids) - {c.id for c in rows}
    if missing:
        raise ApiError(E_VALIDATION,
                       f"case_ids 含无效用例（不属于该 suite / 非 active / 留出集不匹配）: "
                       f"{sorted(missing)[:5]}", 400)
    return ids


async def _ensure_suite_has_cases(db: AsyncSession, suite_id: int, trigger_type: str,
                                  case_ids: list[int] | None) -> None:
    """V1 防 0-case 空转：全量触发（case_ids=None）校验 suite 有匹配的 active 用例。

    子集路径已由 _validate_case_ids 逐 id 校验（返回即全有效），此处仅兜全量。
    校验条件与 case_loader._load_run_cases 逐字同口径（status=active + is_held_out 匹配）。
    稳态防护：校验与执行窗口间 case 仍可能被置非 active（TOCTOU），与 case_loader 既有
    静默剔除语义一致——防的是「suite 本就无 active 用例」造出空转 run，非并发安全。
    """
    if case_ids is not None:
        return
    cnt = (await db.execute(select(func.count()).select_from(TestCase).where(
        TestCase.suite_id == suite_id,
        TestCase.status == "active",
        TestCase.is_held_out == (trigger_type == "held_out")))).scalar() or 0
    if cnt == 0:
        raise ApiError(E_VALIDATION,
                       "该 suite 无匹配的 active 用例（全量触发），请先创建/启用用例", 400)


async def _is_held_out_hidden(db: AsyncSession, run: EvalRun, user: User) -> bool:
    """7.8 前置⑤：留出集 owner 隐藏。owner 不可见 held-out run 的用例结果
    （deps.is_held_out_visible 同语义；与 annotations.py 对 held-out case 的处理一致）。"""
    agent = await db.get(Agent, run.agent_id)
    return not is_held_out_visible(run.trigger_type, user, agent.owner_id if agent else None)


# #12 进度估算默认值：scoring 阶段按 judge 配置快照的最坏时长估剩余（未配置用保守默认）
_JUDGE_EST_DEFAULTS = {"judge_call_timeout": 120, "judge_repeat": 3, "judge_concurrency": 1}


def _estimate_remaining_sec(run: EvalRun, done: int, judge: int, now) -> int | None:
    """#12 预计剩余秒数（None=无法估算）。

    running：已执行耗时按完成比例线性外推（同 agent 并发 N>1 时并行吞吐比串行快，
    该外推是上界）；scoring：judge 队列数 × 单 task 最坏时长（call_timeout×repeat）÷ 并发。
    """
    total = run.total_case or 0
    if run.status == "running" and total and done:
        elapsed = max(0.0, (now - run.started_at).total_seconds())
        return max(1, int(elapsed * (total - done) / done))
    if run.status == "scoring" and judge:
        cfg = {**_JUDGE_EST_DEFAULTS, **(run.run_config or {})}
        call_s = float(cfg.get("judge_call_timeout") or _JUDGE_EST_DEFAULTS["judge_call_timeout"])
        repeat = int(cfg.get("judge_repeat") or _JUDGE_EST_DEFAULTS["judge_repeat"])
        conc = int(cfg.get("judge_concurrency") or _JUDGE_EST_DEFAULTS["judge_concurrency"])
        return max(1, int(judge * call_s * repeat / max(conc, 1)))
    return None


def _result_stage(pass_fail: str, judge_status: str | None) -> str:
    """#12 单 case 状态流（纯数据推断，不加表字段）：error 优先，judge 未 done 归 judging。"""
    if pass_fail == "error":
        return "error"
    if judge_status in ("pending", "processing"):
        return "judging"
    if judge_status == "failed":
        return "judge_failed"
    return "completed"


def _case_stage(pass_fail: str, statuses: list[str] | None) -> str:
    """#12 单 case 状态流（多维度聚合）：error 优先；任一 judge 未 done → judging。

    同一 case 多语义维度（factuality+reasoning_quality）有多个 JudgeTask
    （PK=run_id+case_id+dimension_code），按 case 聚合状态列表而非 last-write-wins——
    任一维度仍在判分即 case 整体 judging，防一个维度 done 另一个 processing 被误标 completed。
    """
    if pass_fail == "error":
        return "error"
    statuses = statuses or []
    if any(s in ("pending", "processing") for s in statuses):
        return "judging"
    if any(s == "failed" for s in statuses):
        return "judge_failed"
    return "completed"


def _run_out(r: EvalRun, *, redact: bool = False, progress: dict | None = None) -> dict:
    """run 摘要。redact=True：裁剪结果型字段（P2-D8 owner 对 held_out run 隐藏聚合结果）。

    owner 不可见留出集复测的分数/通过数/延迟，但 run 记录本身保留——它占用 agent
    执行槽位（create_run 互斥 409），owner 需知情。状态型元数据不裁剪。
    progress（#12）：活跃 run 的 {done, judge, remaining}，终态 run 为 None。
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
        "case_ids": None if redact else r.case_ids,  # #3 子集（redact 裁剪留出集内容指纹）
        # #12 进度（仅活跃 run 提供；终态 run 三个字段全 None）
        "done_case": progress["done"] if progress else None,
        "judge_queue": progress["judge"] if progress else None,
        "estimate_remaining_sec": progress["remaining"] if progress else None,
        # P2-9 评测态追溯：知识版本（agent SSE meta 回填，库级文档时间戳锚；held_out 作为元数据保留）
        "knowledge_version": (r.env_snapshot or {}).get("knowledge_version"),
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
    # #3 定向重跑：case_ids 校验（None/空=全量；非空=子集）
    case_ids = await _validate_case_ids(db, body.suite_id, body.trigger_type, body.case_ids)
    # V1 防 0-case 空转：全量触发时校验 suite 有匹配的 active 用例（子集已有逐 id 校验）
    await _ensure_suite_has_cases(db, body.suite_id, body.trigger_type, case_ids)
    # 互斥：#4 并发上限 N（max_active_runs_per_agent，默认 1）。7.6 C1 多 worker 化：
    # 持 agent 行 FOR UPDATE 锁跨 worker 串行「查 active + 插 run + commit」，
    # 防止两 worker 同时过互斥检查各建一个 run（内存态互斥不跨进程）。
    # P2-D2 修复：活跃检查必须锁定读（with_for_update）——REPEATABLE READ 下本事务
    # 首个一致性读（上方 db.get(Agent)）已建立快照，普通读查 active 会读到旧快照
    # 漏掉并发 worker 刚提交的 run（实测双插）；锁定读永远读最新已提交数据。
    # #4：计数槽用 _MUTEX_STATUS（pending/running，scoring 不占执行槽）；count>=N → 409。
    async with agent_mutex(body.agent_id, db):
        n = await _max_active_runs(db)
        active = (await db.execute(select(EvalRun.id).with_for_update().where(
            EvalRun.agent_id == body.agent_id, EvalRun.status.in_(_MUTEX_STATUS)))).all()
        if len(active) >= n:
            raise ApiError(E_RUN_MUTEX,
                           f"该 agent 已有 {len(active)} 个执行中 run（上限 {n}），请等待完成或取消", 409)

        run = EvalRun(
            agent_id=body.agent_id, suite_id=body.suite_id, version=body.version,
            trigger_type=body.trigger_type, status="pending", generation=1,
            run_config=await _snapshot_run_config(db),
            case_ids=case_ids,
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
    # C6：limit 补下界（offset 已有 max(offset,0)；limit=-50 会生成 LIMIT -50，MySQL 行为不定）
    rows = (await db.execute(stmt.limit(max(0, min(limit, 200))).offset(max(offset, 0)))).scalars().all()
    # P2-D8：批量查 agent owner → owner 对 held_out run 裁剪聚合结果。列表不能逐条 404
    #（会破坏分页语义），run 记录保留、结果型字段置 None。
    owner_map = {}
    if rows:
        owner_map = {a.id: a.owner_id for a in (await db.execute(
            select(Agent.id, Agent.owner_id).where(
                Agent.id.in_({r.agent_id for r in rows})))).all()}
    # #12 进度：只对活跃 run 批量聚合已完成 case 数 + judge 队列（避免逐条 N+1）。
    # 已完成 case = eval_result 行数（每 case 执行完即落库）；judge 队列 = pending/processing 任务数。
    now = datetime.utcnow()
    active = [r for r in rows if r.status in ("pending", "running", "scoring")]
    done_map, judge_map = {}, {}
    if active:
        ids = [r.id for r in active]
        done_map = {rid: n for rid, n in (await db.execute(
            select(EvalResult.run_id, func.count()).where(
                EvalResult.run_id.in_(ids)).group_by(EvalResult.run_id))).all()}
        judge_map = {rid: n for rid, n in (await db.execute(
            select(JudgeTask.run_id, func.count()).where(
                JudgeTask.run_id.in_(ids),
                JudgeTask.status.in_(("pending", "processing"))).group_by(JudgeTask.run_id))).all()}
    out = []
    for r in rows:
        prog = None
        if r.status in ("pending", "running", "scoring"):
            d, j = done_map.get(r.id, 0), judge_map.get(r.id, 0)
            prog = {"done": d, "judge": j, "remaining": _estimate_remaining_sec(r, d, j, now)}
        out.append(_run_out(r, redact=not is_held_out_visible(r.trigger_type, user, owner_map.get(r.agent_id)),
                            progress=prog))
    return ok(out)


@router.get("/{run_id}")
async def get_run(run_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    run = await db.get(EvalRun, run_id)
    if run is None:
        raise ApiError(E_NOT_FOUND, "run 不存在", 404)
    # P2-D8：owner 对 held_out run 裁剪聚合结果（记录可见、结果不可见；run_results 同源判定）
    # #12 进度：详情页同源提供 done/judge/remaining（单 run 不批量）
    prog = None
    if run.status in ("pending", "running", "scoring"):
        done = (await db.execute(select(func.count()).select_from(EvalResult).where(
            EvalResult.run_id == run_id))).scalar() or 0
        judge = (await db.execute(select(func.count()).select_from(JudgeTask).where(
            JudgeTask.run_id == run_id,
            JudgeTask.status.in_(("pending", "processing"))))).scalar() or 0
        prog = {"done": done, "judge": judge,
                "remaining": _estimate_remaining_sec(run, done, judge, datetime.utcnow())}
    return ok(_run_out(run, redact=await _is_held_out_hidden(db, run, user), progress=prog))


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
                    db: AsyncSession = Depends(get_db),
                    body: RerunBody | None = None):
    src = await db.get(EvalRun, run_id)
    if src is None:
        raise ApiError(E_NOT_FOUND, "run 不存在", 404)
    # #3 定向重跑：None=继承源 run 子集（普通 run=全量，子集 run=同一子集）；非空=定向改批
    case_ids = src.case_ids if body is None or body.case_ids is None else (
        await _validate_case_ids(db, src.suite_id, src.trigger_type, body.case_ids))
    # V1 防 0-case 空转：源 run 全量（case_ids=None）且 suite 用例随后被禁用 → 重跑同样空转，
    # 与 create_run 同校验（子集继承已有 _validate_case_ids 保护）
    await _ensure_suite_has_cases(db, src.suite_id, src.trigger_type, case_ids)
    # #4 护栏：源 run 执行中（pending/running）不可并行重跑——N>1 放行计数时若源仍在跑，
    # 会「源执行中即全量再跑一遍」（前端 isActive 已门住，API 层兜底防直连绕过）。
    # scoring 源允许（不占执行槽，复用配置再跑合理）。
    if src.status in _MUTEX_STATUS:
        raise ApiError(E_VALIDATION, f"源 run 执行中（{src.status}），不可重跑", 400)
    # 7.6 C1 同 create_run：跨 worker 串行「查 active + 插 run + commit」
    # P2-D2：同 create_run，活跃检查锁定读，避免 REPEATABLE READ 旧快照漏看并发 run。
    # #4：计数槽 _MUTEX_STATUS，count>=N → 409（scoring 不占槽，判分期间可 rerun）。
    async with agent_mutex(src.agent_id, db):
        n = await _max_active_runs(db)
        active = (await db.execute(select(EvalRun.id).with_for_update().where(
            EvalRun.agent_id == src.agent_id, EvalRun.status.in_(_MUTEX_STATUS)))).all()
        if len(active) >= n:
            raise ApiError(E_RUN_MUTEX,
                           f"该 agent 已有 {len(active)} 个执行中 run（上限 {n}），请等待完成或取消", 409)
        run = EvalRun(
            agent_id=src.agent_id, suite_id=src.suite_id, version=src.version,
            trigger_type=src.trigger_type, status="pending", generation=1,
            run_config=src.run_config,  # 复用冻结配置
            case_ids=case_ids,  # #3：None=继承源子集，非空=定向改批
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
    """L3 用例明细：run 内全部用例结果（error/fail 靠前，其余按分数降序）。
    D3 内联：answer（截断 500）+ 失败断言（basic，含 viewer）；judge_results（reason 证据级）仅 staff。
    """
    logger.debug("run_results in: run_id=%s user=%s", run_id, user.username)
    run = await db.get(EvalRun, run_id)
    if run is None:
        raise ApiError(E_NOT_FOUND, "run 不存在", 404)
    # 7.8 前置⑤：留出集 owner 视为不存在（不泄露 held-out 结果）
    if await _is_held_out_hidden(db, run, user):
        raise ApiError(E_NOT_FOUND, "run 不存在", 404)
    # B2 列子集查询：answer 在 DB 层截断（func.left 多取 1 位，Python 侧 _truncate 补 "…"，
    # 与断言 actual 同口径），避免整列 MEDIUMTEXT 出库。
    rows = (await db.execute(
        select(EvalResult.id, EvalResult.case_id, EvalResult.pass_fail,
               EvalResult.score_total, EvalResult.score_per_dimension,
               EvalResult.error_type, EvalResult.error_detail,
               func.left(EvalResult.answer, _ANSWER_PREVIEW + 1).label("answer_preview"),
               EvalResult.assertion_results, EvalResult.judge_results, EvalResult.usage)
        .where(EvalResult.run_id == run_id))).all()
    if not rows:
        return ok([])
    # #12 单 case 状态流：批量取该 run 各 case 的 judge_task 状态（数据推断，无新字段）。
    # 聚合为 case→[status...]：多语义维度 case 有多个 JudgeTask，按 case 聚合避免 last-write-wins 丢维度
    judge_state: dict[int, list[str]] = {}
    for c, s in (await db.execute(
            select(JudgeTask.case_id, JudgeTask.status).where(JudgeTask.run_id == run_id))).all():
        judge_state.setdefault(c, []).append(s)
    cases = {c.id: c for c in (await db.execute(select(TestCase).where(
        TestCase.id.in_([r.case_id for r in rows])))).scalars().all()}
    ifaces = {i.id: i.name for i in (await db.execute(
        select(AgentInterface).where(AgentInterface.agent_id == run.agent_id))).scalars().all()}
    sees_full = sees_evidence_full(user)  # D3 full：judge_results（含 reason）仅 staff
    out = [{
        "id": r.id, "case_id": r.case_id,
        "case_name": cases[r.case_id].name if r.case_id in cases else "",
        "interface_name": ifaces.get(cases[r.case_id].interface_id, "")
        if r.case_id in cases else "",
        "input_type": cases[r.case_id].input_type if r.case_id in cases else "",
        "pass_fail": r.pass_fail,
        # #12 单 case 状态流：error / judging（judge 队列未 done）/ judge_failed / completed
        "stage": _case_stage(r.pass_fail, judge_state.get(r.case_id)),
        "score_total": float(r.score_total) if r.score_total is not None else None,
        "score_per_dimension": r.score_per_dimension or [],
        "error_type": r.error_type, "error_detail": r.error_detail,
        # D3 basic（所有登录用户，含 viewer）：answer 截断 + 失败断言明细（「为什么扣分」定位）
        "answer": _truncate(r.answer_preview),
        "assertion_results": [a for a in (r.assertion_results or []) if not a.get("pass")],
        # D3 full（staff）：judge reason 证据级，viewer 不返回（score 仍可从 score_per_dimension 见）
        "judge_results": ([{"dimension": j.get("dimension"), "score": j.get("score"),
                            "reason": j.get("reason")} for j in (r.judge_results or [])]
                          if sees_full else None),
        # P2-9 缓存命中标识：agent 应用层问答缓存（gq usage.cached=True；真实流式无该键恒 False）
        "cache_hit": any(u and u.get("cached") for u in (r.usage or [])),
    } for r in rows]
    out.sort(key=lambda x: (PF_ORDER.get(x["pass_fail"], 9),
                            -(x["score_total"] or -1)))
    logger.debug("run_results out: count=%s", len(out))
    return ok(out)


@router.get("/{run_id}/failures")
async def run_failures(run_id: int, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_db)):
    """L3.5 门禁失败摘要：fail 用例 + 不达标维度/断言/judge 失败 + top 扣分原因（viewer 可达）。
    断言明细 basic（viewer 可见，现状已暴露）；judge 失败理由与 top_reasons 属证据级（L4），
    仅 staff（D3：judge reason 归 full）。
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
        return ok({"items": [], "top_reasons": []})
    cases = {c.id: c for c in (await db.execute(select(TestCase).where(
        TestCase.id.in_([r.case_id for r in results])))).scalars().all()}
    ifaces = {i.id: i.name for i in (await db.execute(
        select(AgentInterface).where(AgentInterface.agent_id == run.agent_id))).scalars().all()}
    targets = await _load_agent_targets(db, run.agent_id)
    sees_full = sees_evidence_full(user)

    out = []
    top_agg = {}  # dimension_code -> bucket（#7 top 扣分原因聚合）
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
            if target is not None and not _gate_met(value, target, code):
                fail_dims.append({"code": code, "value": value, "target": target})
        fail_codes = {d["code"] for d in fail_dims}
        judge_failures = [j for j in (r.judge_results or [])
                          if j.get("dimension") in fail_codes]
        if sees_full:
            # #7 按 dimension 聚合（不按 reason 自由文本：LLM 措辞几乎不重复，精确分组必碎片化）
            for j in judge_failures:
                bucket = top_agg.setdefault(j.get("dimension"), {
                    "dimension": j.get("dimension"), "count": 0,
                    "sample_reasons": [], "avg_score": 0.0})
                bucket["count"] += 1
                reason = j.get("reason")
                if reason and reason not in bucket["sample_reasons"]:
                    bucket["sample_reasons"].append(reason)
                bucket["avg_score"] += float(j.get("score") or 0)
        out.append({
            "case_id": r.case_id,
            "case_name": case.name if case else "",
            "interface_name": ifaces.get(case.interface_id, "") if case else "",
            "score_total": float(r.score_total) if r.score_total is not None else None,
            "fail_dims": fail_dims,
            "assertion_failures": [a for a in (r.assertion_results or []) if not a.get("pass")],
            "judge_failures": judge_failures if sees_full else [],
        })
    top_reasons = []
    if sees_full:
        for b in top_agg.values():
            b["avg_score"] = round(b["avg_score"] / b["count"], 1)
            b["sample_reasons"] = b["sample_reasons"][:_TOP_REASONS_SAMPLE]
        top_reasons = sorted(top_agg.values(),
                             key=lambda b: (-b["count"], b["dimension"]))[:_TOP_REASONS_N]
    logger.debug("run_failures out: count=%s top=%s", len(out), len(top_reasons))
    return ok({"items": out, "top_reasons": top_reasons})


@router.get("/{run_id}/results/{result_id}/evidence")
async def result_evidence(run_id: int, result_id: int, user: User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)):
    """L4 证据：answer（截断）/ reasoning / judge 理由 / 断言详情 / 工具调用。
    D3 分级：viewer 仅 basic（answer 截断 500 + 断言明细）；full（reasoning/tool_calls/usage/
    timing/judge reason）仅 staff。原「viewer 403」放开为 basic 可见（前端证据按钮仍 isStaff 门住）。
    """
    logger.debug("result_evidence in: run_id=%s result_id=%s user=%s",
                 run_id, result_id, user.username)
    # 7.8 前置⑤：留出集 owner 视为不存在（先于结果校验，不泄露 held-out 存在性）
    run = await db.get(EvalRun, run_id)
    if run is not None and await _is_held_out_hidden(db, run, user):
        raise ApiError(E_NOT_FOUND, "结果不存在", 404)
    result = await db.get(EvalResult, result_id)
    if result is None or result.run_id != run_id:
        raise ApiError(E_NOT_FOUND, "结果不存在", 404)
    sees_full = sees_evidence_full(user)
    out = {
        "id": result.id, "case_id": result.case_id,
        # D3 basic：viewer 也可见 answer（截断 500）——与 run_results 内联同口径
        "answer": result.answer if sees_full else _truncate(result.answer),
        "reasoning": result.reasoning if sees_full else None,
        "tool_calls": result.tool_calls if sees_full else None,
        "usage": result.usage if sees_full else None,
        "timing": result.timing if sees_full else None,
        "assertion_results": result.assertion_results,  # basic：断言明细现状已对 viewer 暴露
        "judge_results": result.judge_results if sees_full else None,  # judge reason 证据级
        "error_type": result.error_type, "error_detail": result.error_detail,
    }
    logger.debug("result_evidence out: case_id=%s", out["case_id"])
    return ok(out)


async def _load_agent_targets(db: AsyncSession, agent_id: int) -> dict:
    """{(interface_id, dimension_code): target}，与 scorer._load_targets 同口径。"""
    rows = (await db.execute(select(BaselineTarget).where(
        BaselineTarget.agent_id == agent_id))).scalars().all()
    return {(r.interface_id, r.dimension_code): float(r.target_score) for r in rows}
