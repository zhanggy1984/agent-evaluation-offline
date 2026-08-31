"""run 生命周期 orchestrator（§15.1 状态机 / §15.4 熔断限流 / §8 并发重试）。

- 互斥：同一 agent 同时只允许一个 in_progress run（§15.4）
- 并发：跨 agent 并发 + 同 agent 低并发（KeyedLimiter per-run 桶，先 per-agent 后全局）
- 熔断：agent 级 CircuitBreaker；熔断期用例直接标 error（circuit_open）
- 重试：指数退避，仅可重试技术失败
- 心跳：run 内后台 task 周期刷新 lease_until（防 scanner 误标 timeout）
- generation fencing：cancel 自增令牌，执行循环轮询中断
- 性能重复测量：用例启用 ttft/e2e 维度时按 perf_repeat_count 重复执行，聚合 P50/P95
- 收尾：对账 total_case vs eval_result，缺失回填 error；更新 run 统计与状态
评分（score_total/score_per_dimension）由阶段三 scorer 接管，本模块只采数与落原始结果。
"""
import asyncio
import hashlib
import logging
import math
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.adapters.engine import ConfigEngine
from app.core import circuit_repo
from app.core.circuit_breaker import CircuitBreaker
from app.core.db import SessionLocal
from app.core.http import build_agent_client
from app.core.limiter import KeyedLimiter
from app.core.lock import agent_mutex
from app.core.probe import probe_interface
from app.core.retry import retry_with_backoff
from app.core.security import fernet_decrypt
from app.models import Agent, AgentInterface, CaseVersion, EvalResult, EvalRun, TestSuite
from app.runner.case_loader import _load_run_cases
from app.runner.executor import RETRYABLE_ERRORS, CaseOutcome, execute_case
from app.runner.scorer import _enabled_semantic_dims, score_run

logger = logging.getLogger(__name__)

ERROR_CIRCUIT_OPEN = "circuit_open"  # 熔断期未实际调用

RUNNING = "running"
SCORING = "scoring"
COMPLETED = "completed"
PARTIAL_FAILED = "partial_failed"
CANCELLED = "cancelled"


def _now() -> datetime:
    """naive UTC（MySQL DATETIME 无时区；aware 直存会被 pymysql 带 offset 破坏比较）。"""
    return datetime.utcnow()


def _pct(values: list[float], p: float) -> float:
    """p 分位数（0-100），nearest-rank 口径（位置=ceil(n·p/100)，1-based）；最小数据集 len>=1。"""
    if not values:
        return 0.0
    s = sorted(values)
    idx = max(0, min(len(s) - 1, math.ceil(len(s) * p / 100) - 1))
    return s[idx]


RUN_TIMEOUT_CAP_S = 7200  # 估算值封顶 120min（solution_detail.md:879）


def estimate_run_timeout(*, n_cases: int, active_runs: int, repeat: int,
                         max_iface_timeout: int, max_retries: int,
                         semantic_tasks: int, judge_repeat: int,
                         judge_concurrency: int, judge_call_timeout: int) -> int:
    """run_timeout 估算公式（solution_detail.md:879）：执行窗口 + judge 窗口，封顶 120min。

    - 执行窗口：并发折算（在途 run 越多，单 run 分到的执行时间越长）下的逐 case
      串行上限 × repeat × 单次最长超时 × (max_retries+1) × 1.5 缓冲
    - judge 窗口：语义维度任务数 × judge_repeat ÷ judge_concurrency × 单次调用超时
    返回 int 秒（封顶 RUN_TIMEOUT_CAP_S）。纯函数，便于单测。
    """
    eff_cases = math.ceil(n_cases / max(1, active_runs + 1))
    exec_s = eff_cases * repeat * max_iface_timeout * (max_retries + 1) * 1.5
    judge_s = math.ceil(semantic_tasks * judge_repeat / max(1, judge_concurrency)) * judge_call_timeout
    return min(RUN_TIMEOUT_CAP_S, math.ceil(exec_s + judge_s))


