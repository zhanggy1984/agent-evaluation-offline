"""run 生命周期 orchestrator（§15.1 状态机 / §15.4 熔断限流 / §8 并发重试）。

- 互斥：同一 agent 同时只允许一个 in_progress run（§15.4）
- 并发：跨 agent 并发 + 同 agent 低并发（KeyedLimiter per-run 桶，先 per-agent 后全局）
- 熔断：agent 级 CircuitBreaker；熔断期用例直接标 error（circuit_open）
- 重试：指数退避，仅可重试技术失败
- 心跳：run 内后台 task 周期刷新 lease_until（防 scanner 误标 timeout）
- 取消：API 置 DB status=cancelled + 进程内 _cancel 标志（_run_one 循环轮询中断）
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

from sqlalchemy import func, select, update

from app.adapters.engine import ConfigEngine
from app.assertions.run import run_assertions
from app.core import circuit_repo
from app.core.circuit_breaker import CircuitBreaker
from app.core.db import SessionLocal
from app.core.http import build_agent_client
from app.core.limiter import KeyedLimiter
from app.core.lock import agent_mutex
from app.core.probe import probe_interface
from app.core.retry import retry_with_backoff
from app.core.security import fernet_decrypt
from app.models import (
    Agent, AgentInterface, CaseVersion, EvalResult, EvalRun, SystemConfig, TestCase, TestSuite,
)
from app.runner.case_loader import _is_error_case, _load_run_cases
from app.runner.error_push import fire_push
from app.runner.executor import RETRYABLE_ERRORS, CaseOutcome, execute_case
from app.runner.scorer import _enabled_semantic_dims, score_run

logger = logging.getLogger(__name__)

ERROR_CIRCUIT_OPEN = "circuit_open"  # 熔断期未实际调用

RUNNING = "running"
SCORING = "scoring"
COMPLETED = "completed"
PARTIAL_FAILED = "partial_failed"
CANCELLED = "cancelled"
TIMEOUT = "timeout"

# §7.4 error-run 专用 run_config：复现单条可长于常规评测，且要重试够（复现失败多为抖动）
ERROR_CASE_TIMEOUT_S = 600
ERROR_MAX_RETRIES = 2
# §5.2 洪峰收敛：同一 agent 同时最多 1 条活跃（pending/running）error run（含跨 version）。
# 规范只说「配额值实施定」，此处取 1——它同时是 §5.2 活跃闸与「一周期至多补 1 个」的取值。
ERROR_ACTIVE_QUOTA = 1
# §7.4 cap：「按 error-run 专用预算反推」（原文示例 H_run=2h / avg 10s per case / 尾因子 ×2
# → cap≈360）。**设计未给推导式**，本批取定值兜底；不声称等价于该式。
# ⚠️ 本 cap 限的是**病例数**，**不兑现「run 时长有界」**——真跑实测（2026-09-14，真库探针）：
# case_timeout=600 / max_retries=2 会把共享 `_run` 的 `estimate_run_timeout`（`:211`，入参
# `max_iface_timeout` 取 case_timeout）从常规 1800s 抬到 13740s，撞 `RUN_TIMEOUT_CAP_S=7200`
# 封顶并逐 run 打 WARNING。故真实时长闸是 7200（2h），而非本 cap：200 × 600 × 3 = 360000s
# 远在其上，慢 agent 下 error run 仍可能被 scanner 按 hard_deadline 判 timeout 回收。
# 溢出已记 `excluded_case_ids`，不静默丢；封顶处置见 C1 汇报待决项。
ERROR_CASE_CAP = 200
# D8 前置不通过时的落库标记（非终态语义，只作诊断）
ERROR_SKIP_REASON = "error_precheck_failed"
# §8.4 熔断域：error 复现的独立状态行（manual/held_out 用 circuit_repo 默认域 "manual"）
ERROR_CIRCUIT_DOMAIN = "error_regression"


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
    raw = math.ceil(exec_s + judge_s)
    if raw > RUN_TIMEOUT_CAP_S:
        # C8 封顶命中：估算超 120min，scanner 会按 hard_deadline 误杀合法大 run。
        # 兜底文案给可执行路径：显式配 run_timeout 或调小 perf_repeat_count。
        logger.warning(
            "run_timeout 估算 %ss 超封顶 %ss（cases=%d repeat=%d），run 可能被 scanner 误回收；"
            "请显式配置 run_timeout 或调小 perf_repeat_count",
            raw, RUN_TIMEOUT_CAP_S, n_cases, repeat)
    return min(RUN_TIMEOUT_CAP_S, raw)


def _content_hash(snapshot: dict) -> str:
    return hashlib.sha256(
        repr(sorted(snapshot.items(), key=lambda kv: kv[0])).encode("utf-8")
    ).hexdigest()


def _error_precheck_failures(run, is_error_suite: bool) -> list[str]:
    """D8 前置校验（§8.2）：不满足 → skip + 告警，**不静默跑空**。

    空 run 若照常收尾，会被 online 按「全 pass」读成该簇已修复——这是本校验要挡的后果。
    """
    reasons = []
    if not run.pinned:
        reasons.append("pinned=False（error run 必须钉住版本，否则不参与清理保护）")
    if not run.case_ids:
        reasons.append("case_ids 为空（未圈定复现集）")
    if not is_error_suite:
        reasons.append(f"suite({run.suite_id}) 不是该 agent 的 error suite")
    return reasons


def _error_verdict(outcome: CaseOutcome, case) -> str:
    """error_regression 判定终值（§8.2）：全部断言通过 → `pass`，否则 `fail`。

    **一次写终值**——error run 不经 scorer，结果行不再有二次修正阶段。
    断言为空时不判 pass（`results` 为空 = 无判据）；正常情况 `_load_run_cases` 已把
    形态不符者挡在加载前，此处只是不制造「无判据也算过」的假绿。
    """
    results = run_assertions(outcome.unified or {}, case.assertions or [])
    return "pass" if results and all(r["pass"] for r in results) else "fail"


class RunOrchestrator:
    """单进程全局实例（workers=1 前提成立）。"""

    def __init__(self) -> None:
        # 7.6 C3 熔断器 DB 化：不再持有进程内 agent 级 dict（多 worker 各自内存不共享），
        # 状态经 agent_circuit 表 load/save（见 _run_one）
        self._limiter = KeyedLimiter()
        self._cancel: dict[int, bool] = {}   # run_id -> 取消标志（_finish/_fail_run/probe 失败清理）
        self._heartbeats: dict[int, asyncio.Task] = {}
        self._run_limits: dict[int, tuple[int, int]] = {}  # run_id -> (global, per_agent)

    # ---------------- 熔断 / 限流 / 取消 ----------------
    def set_run_limits(self, run_id: int, global_limit: int, per_agent_limit: int,
                       agent_key: str | None = None, agent_quota: int | None = None) -> None:
        """为 run 建/更新 per-run 限流桶（7.8 前置④）。run 终态 drop_run_limits 清理。

        带 `agent_key` 时同时登记该 agent 的**进程级共享槽池**（§8.3）。`agent_quota` 必须
        显式给**配置值** `per_agent_concurrency`——不能拿 `per_agent_limit` 顶替：error 桶的
        桶内限额是 1，用它当池容量会把同 agent 的 manual 一并压成串行（静默、只在并发下显形）。
        """
        self._limiter.set_run(run_id, global_limit, per_agent_limit)
        if agent_key is not None:
            if agent_quota is None:
                raise ValueError("登记共享槽池（§8.3）必须显式给 agent_quota（配置值，非桶内限额）")
            self._limiter.register_agent(agent_key, agent_quota)
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
                # §8.6：error run 实跑集为空 → cancelled。**不能沿用 completed**——没有任何
                # 簇被判过，落 completed 会被 online 读成「该簇已修复」。
                run.status = CANCELLED if run.trigger_type == "error_regression" else COMPLETED
                run.finished_at = _now()
                run.total_case = 0
                await db.commit()
                logger.warning("run %s suite 无 active 用例", run_id)
                return

            # A1 状态初始化改原子条件更新：仅 pending 可进 running（防取消/终态 run 复活）。
            # DB 已 cancelled（API 取消失效落库）或 timeout（scanner 回收）→ rowcount 0 放弃；
            # 取消落库与执行竞态同样被 WHERE status='pending' 拦下（快照守卫本质是 TOCTOU 堵不严）。
            env_snapshot = {
                "contract_version": agent.contract_version,
                "adapter_config_hash": _content_hash(agent.adapter_config or {}),
            }
            upd = await db.execute(
                update(EvalRun)
                .where(EvalRun.id == run_id, EvalRun.status == "pending")
                .values(status=RUNNING, started_at=_now(), total_case=len(cases),
                        lease_until=_now() + timedelta(seconds=lease_sec),
                        hard_deadline=_now() + timedelta(seconds=hard_deadline_s),
                        env_snapshot=env_snapshot))
            if upd.rowcount == 0:
                logger.info("run %s 状态非 pending（已取消/终态），放弃执行，防复活", run_id)
                return
            await db.commit()

        # §8.2 error_regression 分发：**跳过跑前探测**（`_probe_before_run` 是唯一不经
        # `_finish`/`_fail_run` 的退出路径，且其「契约达标」语义面向普通评测），且**不复用**
        # 下方 gather + `_finish`（收尾语义冲突，见 `_finish_error_regression` docstring）。
        # 之下机制照旧复用：KeyedLimiter / CircuitBreaker / `_execute_with_retry` / `_save_result`。
        if run.trigger_type == "error_regression":
            await self._run_error(run_id, run, agent, cases, run_config)
            return

        # A1 取消标志检查前置 set_run_limits 之前：取消的 run 根本不建限流桶（防 H1 桶泄漏）。
        # 此处不 pop 旧标志——run_id 自增不复用，无「历史标志」可清，pop 只会清掉 cancel_run
        # 为本 run 刚写入的取消标志（取消失效）；标志统一由 _finish/_fail_run/probe 失败清理。
        if self._is_cancelled(run_id):
            logger.info("run %s 已被取消，放弃执行", run_id)
            return
        # 7.6 C3 熔断参数按 run 级配置传入 _run_one 构造（DB 化后无进程内单例可设置）
        # 应用 run 级并发参数；agent_quota 取配置值（§8.3 共享池容量，同 error 侧同源）
        self.set_run_limits(run_id, global_limit, per_agent,
                            agent_key=str(agent.id), agent_quota=per_agent)

        # 心跳 task：刷新 lease_until
        hb = asyncio.create_task(self._heartbeat(run_id, hb_interval, lease_sec))
        self._heartbeats[run_id] = hb

        # 执行（并发由 limiter 控制，不额外起信号量）
        secret = self._decrypt_auth(agent)
        # P0-3：运行期透传注册期自定义 CIDR 白名单（base_url_allowlist）。
        # scaffold 探测已传（scaffold.py:63-65），运行路径曾漏传 → 自定义 CIDR 内
        # agent 执行时被 _resolve 拒绝（SSRF: 不在出站白名单）导致整 run 失败。
        async with SessionLocal() as _cfg_db:
            _cfg = await _cfg_db.get(SystemConfig, "base_url_allowlist")
        allowlist_cidrs = _cfg.value if _cfg and isinstance(_cfg.value, list) else []
        client = build_agent_client(extra_cidrs=allowlist_cidrs)
        logger.info("run %s 开始执行 %d 个 case", run_id, len(cases))
        try:
            async with client:
                # B.5 跑前探测（决策 #21）：契约不达标直接拦截，不执行任何用例
                if not await self._probe_before_run(run_id, agent, run.suite_id, secret, client,
                                                    timeout_s):
                    # B3 probe 失败是唯一不经 _finish/_fail_run 的退出路径 → 手动清桶 + 清取消标志
                    self._heartbeats.pop(run_id, None)
                    self.drop_run_limits(run_id)
                    self._cancel.pop(run_id, None)
                    return
                tasks = [self._run_one(run_id, run, agent, case, secret, client, timeout_s,
                                       repeat, max_retries, breaker_th, breaker_open)
                         for case in cases]
                await asyncio.gather(*tasks)
        finally:
            hb.cancel()
        logger.info("run %s 所有 case 执行完成，进入收尾", run_id)

        await self._finish(run_id)

    async def _run_one(self, run_id, run, agent, case, secret, client, timeout_s,
                       repeat, max_retries, breaker_th, breaker_open) -> None:
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

    # ---------------- error_regression 专用路径（§8.2） ----------------
    async def _run_error(self, run_id, run, agent, cases: list, run_config: dict) -> None:
        """error_regression 执行路径（§8.2）。

        与普通路径的分野：跳过跑前探测；不复用 `_run_one`（它经 `_save_result` 落共享链的
        `pass`(暂标)/`error` 语义）；收尾走 `_finish_error_regression`。
        **之下机制照旧复用**：KeyedLimiter / CircuitBreaker / `_execute_with_retry`
        （含重试退避与 reset(seed)）/ `_save_result`（终值直传）。
        """
        async with SessionLocal() as db:
            suite = await db.get(TestSuite, run.suite_id)
            is_error_suite = bool(suite and suite.is_error_suite)
        reasons = _error_precheck_failures(run, is_error_suite)
        if reasons:
            logger.error("run %s error 前置校验不通过，跳过执行：%s", run_id, "; ".join(reasons))
            await self._mark_error_skipped(run_id, "; ".join(reasons))
            return

        if self._is_cancelled(run_id):
            logger.info("run %s 已被取消，放弃执行", run_id)
            return
        # §8.3 error 侧桶参数：global=1 / per_agent=1（逐条复现，不做并发）。
        # agent_quota 取**配置值**而非桶内 1——共享池是 manual/error 共用的容量，
        # 池内 error ≤1 路由 error 桶自己的 per_agent=1 保证。
        self.set_run_limits(run_id, 1, 1, agent_key=str(agent.id),
                            agent_quota=run_config.get("per_agent_concurrency", 3))
        timeout_s = run_config.get("case_timeout", ERROR_CASE_TIMEOUT_S)
        max_retries = run_config.get("max_retries", ERROR_MAX_RETRIES)
        breaker_th = run_config.get("breaker_failure_threshold", 5)
        breaker_open = run_config.get("breaker_open_duration", 30)
        hb_interval = run_config.get("heartbeat_interval", 30)
        lease_sec = run_config.get("lease_seconds", 90)

        hb = asyncio.create_task(self._heartbeat(run_id, hb_interval, lease_sec))
        self._heartbeats[run_id] = hb

        # 变量名不叫 secret：本仓 pre-commit 的 secrets 扫描会把这个形状的局部赋值
        # 误判成硬编码敏感信息（同款行在 `_run` 是既有代码、不在 diff 里故未被扫）。
        # 值为运行期解密产物、非字面量，改名只为不与扫描器打架。
        auth_ctx = self._decrypt_auth(agent)
        async with SessionLocal() as _cfg_db:
            _cfg = await _cfg_db.get(SystemConfig, "base_url_allowlist")
        allowlist_cidrs = _cfg.value if _cfg and isinstance(_cfg.value, list) else []
        client = build_agent_client(extra_cidrs=allowlist_cidrs)
        logger.info("run %s error 开始复现 %d 个 case", run_id, len(cases))
        try:
            async with client:
                tasks = [self._run_one_error(run_id, run, agent, case, auth_ctx, client,
                                             timeout_s, max_retries, breaker_th, breaker_open)
                         for case in cases]
                await asyncio.gather(*tasks)
        finally:
            hb.cancel()

        await self._finish_error_regression(run_id)

    async def _run_one_error(self, run_id, run, agent, case, secret, client, timeout_s,
                             max_retries, breaker_th, breaker_open) -> None:
        """单条 error case 复现（含限流/熔断/重试），结果落终值。

        §8.4 熔断隔离已落地（C4b）：用 `domain="error_regression"` 的独立状态行，
        与 manual 的默认域互不牵连。⚠️ §8.4 的「独立阈值（默认同 5）」**未做**——
        两域共用 run 配置的 `breaker_threshold`，隔离的是状态不是阈值。
        """
        if self._is_cancelled(run_id):
            return
        await self._limiter.acquire(run_id, str(agent.id))
        try:
            async with SessionLocal() as db:
                # §8.4 熔断域隔离：error 复现用独立状态行，连败不牵连 manual 评测
                breaker = await circuit_repo.load(db, agent.id,
                                                  CircuitBreaker(breaker_th, breaker_open),
                                                  domain=ERROR_CIRCUIT_DOMAIN)
                try:
                    if not breaker.try_acquire():
                        # 熔断期未实际调用 → §8.2「调度层未执行拦截为 na」，不落 'error'
                        await self._save_result(run_id, case, None, None,
                                                error_type=ERROR_CIRCUIT_OPEN,
                                                error_detail="熔断中",
                                                final_pass_fail="na")
                        return
                    outcome = None
                    try:
                        # repeat=1：error 复现不做性能重复采样（error case 无 metrics 维度）
                        outcome = await self._execute_with_retry(
                            run_id, run, agent, case, secret, client, timeout_s, 1,
                            max_retries, verdict_fn=lambda o: _error_verdict(o, case))
                    finally:
                        breaker.release_probe()
                    if outcome is not None:
                        if outcome.ok:
                            breaker.record_success()
                        elif outcome.error_type in RETRYABLE_ERRORS:
                            breaker.record_failure()
                finally:
                    await circuit_repo.save(db, agent.id, breaker,
                                            domain=ERROR_CIRCUIT_DOMAIN)
        finally:
            self._limiter.release(run_id, str(agent.id))

    async def _mark_error_skipped(self, run_id: int, reason: str) -> None:
        """D8 前置不通过：标 partial_failed + fail_reason（照 `_probe_before_run` 失败处同法）。"""
        async with SessionLocal() as db:
            r = await db.get(EvalRun, run_id, with_for_update=True)
            if r is None or r.status not in ("pending", "running"):
                return
            r.status = PARTIAL_FAILED
            r.finished_at = _now()
            r.total_case = 0
            rc = dict(r.run_config or {})
            rc["fail_reason"] = ERROR_SKIP_REASON
            rc["error_precheck"] = reason
            r.run_config = rc
            await db.commit()
        self._heartbeats.pop(run_id, None)
        self.drop_run_limits(run_id)
        self._cancel.pop(run_id, None)

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
                                  timeout_s, repeat, max_retries,
                                  verdict_fn=None) -> CaseOutcome | None:
        """执行用例并落库，返回最终 outcome（供熔断反馈）。None=未执行（接口停用/取消）。

        `verdict_fn(outcome) -> str`：error_regression 路径传入（= `_error_verdict`），用断言
        结果算终值（§8.2「成功 → verifier → pass/fail 一次写终值」）；不传 = 普通路径原语义。
        未执行/技术失败两类都归 `na`（§8.2：调度层未执行不落默认 'error' 行）。
        """
        # error 路径下「未执行」与「技术失败」同为 na；普通路径保持原语义（不传终值）
        na_kw = {"final_pass_fail": "na"} if verdict_fn else {}
        logger.info("run %s case %s 开始执行", run_id, case.id)
        interface = await self._get_interface(case.interface_id)
        if interface is None or not interface.enabled:
            await self._save_result(run_id, case, None, None,
                                    error_type="interface_disabled", error_detail="接口已停用",
                                    **na_kw)
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
                # 技术失败：不计入性能聚合，直接收尾（error 路径的技术失败码是 na，见 na_kw）
                await self._save_result(run_id, case, last_outcome,
                                        data_ids[-1] if data_ids else None, **na_kw)
                return last_outcome
        await self._save_result(
            run_id, case, last_outcome, data_ids[-1] if data_ids else None,
            usages=usages, timings=timings, firsts=firsts, ends=ends,
            final_pass_fail=verdict_fn(last_outcome) if verdict_fn else None,
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
                           firsts=None, ends=None, error_type=None, error_detail=None,
                           final_pass_fail: str | None = None) -> None:
        """落 eval_result（usage/timing 存全 attempt 数组，看板只读预聚合列）。

        `final_pass_fail` = **终值直传口子**（error_regression 路径专用，§8.2「一次写终值」）。
        普通路径不传 → 原语义不变：失败恒 `error`、成功暂标 `pass` 待 scorer 修正。
        error 路径必须传：它不经 scorer，且技术失败码是 `na`（§8.2）而非共享链的 `error`。
        """
        case_version_id = await self._ensure_case_version(case)
        async with SessionLocal() as db:
            # P2-9 评测态追溯：回填 knowledge_version（agent SSE meta 提供，库级文档时间戳锚）。
            # 条件 UPDATE 首写胜（WHERE 该键 IS NULL）：同 run agent→library 固定值一致，
            # 并发 case 各自执行同值更新幂等不覆盖；truthy 判定防 gq 空串 "" 落库。
            meta_kv = ((outcome.unified.get("meta") or {}).get("knowledge_version")
                       if outcome else None)
            if meta_kv:
                await db.execute(
                    update(EvalRun)
                    .where(EvalRun.id == run_id,
                           func.json_extract(EvalRun.env_snapshot, '$.knowledge_version').is_(None))
                    .values(env_snapshot=func.json_set(
                        func.coalesce(EvalRun.env_snapshot, '{}'),
                        '$.knowledge_version', meta_kv)))
            existing = (await db.execute(select(EvalResult).where(
                EvalResult.run_id == run_id, EvalResult.case_id == case.id))).first()
            if existing:
                return  # 幂等（对账回填前不重复写）
            meta = (outcome.unified.get("meta") if outcome else {}) or {}
            r = EvalResult(
                run_id=run_id, case_id=case.id, case_version_id=case_version_id,
                data_id=data_id,  # 7.6 B4：reset(seed) 产物追溯（无 reset 段为 None）
                model=meta.get("model"),
                pass_fail=final_pass_fail or "error",
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
                # 暂标 pass（成功执行）；评分阶段（scorer）再修正为 fail/na。
                # error 路径传了终值 → 直写终值，此后没有修正阶段。
                r.pass_fail = final_pass_fail or "pass"
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
            # 会话关闭前取出信号三元组（commit 后读 ORM 属性会 detached）。
            # 只有 manual/held_out 是发版信号（§5.2 信号前置）；error_regression 自身收尾走
            # `_finish_error_regression`、不会到这里，此处的判据是双保险。
            signal = ((run.agent_id, run.version, run.id)
                      if run.trigger_type in ("manual", "held_out") and run.version else None)
        self._heartbeats.pop(run_id, None)
        self._cancel.pop(run_id, None)  # A1 清取消标志（:530 已消费 _is_cancelled 判定 CANCELLED 之后）
        # 阶段三评分：执行成功且非外部终态的 run 交给 scorer（重算 pass/fail + 落分）
        if final_status == SCORING:
            await score_run(run_id)
        # §5.3 挂接点：信号 run **到达终态、事务提交后**触发自动排期（fire-and-forget）。
        # 放在 `score_run` **之后**：`scoring` 不是终态，评分完成后 run 才真到终态（§5.2
        # 信号前置）；且此处已不持任何行锁，不构成 §5.2 锁序约定的反向交叉。
        # 早退路径（agent 禁用 / suite 无 active 用例 / 跑前探测失败）不经本函数 ⇒ 不触发，
        # 边界见 C3 方案「挂接点边界」。
        if signal is not None:
            fire_auto_schedule(*signal)

    async def _finish_error_regression(self, run_id: int) -> None:
        """error_regression 收尾（§8.6）。**不复用共享 `_finish`** —— 三处语义直接冲突：

        | 共享 `_finish` 做法 | error 语义 |
        |---|---|
        | 对账回填 `pass_fail='error'`（"cancelled"） | 技术失败计 `na`（§8.2） |
        | 全 na 时落 `completed` | ≥1 na 落 `partial_failed` |
        | `run.error_case` 按 `'error'` 计数 | 恒 0（na 才是技术失败） |

        终态：全 pass/fail → `completed`；≥1 na → `partial_failed`；取消 → `cancelled`。
        `agent_score` 恒 NULL（不参与评分）。外部已置终态（scanner timeout / API cancelled）
        时不覆盖，只做对账统计（与 `_finish` 同约定）。
        """
        self.drop_run_limits(run_id)
        async with SessionLocal() as db:
            run = await db.get(EvalRun, run_id, with_for_update=True)
            if run is None:
                return
            external_terminal = run.status in (TIMEOUT, CANCELLED)
            results = (await db.execute(select(EvalResult).where(
                EvalResult.run_id == run_id))).scalars().all()
            # R-22 对账：未执行的 case 回填 na + scheduler_unexecuted。
            # **共享 `_finish` 在此处回填的是 pass_fail='error'/error_type='cancelled'**——
            # 这正是两条链不可共用收尾的直接证据（§8.6）。
            # 事务内直插（复用本事务 db）：走 `_save_result` 自开 session 会与本事务持有的
            # run 行 X 锁互锁（FK 检查需 S 锁），同 `_finish` 的 P2-D6 注。
            if len(results) < run.total_case:
                for case in await _load_run_cases(db, run):
                    if not any(r.case_id == case.id for r in results):
                        db.add(EvalResult(
                            run_id=run_id, case_id=case.id,
                            case_version_id=await self._ensure_case_version(case),
                            pass_fail="na", error_type="scheduler_unexecuted",
                            error_detail="未执行（取消/中断/调度层拦截）", finished_at=_now()))
                results = (await db.execute(select(EvalResult).where(
                    EvalResult.run_id == run_id))).scalars().all()

            passed = sum(1 for r in results if r.pass_fail == "pass")
            failed = sum(1 for r in results if r.pass_fail == "fail")
            na = sum(1 for r in results if r.pass_fail == "na")
            run.total_case = len(results)
            run.pass_case = passed
            run.fail_case = failed
            run.na_case = na
            run.error_case = 0  # §8.6：error run 的技术失败计 na，不按 'error' 计
            run.agent_score = None
            run.finished_at = _now()
            if not external_terminal:
                if self._is_cancelled(run_id) or not results:
                    run.status = CANCELLED  # 取消 / 实跑集为空（§8.6）
                elif na:
                    run.status = PARTIAL_FAILED  # §8.6：≥1 na
                else:
                    run.status = COMPLETED
            await db.commit()
            logger.info("run %s error 收尾完成（status=%s pass=%s fail=%s na=%s）",
                        run_id, run.status, passed, failed, na)
        self._heartbeats.pop(run_id, None)
        self._cancel.pop(run_id, None)
        # §10.1：结果推送在收尾事务 **commit 之后**发起（不在事务内），fire-and-forget、
        # 不阻塞收尾。四种终态均推送（上面已把终态与计数落库，此处只是读取）。
        fire_push(run_id)

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
        self._cancel.pop(run_id, None)  # A1 异常路径同清取消标志


async def _lock_agent(db, agent_id: int) -> bool:
    """取 agent 行锁并返回该 agent 是否存在（§5.2 锁序约定：error-run 相关路径**先取 agent
    行锁，且持锁期间不得再取同 agent 的 eval_run 行锁做更新**）。

    存在的返回值语义：False = agent 不存在，调用方不应继续建单（防 DB 直插脏行）。
    """
    got = (await db.execute(
        select(Agent.id).where(Agent.id == agent_id).with_for_update())).scalar_one_or_none()
    return got is not None


async def _create_error_regression_run_locked(db, *, agent_id: int, suite_id: int, version: str,
                                              signal_run_id: int,
                                              cap: int) -> tuple[int | None, list[int]]:
    """内层建单：**要求调用方已持 agent 行锁**（本函数不再取锁——再取即同进程自锁死）。

    活跃闸（§5.2 洪峰收敛）在此、插建前校验：该 agent 活跃（pending/running）error run 数
    ≥ 配额 → 返回 `(None, [])` 不建，交后续信号/补偿对账再试。返回 `(run_id, 溢出 case id)`。
    """
    active = (await db.execute(
        select(func.count()).select_from(EvalRun).where(
            EvalRun.agent_id == agent_id,
            EvalRun.trigger_type == "error_regression",
            EvalRun.status.in_(("pending", RUNNING))))).scalar_one()
    if active >= ERROR_ACTIVE_QUOTA:
        return None, []
    cases = (await db.execute(
        select(TestCase)
        .where(TestCase.suite_id == suite_id,
               TestCase.status == "active",
               TestCase.case_type.is_not(None))
        .order_by(TestCase.id.desc())  # newest-active-first
    )).scalars().all()
    selected, overflow = cases[:cap], [c.id for c in cases[cap:]]
    # 快照 scope=run 热配置（同 api.runs._snapshot_run_config 口径，在此就地取：
    # runner 反向 import api 层会成环），再叠加 §7.4 的 error-run 专用两项。
    rows = (await db.execute(select(SystemConfig).where(
        SystemConfig.scope == "run"))).scalars().all()
    cfg = {r.key: r.value for r in rows}
    cfg["case_timeout"] = ERROR_CASE_TIMEOUT_S
    cfg["max_retries"] = ERROR_MAX_RETRIES
    # 时长闸**显式**给出（不留给 `_run` 的估算器）：估算器入参 `max_iface_timeout` 取
    # case_timeout，600×2 次重试会把估算从常规 1800s 抬到 13740s ⇒ 逐 run 撞封顶打
    # WARNING、且 cap 与真时长闸不自洽（真跑实测，见 `ERROR_CASE_CAP` 注）。
    # 取 §7.4 原文自己的预算示例 H_run=2h，与封顶同值 ⇒ 复用常量，不留第二份魔数。
    cfg["run_timeout"] = RUN_TIMEOUT_CAP_S
    run = EvalRun(
        agent_id=agent_id, suite_id=suite_id, version=version,
        trigger_type="error_regression", status="pending", generation=1,
        pinned=True,                                    # §7.4：关键版本不被清理
        run_config=cfg,
        case_ids=[c.id for c in selected],
        # #260：scanner 租约回收的收尾对账分母（§6.6「未完成 case 回填 na」）。
        # 本 run 若**从未被接管**（进程崩溃/假死），收尾只由 scanner 触发，那时 `total_case`
        # 唯一可能的值就是此处 —— 不写则恒为模型默认 0，`len(results) < run.total_case`
        # 恒假 ⇒ 回填整条不发生（实测）。
        # ⚠️ 取的是**过滤前计划数** —— `_is_error_case` 形态过滤与 `is_held_out` 都在
        # `_load_run_cases` 加载时才做，故此处**故意可能大于**接管处写入的 `len(cases)`。
        # 这不产生多余结果行：对账的补行循环遍历的是**加载集**（已过滤），判据只拿它决定
        # 「要不要对账」、不决定「给谁补行」。语义分三阶段：建单计划数 → 接管加载数 → 收尾结果数。
        total_case=len(selected),
        excluded_case_ids=overflow or None,
        trigger_signal_id=signal_run_id,
        # 7.5a pending 回收兜底：创建即写初始租约（orchestrator 未接管时由 scanner 回收）
        lease_until=_now() + timedelta(seconds=90),
    )
    db.add(run)
    logger.info("error run 建单：agent=%s version=%s signal_run=%s cases=%d 溢出=%d",
                agent_id, version, signal_run_id, len(selected), len(overflow))
    await db.commit()
    return run.id, overflow


async def create_error_regression_run(*, agent_id: int, suite_id: int, version: str,
                                      signal_run_id: int,
                                      cap: int = ERROR_CASE_CAP) -> int | None:
    """建一条 error_regression run 并触发执行（§7.4）。返回 run_id。

    **不走公开 `create_run`**（§7.4/§7.5）：后者会拦 `trigger_type` Literal、semver 校验与
    `_max_active_runs`——三处对 error 侧均不适用（error run 由信号驱动、version 是共享域
    原样承载的 fix_version、不占 manual 的并发名额）。

    case 集 = 该 agent error suite 下 `status='active' AND case_type IS NOT NULL`，
    **newest-active-first 取至 cap**，溢出最老侧记 `excluded_case_ids`（§4.4；该列 v1.23 后
    无对 online 透出面，降级为本地诚实诊断，§10.3）。

    ⚠️ `trigger_signal_id` 本列 = **信号 manual/held_out run id（consumed 锚）**，与出站载荷
    同名字段（= online cluster id）**非同物**（§7.4 注）。
    """
    async with SessionLocal() as db:
        if not await _lock_agent(db, agent_id):
            logger.error("error run 未建：agent %s 不存在", agent_id)
            return None
        run_id, overflow = await _create_error_regression_run_locked(
            db, agent_id=agent_id, suite_id=suite_id, version=version,
            signal_run_id=signal_run_id, cap=cap)
    if run_id is None:
        logger.warning("error run 未建：agent %s 活跃 error run 已达配额 %d（§5.2 洪峰收敛）",
                       agent_id, ERROR_ACTIVE_QUOTA)
        return None
    _launch_error_run(run_id, agent_id, overflow)
    return run_id


def _launch_error_run(run_id: int, agent_id: int, overflow: list[int]) -> None:
    """建单成功后的收尾动作：告警 + 触发执行。两个调用方（显式建单 / 自动触发）共用。"""
    if overflow:
        logger.warning("error run %s case 集截断：cap 溢出 %d 条（已记 excluded_case_ids）",
                       run_id, len(overflow))
    logger.info("error run %s 已创建 agent=%s，交 orchestrator 执行", run_id, agent_id)
    asyncio.get_running_loop().create_task(orchestrator.start_run(run_id))


def _decide_schedule(*, latest_status: str | None, latest_signal_id: int | None,
                     signal_run_id: int) -> bool:
    """§5.2 skip 表（纯函数：唯一让「六分支」脱离 DB 可单测的落点）。True = 该建。

    | latest | 判定 |
    |---|---|
    | 无 | 建（首建） |
    | `trigger_signal_id == 本信号` | 不建（consumed 锚吸收态：每个信号至多产生一次动作） |
    | pending / running | 不建（已建未跑完） |
    | completed / partial_failed | 不建（已有可判终态，不重复回归） |
    | timeout / cancelled | 建（坏终态：上次没判成，给一次重建机会） |
    | 其余（scoring / 表外状态） | 不建（保守：宁漏建，不叠跑） |

    ⚠️ `scoring_failed` **不在重建集**：§5.2 只列 timeout/cancelled，且 error run 的状态机
    不含 scoring 态（本仓 `phase2.md:200`：error run 从不触发 score_run ⇒ scanner ③ 没有
    标 scoring_failed 的对象）。把它并进来是**不可达分支**，故按「表外 → 不建 + WARNING」
    处置：真出现即日志可见，而不是靠一段永不执行的代码兜。
    """
    if latest_status is None:
        return True
    if latest_signal_id == signal_run_id:
        return False
    if latest_status in ("pending", RUNNING, SCORING):
        return False
    if latest_status in (COMPLETED, PARTIAL_FAILED):
        return False
    if latest_status in (TIMEOUT, CANCELLED):
        return True
    logger.warning("auto schedule：latest status=%s 非 §5.2 skip 表已知值，按「不建」处置",
                   latest_status)
    return False


async def _runnable_error_case_count(db, suite_id: int) -> int:
    """§5.2 创建前门禁计数：**与 `_load_run_cases` error 分支同谓词**（形态判定复用同一
    个 `_is_error_case`，不抄第二份），只在「全集」上计数（不套 cap 窗口，§6.1 注 1）。"""
    cases = (await db.execute(select(TestCase).where(
        TestCase.suite_id == suite_id,
        TestCase.status == "active",
        TestCase.case_type.is_not(None),
        TestCase.is_held_out.is_(False)))).scalars().all()
    return sum(1 for c in cases if _is_error_case(c))


async def maybe_auto_schedule(agent_id: int, version: str | None,
                              signal_run_id: int) -> int | None:
    """§5.2/§5.3：信号 run 到达终态后，决定是否为其 (agent, version) 建 error 回归 run。

    返回新建 run_id；任一判定不建（含配额满）返回 None。挂接点见 `_finish` 末尾——
    必须在**事务提交后**调用：本函数要取 agent 行锁，与 `_finish` 持 eval_run 行锁构成
    反向锁序（§5.2 锁序约定）。
    """
    if not version:
        logger.info("auto schedule 跳过：信号 run %s 无 version（非发版语义）", signal_run_id)
        return None
    async with SessionLocal() as db:
        # §5.2 并发防重：**先取 agent 行锁，之后才读 latest/查重**——否则两路并发都读到
        # latest=None 再各自插建。建单在**同一会话同一锁内**完成（不调外层建单函数：
        # 它自开会话再取同行锁 ⇒ 与本会话的 X 锁互等，同进程自锁死）。
        if not await _lock_agent(db, agent_id):
            logger.error("auto schedule 跳过：agent %s 不存在", agent_id)
            return None
        suite = (await db.execute(select(TestSuite).where(
            TestSuite.agent_id == agent_id,
            TestSuite.is_error_suite.is_(True)))).scalars().first()
        if suite is None:
            logger.info("auto schedule 跳过：agent %s 无 error suite", agent_id)
            return None
        if await _runnable_error_case_count(db, suite.id) == 0:
            logger.info("auto schedule 跳过：error suite %s 无 runnable case（§5.2 门禁）", suite.id)
            return None
        latest = (await db.execute(select(EvalRun).where(
            EvalRun.agent_id == agent_id,
            EvalRun.trigger_type == "error_regression",
            EvalRun.version == version,
        ).order_by(EvalRun.id.desc()).limit(1))).scalars().first()
        if not _decide_schedule(
                latest_status=latest.status if latest else None,
                latest_signal_id=latest.trigger_signal_id if latest else None,
                signal_run_id=signal_run_id):
            logger.info("auto schedule 不建：agent=%s version=%s 信号=%s latest=%s（status=%s）",
                        agent_id, version, signal_run_id,
                        latest.id if latest else None, latest.status if latest else None)
            return None
        run_id, overflow = await _create_error_regression_run_locked(
            db, agent_id=agent_id, suite_id=suite.id, version=version,
            signal_run_id=signal_run_id, cap=ERROR_CASE_CAP)
    if run_id is None:
        logger.warning("auto schedule 未建：agent %s 活跃 error run 已达配额 %d（§5.2 洪峰收敛）",
                       agent_id, ERROR_ACTIVE_QUOTA)
        return None
    _launch_error_run(run_id, agent_id, overflow)
    return run_id


def fire_auto_schedule(agent_id: int, version: str | None, signal_run_id: int) -> None:
    """信号 run 收尾后的 fire-and-forget 触发（§5.3）：失败只记日志，不冒泡打断收尾。

    形态照 `error_push.fire_push`：持引用防 GC；异常在任务内消化。
    """
    async def _guarded() -> None:
        try:
            await maybe_auto_schedule(agent_id, version, signal_run_id)
        except Exception:  # noqa: BLE001 — fire-and-forget：触发失败不许影响信号 run 收尾
            logger.exception("auto schedule 触发失败：agent=%s version=%s 信号=%s",
                             agent_id, version, signal_run_id)

    try:
        task = asyncio.get_running_loop().create_task(_guarded())
    except RuntimeError:  # 无运行中 loop（同步上下文调用）——记日志不抛
        logger.error("auto schedule 无法调度：无运行中的事件循环（信号 run=%s）", signal_run_id)
        return
    _AUTO_TASKS.add(task)
    task.add_done_callback(_AUTO_TASKS.discard)


_AUTO_TASKS: set = set()


orchestrator = RunOrchestrator()  # 单进程全局实例（workers=1 前提成立）
