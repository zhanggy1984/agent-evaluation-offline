"""judge 批处理 worker（§15.4 #68：可恢复队列 + 幂等 key + scoring 态）。

- 抢占认领：pending（含到期 next_retry_at）→ processing + claim_id + lease_until
- 处理中超时回收：processing 且 lease_until 过期 → 回 pending（进程崩溃重启不丢）
- 失败重试：attempts 累计，≥ run_config.judge_max_retries → failed（评分侧该维度 N/A），
  否则 next_retry_at 指数退避回队列
- done 回填 result {dimension, level, score, reason, rubric_version}
- 批后对涉及的 run 查「全部任务终态（done/failed）」→ 触发 score_run（幂等：见非 scoring 返回）
- 单进程单 worker（workers=1 前提）；judge LLM 全挂只记录异常不死循环

参数来源：judge_llm.base_url/model_name + llm_allowlist + judge_concurrency 读 system_config
（is_hot 热生效）；judge_call_timeout/judge_max_retries 读各 run 的 run_config 快照。
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import or_, select

# 注意：SessionLocal / settings / models 在函数内延迟导入 —— 保持顶层零 DB 依赖，
# 宿主单测可直接 import（无需 aiomysql）。与 scorer.py 同模式。
from app.judge.aggregate import aggregate_verdicts
from app.judge.client import JudgeClient, JudgeError, JudgeVerdict, is_configured
from app.judge.rubric import BUILTIN_RUBRIC_VERSION, load_rubric
from app.runner.scorer import score_run

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 5        # 轮询间隔（秒）：任务入队后 ≤5s 开始判分
_DEFAULT_LEASE = 600      # 认领默认租约（秒）；P0-1 单 task 最坏 repeat×judge_call_timeout=360s，
                          # 默认值只兜崩溃回收，_process_one 内再按该 run 配置精确放大 lease_until
_MAX_BACKOFF = 30         # 重试退避上限（秒）：min(30, 2^attempts)
_RECLAIM_BATCH = 50       # 一轮回收 processing 超时任务上限


def _now() -> datetime:
    """naive UTC（与 orchestrator/scorer 同口径）。"""
    return datetime.utcnow()


def backoff_seconds(attempt: int, max_backoff: int = _MAX_BACKOFF) -> int:
    """指数退避：attempt=1 → 2s，2 → 4s...封顶 max_backoff。纯函数（单测）。"""
    return min(max_backoff, 2 ** max(attempt, 1))


def done_threshold_for(repeat: int) -> int:
    """多数决 done 阈值：成功采样 ≥ 该值才聚合判 done。纯函数（单测）。

    repeat>1 时必须 ≥2——多数决最少需 2 个成功样本才有「多数」可言；
    repeat=2 若退化为 1，1 成功 1 失败会静默单样本 done，对冲失效（评审 P1-1 补强）。
    repeat=1 显式单次判分（用户关闭对冲），阈值 1。
    """
    return max(2, (repeat + 1) // 2) if repeat > 1 else 1


async def _load_global_cfg(db) -> dict:
    """judge 全局配置（system_config，is_hot 热生效）。"""
    from app.models import SystemConfig
    keys = {"judge_llm.base_url", "judge_llm.model_name", "llm_allowlist", "judge_concurrency"}
    rows = (await db.execute(select(SystemConfig).where(SystemConfig.key.in_(keys)))).scalars().all()
    cfg = {r.key: r.value for r in rows}
    return {
        "base_url": (cfg.get("judge_llm.base_url") or "").strip(),
        "model": (cfg.get("judge_llm.model_name") or "").strip(),
        "allowlist": cfg.get("llm_allowlist") or [],
        "concurrency": max(int(cfg.get("judge_concurrency") or 4), 1),
    }


async def _reclaim_stale(db, now: datetime) -> int:
    """processing 租约过期 → 回 pending（worker 崩溃/重启后任务可被再次认领）。"""
    from app.models import JudgeTask
    rows = (await db.execute(select(JudgeTask).where(
        JudgeTask.status == "processing",
        JudgeTask.lease_until.isnot(None),
        JudgeTask.lease_until < now,
    ).limit(_RECLAIM_BATCH))).scalars().all()
    for t in rows:
        t.status = "pending"
        t.claim_id = None
        t.lease_until = None
    if rows:
        await db.commit()
        logger.info("回收 %d 个 processing 超时 judge 任务回 pending", len(rows))
    return len(rows)


async def _claim_batch(db, limit: int, now: datetime) -> list[JudgeTask]:
    """抢占认领一批 pending（含到期重试）任务。单 worker 无竞争，条件字段保留多实例语义。"""
    from app.models import JudgeTask
    candidates = (await db.execute(select(JudgeTask).where(
        JudgeTask.status == "pending",
        or_(JudgeTask.next_retry_at.is_(None), JudgeTask.next_retry_at <= now),
    ).order_by(JudgeTask.updated_at).limit(limit))).scalars().all()
    for t in candidates:
        t.status = "processing"
        t.claim_id = uuid.uuid4().hex
        t.lease_until = now + timedelta(seconds=_DEFAULT_LEASE)
    if candidates:
        await db.commit()
    return candidates


async def _run_judge_done(db, run_id: int) -> bool:
    """该 run 是否已无 pending/processing 任务（全部终态可评分）。无任务视为 False（不触发）。"""
    from app.models import JudgeTask
    statuses = (await db.execute(select(JudgeTask.status).where(
        JudgeTask.run_id == run_id))).scalars().all()
    if not statuses:
        return False
    return all(s in ("done", "failed") for s in statuses)


async def _rubric_version(db, dimension_code: str, interface_id: int) -> str:
    from app.models import JudgeRubric
    row = (await db.execute(select(JudgeRubric.version).where(
        JudgeRubric.dimension_code == dimension_code,
        JudgeRubric.interface_id.in_((0, interface_id)),
    ).order_by(JudgeRubric.interface_id.desc(), JudgeRubric.id.desc()).limit(1))).first()
    return row[0] if row else BUILTIN_RUBRIC_VERSION


async def _process_one(t: JudgeTask, run_cfg: dict, interface_id: int, client: JudgeClient) -> None:
    """判分单个任务：同一 (case, 维度) 判 repeat 次 → 档位多数决聚合（P0-1）。

    - repeat = run_cfg.judge_repeat（默认 3）；done 阈值 = done_threshold_for(repeat)
      （repeat>1 时 ≥2，多数决最少需 2 个成功样本）——成功采样不足 → 整 task 重采样重试，
      防止 judge 失败率高（噪声大）时退化为单样本对冲失效
    - 单次判分失败不中止循环（partial 也聚合）；成功采样 < 阈值 → 整 task 重采样重试
    - result 顶层保持 dimension/level/score/reason/rubric_version（scorer 零改动兼容），
      新增 repeat（实际采样数，evidence 可识别退化多数决）+ repeats（各次明细）
    """
    from app.core.db import SessionLocal
    from app.models import CaseVersion, EvalResult, JudgeTask
    max_retries = int(run_cfg.get("judge_max_retries", 2))
    repeat = max(1, int(run_cfg.get("judge_repeat", 3)))
    done_threshold = done_threshold_for(repeat)
    # 租约覆盖单 task 最坏时长（repeat × judge_call_timeout），崩溃回收不丢已做样本
    call_timeout = max(float(run_cfg.get("judge_call_timeout", 120)), 1)
    async with SessionLocal() as db:
        try:
            # t 来自认领 session（detached），须 add 回当前 session 修改才能持久化
            db.add(t)
            t.lease_until = _now() + timedelta(seconds=repeat * call_timeout + 60)
            result = (await db.execute(select(EvalResult).where(
                EvalResult.run_id == t.run_id,
                EvalResult.case_id == t.case_id))).scalars().first()
            if result is None or result.pass_fail == "error":
                # 结果缺失/技术失败：判分无意义，直接 failed（评分侧该维度 N/A）
                logger.warning("run %s case %s %s 结果不可判，标 failed",
                               t.run_id, t.case_id, t.dimension_code)
                t.status = "failed"
                t.claim_id = None
                t.lease_until = None
                await db.commit()
                return
            cv = await db.get(CaseVersion, result.case_version_id)
            snapshot = cv.snapshot if cv else {}
            template = await load_rubric(db, t.dimension_code, interface_id)
            version = await _rubric_version(db, t.dimension_code, interface_id)
            verdicts: list[JudgeVerdict] = []
            last_error: Exception | None = None
            for i in range(repeat):
                try:
                    verdict = await client.judge(
                        dimension=t.dimension_code,
                        template=template,
                        case_input=snapshot.get("input"),
                        golden_answer=(snapshot.get("expected") or {}).get("golden_answer"),
                        reference_docs=(snapshot.get("expected") or {}).get("reference_docs"),
                        agent_output=result.answer,
                        # P2-A3：reasoning 维度判分证据透传。MEDIUMTEXT(16MB) + repeat 翻倍
                        # （最多 repeat×2 维次全量发送）会撑爆输入上下文 → 统一 [:4000] 截断；
                        # answer 未截断是既有状态，本次不动（超待办范围）
                        agent_reasoning=(result.reasoning or "")[:4000],
                        agent_tool_calls=(json.dumps(result.tool_calls, ensure_ascii=False)[:4000]
                                          if result.tool_calls else None),
                        rubric_version=version,
                    )
                    verdicts.append(verdict)
                except JudgeError as e:
                    # C4：内层只收单次判分失败。client.judge 已把 httpx 超时/连接错等全部异常
                    # 包成 JudgeError（client.py:200-201），无裸异常逃逸——aggregate_verdicts/DB 等
                    # 非判分错误不再被当作单次失败静默吞掉，真实 bug 走外层完整栈。
                    last_error = e
                    logger.warning("run %s case %s %s 第 %d/%d 次判分失败: %s",
                                   t.run_id, t.case_id, t.dimension_code, i + 1, repeat, e)
            if len(verdicts) >= done_threshold:
                agg = aggregate_verdicts(verdicts)
                t.status = "done"
                t.result = agg
                t.claim_id = None
                t.lease_until = None
                await db.commit()
                logger.info("run %s case %s %s done score=%s (repeat=%d/%d)",
                            t.run_id, t.case_id, t.dimension_code, agg["score"],
                            len(verdicts), repeat)
                return
            # 成功采样 < done 阈值（含全失败）：整 task 重采样重试，走现有 attempts/退避
            raise JudgeError(f"成功采样 {len(verdicts)}/{repeat} < done 阈值 {done_threshold}"
                             + (f"；最后错误: {last_error}" if last_error else ""))
        except (JudgeError, Exception) as e:
            # C4：JudgeError（判分失败）与非 JudgeError 真实 bug（aggregate_verdicts/DB 等）统一
            # 按 attempts 重试/标 failed——logger.exception 保证后者完整栈可见，不再被静默掩盖。
            # done/failed 均属终态，不阻塞 run 收尾。
            t.attempts = (t.attempts or 0) + 1
            t.claim_id = None
            t.lease_until = None
            if t.attempts >= max_retries:
                t.status = "failed"
                await db.commit()
                logger.exception("run %s case %s %s judge 重试 %d 次仍失败 → failed: %s",
                                 t.run_id, t.case_id, t.dimension_code, t.attempts, e)
            else:
                t.status = "pending"
                t.next_retry_at = _now() + timedelta(seconds=backoff_seconds(t.attempts))
                await db.commit()
                logger.warning("run %s case %s %s judge 失败（attempts=%d）: %s",
                               t.run_id, t.case_id, t.dimension_code, t.attempts, e)


async def _drain_once() -> None:
    """一轮：回收 → 认领 → 判分 → 触发就绪 run 的评分。judge 未配置则空转。"""
    from app.core.config import settings
    from app.core.db import SessionLocal
    async with SessionLocal() as db:
        cfg = await _load_global_cfg(db)
        if not is_configured(settings.judge_api_key, cfg["base_url"], cfg["model"]):
            return
        now = _now()
        await _reclaim_stale(db, now)
        tasks = await _claim_batch(db, cfg["concurrency"], now)
    if not tasks:
        return

    # 涉及 run 的 run_config 快照 + 用例 interface_id（rubric 按接口覆盖）
    run_ids = {t.run_id for t in tasks}
    case_ids = {t.case_id for t in tasks}
    from app.models import EvalRun, TestCase
    run_cfgs: dict[int, dict] = {}
    case_iface: dict[int, int] = {}
    async with SessionLocal() as db:
        runs = (await db.execute(select(EvalRun).where(EvalRun.id.in_(run_ids)))).scalars().all()
        run_cfgs = {r.id: (r.run_config or {}) for r in runs}
        ifaces = (await db.execute(select(TestCase.id, TestCase.interface_id).where(
            TestCase.id.in_(case_ids)))).all()
        case_iface = {row[0]: row[1] for row in ifaces}

    timeout = max((run_cfgs.get(rid, {}).get("judge_call_timeout", 120) for rid in run_ids), default=120)
    client = JudgeClient(base_url=cfg["base_url"], model=cfg["model"],
                         api_key=settings.judge_api_key, allowlist=cfg["allowlist"],
                         timeout=timeout)
    sem = asyncio.Semaphore(cfg["concurrency"])
    async def _one(t: JudgeTask) -> None:
        async with sem:
            await _process_one(t, run_cfgs.get(t.run_id, {}), case_iface.get(t.case_id, 0), client)
    await asyncio.gather(*[_one(t) for t in tasks])

    # 判分就绪的 run → 触发最终评分（score_run 幂等：非 scoring 状态直接返回）
    for rid in run_ids:
        async with SessionLocal() as db:
            ready = await _run_judge_done(db, rid)
        if ready:
            await score_run(rid)


async def judge_worker_loop() -> None:
    """后台循环（main startup 时 create_task）。异常不退出循环。"""
    logger.info("judge worker 启动（interval=%ss）", _POLL_INTERVAL)
    while True:
        try:
            await _drain_once()
        except Exception:
            logger.exception("judge worker 一轮异常")
        await asyncio.sleep(_POLL_INTERVAL)