def _content_hash(snapshot: dict) -> str:
    return hashlib.sha256(
        repr(sorted(snapshot.items(), key=lambda kv: kv[0])).encode("utf-8")
    ).hexdigest()


class RunOrchestrator:
    """单进程全局实例（workers=1 前提成立）。"""

    def __init__(self) -> None:
        # 7.6 C3 熔断器 DB 化：不再持有进程内 agent 级 dict（多 worker 各自内存不共享），
        # 状态经 agent_circuit 表 load/save（见 _run_one）
        self._limiter = KeyedLimiter()
        self._cancel: dict[int, int] = {}   # run_id -> generation
        self._heartbeats: dict[int, asyncio.Task] = {}
        self._run_limits: dict[int, tuple[int, int]] = {}  # run_id -> (global, per_agent)

    # ---------------- 熔断 / 限流 / 取消 ----------------
    def set_run_limits(self, run_id: int, global_limit: int, per_agent_limit: int) -> None:
        """为 run 建/更新 per-run 限流桶（7.8 前置④）。run 终态 drop_run_limits 清理。"""
        self._limiter.set_run(run_id, global_limit, per_agent_limit)
        self._run_limits[run_id] = (global_limit, per_agent_limit)

    def drop_run_limits(self, run_id: int) -> None:
        self._limiter.drop_run(run_id)
        self._run_limits.pop(run_id, None)

    def cancel_run(self, run_id: int) -> None:
        """置取消标志。单进程部署：新 run start 时清标志，天然 fencing。"""
        self._cancel[run_id] = True

    def _is_cancelled(self, run_id: int) -> bool:
        return self._cancel.get(run_id, False)

    # ---------------- run 主流程 ----------------
    async def start_run(self, run_id: int) -> None:
        """入口：编排一个 run。异常由调用方记录（run 已标 running）。"""
        try:
            await self._run(run_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("run %s 编排异常", run_id)
            await self._fail_run(run_id, "orchestrator_error")

    async def _run(self, run_id: int) -> None:
        # 加载 run/agent/cases 并做前置校验 + 状态初始化（同一 session 内提交）
        async with SessionLocal() as db:
            run = await db.get(EvalRun, run_id)
            if run is None:
                logger.error("run %s 不存在", run_id)
                return
            agent = await db.get(Agent, run.agent_id)
            suite = await db.get(TestSuite, run.suite_id)
            # 6.4b 留出集 + #3 定向重跑：case 过滤单一来源
            # （执行/probe/对账/salvage 四处同源，过滤规则变更只改 _load_run_cases 一处）
            cases = await _load_run_cases(db, run)
            run_config = run.run_config or {}
            # 冻结 scope=run 的并发/超时/重复数配置（run_config 由创建接口快照）
            global_limit = run_config.get("global_max_inflight", 16)
            per_agent = run_config.get("per_agent_concurrency", 3)
            timeout_s = run_config.get("case_timeout", 120)
            repeat = run_config.get("perf_repeat_count", 5)
            max_retries = run_config.get("max_retries", 1)
            breaker_th = run_config.get("breaker_failure_threshold", 5)
            breaker_open = run_config.get("breaker_open_duration", 30)
            hb_interval = run_config.get("heartbeat_interval", 30)
            lease_sec = run_config.get("lease_seconds", 90)
            # run_timeout：显式配置优先（seed 默认 None），None → 按公式估算
            # （solution_detail.md:879；7.4 前读 hard_deadline_s 恒 3600，公式从未生效）
            configured_timeout = run_config.get("run_timeout")
            if configured_timeout is not None:
                hard_deadline_s = int(configured_timeout)
            else:
                # 在途 run（不含本 run，此刻仍 pending，双保险排除）——公式并发折算分母
                active_runs = len((await db.execute(select(EvalRun.id).where(
                    EvalRun.status.in_((RUNNING, SCORING)),
                    EvalRun.id != run_id))).scalars().all())
                # 语义维度任务数 = Σ per-case enabled 语义维度（scorer 口径，判分需 judge）
                semantic_tasks = sum(len(_enabled_semantic_dims(case.metrics)) for case in cases)
                max_iface_timeout = int((agent.adapter_config or {}).get("timeout") or timeout_s)
                hard_deadline_s = estimate_run_timeout(
                    n_cases=len(cases), active_runs=active_runs, repeat=repeat,
                    max_iface_timeout=max_iface_timeout, max_retries=max_retries,
                    semantic_tasks=semantic_tasks,
                    judge_repeat=int(run_config.get("judge_repeat", 3)),
                    judge_concurrency=int(run_config.get("judge_concurrency", 4)),
                    judge_call_timeout=int(run_config.get("judge_call_timeout", 120)))

            if agent is None or not agent.enabled:
                run.status = PARTIAL_FAILED
                run.finished_at = _now()
                await db.commit()
                logger.error("run %s agent 不存在或已禁用", run_id)
                return
            if not cases:
                run.status = COMPLETED
                run.finished_at = _now()
                run.total_case = 0
                await db.commit()
                logger.warning("run %s suite 无 active 用例", run_id)
                return

            # 状态初始化：running + 心跳租约 + 硬超时 + 用例总数（scanner 靠 lease_until 判断存活）
            run.status = RUNNING
            run.started_at = _now()
            run.total_case = len(cases)
            run.lease_until = _now() + timedelta(seconds=lease_sec)
            run.hard_deadline = _now() + timedelta(seconds=hard_deadline_s)
            run.env_snapshot = {
                "contract_version": agent.contract_version,
                "adapter_config_hash": _content_hash(agent.adapter_config or {}),
            }
            await db.commit()

        # 7.6 C3 熔断参数按 run 级配置传入 _run_one 构造（DB 化后无进程内单例可设置）
        self.set_run_limits(run_id, global_limit, per_agent)  # 应用 run 级并发参数
        generation = run.generation
        self._cancel.pop(run_id, None)

        # 心跳 task：刷新 lease_until
        hb = asyncio.create_task(self._heartbeat(run_id, hb_interval, lease_sec))
        self._heartbeats[run_id] = hb

        # 执行（并发由 limiter 控制，不额外起信号量）
        secret = self._decrypt_auth(agent)
        client = build_agent_client()
        logger.info("run %s 开始执行 %d 个 case", run_id, len(cases))
        try:
            async with client:
                # B.5 跑前探测（决策 #21）：契约不达标直接拦截，不执行任何用例
                if not await self._probe_before_run(run_id, agent, run.suite_id, secret, client,
                                                    timeout_s):
                    self._heartbeats.pop(run_id, None)
                    return
                tasks = [self._run_one(run_id, run, agent, case, secret, client, timeout_s,
                                       repeat, max_retries, generation, breaker_th, breaker_open)
                         for case in cases]
                await asyncio.gather(*tasks)
        finally:
            hb.cancel()
        logger.info("run %s 所有 case 执行完成，进入收尾", run_id)

        await self._finish(run_id)

    async def _run_one(self, run_id, run, agent, case, secret, client, timeout_s,
                       repeat, max_retries, generation, breaker_th, breaker_open) -> None:
        """单用例（含熔断/限流/重试/性能重复测量），结果落库。"""
        # 取消检查
        if self._is_cancelled(run_id):
            return
        await self._limiter.acquire(run_id, str(agent.id))
        try:
            # 7.6 C3 熔断 DB 化：每次 case 读-改-写（load 无行自动建默认行；
            # threshold/open_duration 按 run 配置构造实例，run 内一致）。
            # save 在 finally 兜底——try_acquire 拒绝/异常路径也会落库当前状态。
            async with SessionLocal() as db:
                breaker = await circuit_repo.load(db, agent.id,
                                                  CircuitBreaker(breaker_th, breaker_open))
                try:
                    if not breaker.try_acquire():
                        await self._save_result(run_id, case, None, None,
                                                error_type=ERROR_CIRCUIT_OPEN, error_detail="熔断中")
                        return
                    outcome = None
                    try:
                        outcome = await self._execute_with_retry(run_id, run, agent, case, secret,
                                                                 client, timeout_s, repeat,
                                                                 max_retries)
                    finally:
                        breaker.release_probe()
                    # 熔断反馈：成功→清计数；可重试技术失败→累计（超时/5xx/连接失败才熔断，
                    # 契约类错误是 agent bug，不熔断以免修契约前全被打死）
                    if outcome is not None:
                        if outcome.ok:
                            breaker.record_success()
                        elif outcome.error_type in RETRYABLE_ERRORS:
                            breaker.record_failure()
                finally:
                    await circuit_repo.save(db, agent.id, breaker)
        finally:
            self._limiter.release(run_id, str(agent.id))

    async def _probe_before_run(self, run_id: int, agent: Agent, suite_id: int, secret: dict,
                                client, timeout_s: float) -> bool:
        """跑前探测 run 涉及的 interface（B.5 决策 #21）。

        输入 = suite 第一个 active case；任一接口不达标 → run 标 partial_failed +
        fail_reason=probe_failed + probe_details，返回 False（调用方中止执行，不产生用例结果）。
        """
        async with SessionLocal() as db:
            run = await db.get(EvalRun, run_id)
            if run is None:
                return False  # run 已不存在，中止探测（原逻辑 run None 兜底 held_out=False，路径实际不可达）
            cases = await _load_run_cases(db, run)
        if not cases:
            return True  # 无 active case 在前面已被拦截，双保险
        probe_case = cases[0]
        iface_ids = {c.interface_id for c in cases}
        details: list[dict] = []
        async with SessionLocal() as db:
            ifaces = (await db.execute(select(AgentInterface).where(
                AgentInterface.id.in_(iface_ids), AgentInterface.enabled == True))).scalars().all()
            for iface in ifaces:
                adapter = ConfigEngine(agent, iface, agent.adapter_config or {}, secret)
                pr = await probe_interface(adapter, client, probe_case, timeout_s)
                details.append(asdict(pr))
        if all(d.get("ok") for d in details):
            logger.info("run %s 跑前探测通过（%d 接口）", run_id, len(ifaces))
            return True
        async with SessionLocal() as db:
            r = await db.get(EvalRun, run_id)
            if r is not None:
                r.status = PARTIAL_FAILED
                r.finished_at = _now()
                r.total_case = 0  # 探测拦截：实际 0 条用例执行（消除计划数残留误导）
                rc = dict(r.run_config or {})
                rc["fail_reason"] = "probe_failed"
                rc["probe_details"] = details
                r.run_config = rc
                await db.commit()
        logger.error("run %s 跑前探测不达标: %s", run_id,
                     [{"interface_id": d["interface_id"], "errors": d.get("errors")} for d in details])
        return False

    async def _execute_with_retry(self, run_id, run, agent, case, secret, client,
                                  timeout_s, repeat, max_retries) -> CaseOutcome | None:
        """执行用例并落库，返回最终 outcome（供熔断反馈）。None=未执行（接口停用/取消）。"""
        logger.info("run %s case %s 开始执行", run_id, case.id)
        interface = await self._get_interface(case.interface_id)
        if interface is None or not interface.enabled:
            await self._save_result(run_id, case, None, None,
                                    error_type="interface_disabled", error_detail="接口已停用")
            return None
        adapter = ConfigEngine(agent, interface, agent.adapter_config or {}, secret)
        # 7.6 B2：adapter_config.reset 段存在时，每次采样前先 reset(seed) 拿真实 data_id
        reset_cfg = (agent.adapter_config or {}).get("reset")
        metrics = case.metrics or {}
        # 7.5b 副作用接口只执行一次：retryable=False 不重试、不做性能重复采样（配置化接口标记）
        retryable = bool(getattr(interface, "retryable", True))
        perf_needed = (metrics.get("ttft", {}).get("enabled") or metrics.get("e2e", {}).get("enabled"))
        attempts = repeat if (perf_needed and retryable) else 1

        usages, timings, statuses, firsts, ends, data_ids = [], [], [], [], [], []
        last_outcome: CaseOutcome | None = None
        for i in range(attempts):
            if self._is_cancelled(run_id):
                return last_outcome
            # 7.6 B2/B3：reset(seed) 每次采样前执行，per-agent 行锁串行（execute 才并发）。
            # reset 失败 raise → 上抛为 run 级 orchestrator_error（前置条件不成立，不重试语义）。
            if reset_cfg is not None:
                async with agent_mutex(agent.id):
                    data_ids.append(await adapter.reset(case, client))
            last_outcome = await self._call_once(adapter, client, case, timeout_s, max_retries,
                                                 retryable=retryable)
            if last_outcome.ok:
                usages.append(last_outcome.unified.get("usage"))
                timings.append(last_outcome.timing)
                if last_outcome.status_code is not None:
                    statuses.append(last_outcome.status_code)
                if last_outcome.timing.get("first_token_ts") is not None:
                    firsts.append(last_outcome.timing["first_token_ts"])
                if last_outcome.timing.get("end_ts") is not None:
                    ends.append(last_outcome.timing["end_ts"])
            else:
                # 技术失败：不计入性能聚合，直接收尾
                await self._save_result(run_id, case, last_outcome,
                                        data_ids[-1] if data_ids else None)
                return last_outcome
        await self._save_result(
            run_id, case, last_outcome, data_ids[-1] if data_ids else None,
            usages=usages, timings=timings, firsts=firsts, ends=ends,
        )
        return last_outcome

    async def _call_once(self, adapter, client, case, timeout_s, max_retries,
                         *, retryable: bool = True) -> CaseOutcome:
        """指数退避重试包装。仅在可重试错误上重试（不可重试/成功/副作用接口首败即返）。"""
        last: CaseOutcome | None = None
        for i in range(max_retries + 1):
            outcome = await execute_case(adapter, client, case, timeout_s)
            last = outcome
            if outcome.ok or outcome.error_type not in RETRYABLE_ERRORS or not retryable:
                return outcome
            if i < max_retries:
                await asyncio.sleep(min(10.0, 0.5 * (2 ** i)))  # 指数退避 base=0.5s
        return last

    # ---------------- 持久化 ----------------
    async def _save_result(self, run_id, case, outcome: CaseOutcome | None,
                           data_id=None, *, usages=None, timings=None,
                           firsts=None, ends=None, error_type=None, error_detail=None) -> None:
        """落 eval_result（usage/timing 存全 attempt 数组，看板只读预聚合列）。"""
        case_version_id = await self._ensure_case_version(case)
        async with SessionLocal() as db:
            existing = (await db.execute(select(EvalResult).where(
                EvalResult.run_id == run_id, EvalResult.case_id == case.id))).first()
            if existing:
                return  # 幂等（对账回填前不重复写）
            meta = (outcome.unified.get("meta") if outcome else {}) or {}
            r = EvalResult(
                run_id=run_id, case_id=case.id, case_version_id=case_version_id,
                data_id=data_id,  # 7.6 B4：reset(seed) 产物追溯（无 reset 段为 None）
                model=meta.get("model"),
                pass_fail="error",
                answer=(outcome.unified.get("answer") if outcome else None),
                reasoning=(outcome.unified.get("reasoning") if outcome else None),
                tool_calls=(outcome.unified.get("tool_calls") if outcome else None),
                usage=usages or ([outcome.unified.get("usage")] if outcome and outcome.ok else None),
                timing=timings or ([outcome.timing] if outcome else None),
                error_type=error_type or (outcome.error_type if outcome else "unknown"),
                error_detail=error_detail or (outcome.error_detail if outcome else None),
                finished_at=_now(),
            )
            if outcome is not None and outcome.ok:
                r.pass_fail = "pass"  # 暂标 pass（成功执行）；评分阶段（scorer）再修正为 fail/na
                if firsts:
                    r.ttft_p50, r.ttft_p95 = _pct(firsts, 50), _pct(firsts, 95)
                if ends:
                    r.e2e_p50, r.e2e_p95 = _pct(ends, 50), _pct(ends, 95)
                r.total_tokens = sum(u.get("total_tokens") or 0 for u in (usages or []) if u)
            db.add(r)
            await db.commit()
            logger.info("run %s case %s 落库 pass=%s err=%s", run_id, case.id, r.pass_fail, r.error_type)

    async def _ensure_case_version(self, case) -> int:
        """取该 case 最新版本；内容已变更则建新版本（(case_id, version_no) 唯一约束递增）。

        7.3 修复：原实现无条件复用最新版本——case 数据变更（如 metrics 开启语义判分）后
        scorer 仍读旧快照（cv.snapshot 的 metrics）判分/评分，语义判分不生效。
        内容哈希不同即建 version_no+1；内容不变仍复用（兼容既有幂等）。
        """
        async with SessionLocal() as db:
            ver = (await db.execute(select(CaseVersion).where(
                CaseVersion.case_id == case.id).order_by(CaseVersion.version_no.desc()))).first()
            snapshot = {
                "input": case.input, "input_turns": case.input_turns, "file_ref": case.file_ref,
                "expected": case.expected, "assertions": case.assertions, "metrics": case.metrics,
            }
            h = _content_hash(snapshot)
            if ver is not None:
                if ver[0].content_hash == h:
                    return ver[0].id
                v = CaseVersion(case_id=case.id, version_no=ver[0].version_no + 1,
                                content_hash=h, snapshot=snapshot)
                db.add(v)
                await db.commit()
                return v.id
            v = CaseVersion(case_id=case.id, version_no=1, content_hash=h, snapshot=snapshot)
            db.add(v)
            await db.commit()
            return v.id

    async def _get_interface(self, interface_id: int) -> AgentInterface | None:
        async with SessionLocal() as db:
            return await db.get(AgentInterface, interface_id)

    def _decrypt_auth(self, agent: Agent) -> dict:
        if not agent.auth_config:
            return {}
        try:
            # auth_config 是 VARBINARY → pymysql 读回为 bytes；str 分支兜底（兼容不同驱动）
            cfg = agent.auth_config
            if isinstance(cfg, str):
                cfg = cfg.encode("ascii")
            raw = fernet_decrypt(cfg)
            import json
            return json.loads(raw)
        except Exception:
            logger.warning("agent %s auth_config 解密失败", agent.id)
            return {}

    # ---------------- 心跳 ----------------
    async def _heartbeat(self, run_id: int, interval: int, lease_sec: int) -> None:
        try:
            while True:
                await asyncio.sleep(interval)
                # 续租失败不退出：DB 抖动时保持心跳（退出会让 scanner 误回收活 run）
                try:
                    async with SessionLocal() as db:
                        run = await db.get(EvalRun, run_id)
                        if run is None or run.status != "running":
                            return
                        run.lease_until = _now() + timedelta(seconds=lease_sec)
                        await db.commit()
                        logger.debug("run %s 心跳续租 lease_until=%s", run_id, run.lease_until)
                except Exception:
                    logger.exception("run %s 心跳续租失败（下次重试）", run_id)
        except asyncio.CancelledError:
            pass

    # ---------------- 收尾 ----------------
    async def _finish(self, run_id: int) -> None:
        """对账 + 统计 + 状态。scoring 阶段三接入（先标 completed/partial_failed）。

        外部已置终态（scanner 标 timeout / API 标 cancelled）时不再覆盖，只对账统计。
        """
        self.drop_run_limits(run_id)  # 7.8 前置④：case 全部归还信号量，清理 per-run 桶
        async with SessionLocal() as db:
            # P2-D6：锁定读保证读到最新已提交状态。REPEATABLE READ 普通读受事务快照影响，
            # 会读到 scanner 标 timeout 前的旧值（external_terminal=False），把 scanner 的
            # timeout 覆盖成 scoring/completed。锁定读永远读最新：scanner 已 commit timeout
            # 则不覆盖只对账；本事务持 run 行锁期间 scanner 的 UPDATE 阻塞，commit 后
            # scanner 条件更新（status IN pending/running）失败自动跳过，形成互不覆盖闭环。
            run = await db.get(EvalRun, run_id, with_for_update=True)
            if run is None:
                return
            logger.info("run %s _finish 进入（status=%s）", run_id, run.status)
            external_terminal = run.status in ("timeout", "cancelled")
            results = (await db.execute(select(EvalResult).where(EvalResult.run_id == run_id))).scalars().all()
            total = len(results)
            # 对账：total_case 与结果条数差 → 缺失回填 error（cancel 或熔断未落）
            if total < run.total_case:
                for case in await _load_run_cases(db, run):
                    if not any(r.case_id == case.id for r in results):
                        # P2-D6：事务内直插 error 结果（复用本事务 db）。本事务持 run 行
                        # FOR UPDATE 锁；若走 _save_result 自开 session，插 eval_result 的
                        # FK 检查需 S 锁 eval_run 行，与本事务 X 锁互锁超时（1205）。
                        db.add(EvalResult(
                            run_id=run_id, case_id=case.id,
                            case_version_id=await self._ensure_case_version(case),
                            pass_fail="error", error_type="cancelled",
                            error_detail="未执行（取消/中断）", finished_at=_now()))
                results = (await db.execute(select(EvalResult).where(EvalResult.run_id == run_id))).scalars().all()

            na = sum(1 for r in results if r.pass_fail == "na")
            error = sum(1 for r in results if r.pass_fail == "error")
            passed = sum(1 for r in results if r.pass_fail == "pass")
            run.total_case = len(results)
            run.error_case = error
            run.na_case = na
            run.pass_case = passed  # 评分阶段（scorer）重算 pass/fail/na
            run.fail_case = 0
            run.finished_at = _now()
            if not external_terminal:
                run.status = CANCELLED if self._is_cancelled(run_id) else (
                    SCORING if passed else PARTIAL_FAILED if error else COMPLETED)
            await db.commit()
            logger.info("run %s _finish 完成（status=%s pass=%s err=%s）", run_id, run.status, passed, error)
            final_status = run.status  # 块内捕获（commit 后属性已刷新），防 detached 读
        self._heartbeats.pop(run_id, None)
        # 阶段三评分：执行成功且非外部终态的 run 交给 scorer（重算 pass/fail + 落分）
        if final_status == SCORING:
            await score_run(run_id)

    async def _fail_run(self, run_id: int, reason: str) -> None:
        async with SessionLocal() as db:
            # P2-D6：锁定读 + 状态前置判断——防旧快照/竞态覆盖已写入的终态
            # （timeout/cancelled/completed）。锁定读永远读最新，仅对非终态置
            # partial_failed；run 不存在提前返回（心跳/限流桶随之也不存在）。
            run = await db.get(EvalRun, run_id, with_for_update=True)
            if run is None:
                return
            if run.status in ("pending", "running", "scoring"):
                run.status = PARTIAL_FAILED
                run.finished_at = _now()
                if not run.run_config:
                    run.run_config = {"fail_reason": reason}
                await db.commit()
        self._heartbeats.pop(run_id, None)
        self.drop_run_limits(run_id)  # 异常路径同清理 per-run 桶


orchestrator = RunOrchestrator()  # 单进程全局实例（workers=1 前提成立）
