# 第五批硬伤/优化技术方案（批次 A + B + C + D，独立 subagent 评审修订版）

> 来源：新一轮独立 subagent 深度审查（12 项清单之外），产出 2 A + 7 B + 8 C 共 17 项。本方案逐项给 file:line 证据、改动、测试、风险。**A1/B1/B2/B3/B4/B5/B6/B7/C1/C4/C8 已人工核验属实**；C2/C5/C6/C7 为单行小改（agent 证据 + 实施时核实）。
> ⚠️ 待确认点集中在文末 §6，评审后用户定夺再实施。

## 独立 Plan subagent 评审结论（2026-08-31）：修订后通过

4 高危 + 5 中危 + 5 低危，关键修订已吸收进正文（标注 **[评审修订]**）：

- **H1**（A1）：`_is_cancelled` 早退会泄漏限流桶 → 早退前补 `drop_run_limits` + 把检查移到 `set_run_limits` 之前（两路都做）
- **H2**（A1）：快照守卫仍留「cancelled→running→timeout」翻转窗口 → :186 状态初始化改**原子条件更新** `UPDATE ... SET status='running' WHERE id=:id AND status='pending'`，rowcount==0 即 return
- **H3**（C1）：漏前端 `configMeta.js` 同步会红 CI（`test_frontend_meta_sync.py` 双向严格相等断言）→ backend seed + frontend configMeta + 运维 DELETE 同批原子变更
- **H4**（C4）：测试规格与修复规格矛盾（内层 :185 只包 client.judge，aggregate_verdicts 在外层 :203）→ 测试改断言「非 JudgeError 走 logger.exception + attempts 推进」
- **M1**：删 :200 pop 后 `_cancel` 无界增长 → `_finish`/:530 之后 + `_fail_run` 结尾补 pop
- **M2**（B1）：admin 重置密码需同批撤销该用户全部 RefreshToken（复刻 change_password），否则不闭环
- **M3**（B7）：批量取结果改**列投影** `select(EvalResult.run_id, EvalResult.score_per_dimension)`，避免拉 MEDIUMTEXT
- **M4**（C4）：实施时确认 JudgeClient 抛错边界（httpx 裸抛 vs JudgeError 包装），决定 :185 是否需单列 httpx
- **L1**（B4）：`float(w)` 可能抛 ValueError → try/except 转 400；文案注明权重 0-100 或 0-1 量纲
- **L3**：`_cancel` 类型注解 `dict[int,bool]`；**L5**（B5）：create_export 也顺带扫过期文件

---

## 批次 A：run 生命周期/状态机（最高优先）

### A1 cancel / 终态 run 会被 `_run` 复活（取消失效 + 状态机非法迁移）

**证据**：
- `orchestrator.py:130` `_run` 读 run 后**无 status 守卫**；`:186` 无条件 `run.status = RUNNING`；`:200` 无条件 `self._cancel.pop(run_id, None)`。
- `runs.py:343-345` cancel_run 置 `status="cancelled"` + `orchestrator.cancel_run(run_id)`（写 `_cancel[run_id]=True`）；`:340` 仅拦终态。
- `scanner.py:44-66` 会按租约把 pending/running 回收为 timeout。

**根因**：run_id 自增不复用，`:200` 的 pop 清掉的恰是 cancel_run 为本 run 刚写入的取消标志（「清历史标志」注释不成立）；且 `_run` 入口不校验 `status != "pending"`。

**触发 → 后果**：
1. 创建后立即取消（start_run 任务未跑到 :186）→ DB `cancelled` 被覆盖为 `running` + 标志被 pop → **run 全量执行，取消完全失效**；
2. scanner 已把 run 标 timeout（start_run 调度延迟超租约）→ `_run` 仍拉回 running 执行 → **已判超时 run 被复活**。

**改动（[评审修订] H1/H2/M1/L3）**：
1. **状态初始化改原子条件更新**：`:186-195` 的 `run.status = RUNNING` + commit 改 `update(EvalRun).where(EvalRun.id==run_id, EvalRun.status==PENDING).values(status=RUNNING, started_at=..., total_case=..., lease_until=..., hard_deadline=..., env_snapshot=...)`，`result.rowcount==0` → `return`——彻底关窗，拦「DB 已 cancelled/timeout」与取消落库/执行间的竞态（快照守卫本质是 TOCTOU，堵不严）。
2. **删 `:200` 的 `_cancel.pop`**；取消标志检查**移到 `set_run_limits`（:198）之前**（:195 事务提交后）——`if self._is_cancelled(run_id): return`（取消的 run 根本不建桶）。若放在 set_run_limits 之后，早退前必须补 `self.drop_run_limits(run_id)`（双保险，防 H1 桶泄漏）。
3. `_cancel` 类型注解 `dict[int, int]` → `dict[int, bool]`。
4. `_finish` 的 :530 状态判定**之后** + `_fail_run` 结尾各补 `self._cancel.pop(run_id, None)`（防标志无界增长；不能早于 :530，那里 `_is_cancelled` 决定 CANCELLED 状态）。

**测试**（新增 `tests/test_run_lifecycle.py`，mock DB + fake start_run）：cancel 后（DB cancelled + 标志）`_run` return 不改状态；scanner timeout 后 `_run` return；正常 pending 流程回归；取消早退不泄漏 `_run_limits`。

**风险**：状态机非法迁移消除；`_is_cancelled` 已在 `_run_one`（:231）使用，语义一致。

### B3 probe 失败路径泄漏 per-run 限流桶

**证据**：`orchestrator.py:198` `set_run_limits` 先执行；`:213-216` `_probe_before_run` 返回 False → `self._heartbeats.pop; return`，**未调 `drop_run_limits`**（清理只在 `_finish`/`_fail_run`）。每次探测失败 run 累积 `_run_limits[run_id]` 与 KeyedLimiter 桶 → 进程内内存无界增长（接入持续探测不达标 agent 即触发）。

**改动**：probe 失败 return 前补 `self.drop_run_limits(run_id)` + `self._cancel.pop(run_id, None)`（顺带清标志）。实施时按 `_finish`（:492 附近）的调用签名对齐。

**测试**：orchestrator 单测——probe 失败后 `_run_limits` 不含该 run。

### C3 generation 参数是死代码

**证据**：`orchestrator.py:199` 局部 `generation`、`:217-219` 与 `:227-228` 传入 `_run_one`，函数体从不使用；`runs.py:342` cancel 自增仅写 DB 无消费。

**改动**：删除 `_run_one` 的 `generation` 形参（:199 局部变量、:217-219 传参、:227-228 形参共 3 处）；`runs.py:342` 自增保留（DB 列写值无害，删列属超范围）。

**测试**：无新增；既有 orchestrator 测试编译回归。

---

## 批次 B：账号安全 + 评分完整性

### B2 executor 重试语义矛盾：HTTP 4xx 业务错被重试（已核验）

**证据**：`executor.py:29` 注释声明「HTTP 4xx 业务错不重试」；`:31` `RETRYABLE_ERRORS` 含 `ERROR_HTTP`；`:90-92/:108-109` 任意非 200（含 4xx）统一归 `ERROR_HTTP` → 4xx 业务错（400 参数错/401 未授权/404 不存在）被指数退避重试，重试无益且放大无效请求；orchestrator 熔断逻辑 `error_type in RETRYABLE_ERRORS` 也会把 4xx 累计为熔断次数。

**改动**：新增 `ERROR_HTTP_CLIENT = "http_client_error"`（HTTP 4xx 业务错，**不在** RETRYABLE_ERRORS）；`:92/:109` 非 200 分流——`400 <= status_code < 500` → `ERROR_HTTP_CLIENT`（不重试、不熔断，对齐 `orchestrator.py:253-255`「契约类错误是 agent bug，不熔断」语义），3xx/5xx 保持 `ERROR_HTTP`（可重试、熔断累计）。前端 Dashboard.vue 直接展示 error_type 自由文本，无白名单，新增类型不 break。

**测试**：`test_executor.py` `test_sync_http_error`（403）断言改为 `ERROR_HTTP_CLIENT`；新增 `test_sync_http_5xx_retryable`（503）→ `ERROR_HTTP`。SSE 500 既有用例（`test_http_non_200`）不变。

### B1 admin 重置密码清掉了「必须改密」标志（已核验）

**证据**：`users.py:90` admin 改密路径 `password_changed_at = datetime.now(timezone.utc)`；强制改密机制 = `password_changed_at is None`（`deps.py:59`、`auth.py:94`）；`create_user`（:64-68）保持 None。admin 重置后用户**不再被强制改密**，用 admin 已知密码永久登录。

**改动（[评审修订] M2）**：① `users.py:90` → `user.password_changed_at = None`（对齐 create_user，重置即进首登强制改密）；② **同批撤销该用户全部 RefreshToken**（复刻 `change_password` 的撤销逻辑 `auth.py:241-245`）——否则攻击者旧 refresh 族仍有效可换 token 进系统，B1 不闭环。

**测试**：`test_auth_guard.py` +1——admin 重置密码后 password_changed_at is None。

> ⚠️ **待确认（产品意图）**：admin 重置密码语义 = 「直接设定新密码（无需再改）」还是「给临时密码首登必须改」？安全一致性 + create_user 行为支持后者（None）。

### B6 refresh token 并发下可绕过复用检测（已核验）

**证据**：`auth.py:152` 读 RefreshToken **无 `FOR UPDATE`**；`:161` 检查 revoked + `:191` 置位非原子。同 token 并发请求都通过「未撤销」分支 → 各拿一个新 refresh（同族），复用检测不触发、破坏「每族至多一个有效 token」不变量。

**改动**：`:152` `select(...).where(...)` → `.with_for_update()`（锁定读，对齐 runs.py `agent_mutex` P2-D2 先例）。并发下第二个请求醒来读到已 revoked → 命中 :161 复用检测 → 整族撤销。

**测试**：`tests/integration/test_integration_auth.py` +1——`asyncio.gather` 两并发 refresh 同 token：恰一个成功，另一个 401 整族撤销。

**风险**：锁定读在 REPEATABLE READ 读最新已提交（与 P2-D2 同原理）。

### B4 weights/targets 接口无取值范围与维度合法性校验（已核验）

**证据**：`agents.py:72-77` `WeightsBody.weights: dict` / `TargetsBody.target_scores: dict` 无 Field 约束；`:356-378` / `:389-409` set 时直接 upsert 不校验 key 与值域。负权重 → score_total 失真；四维全 0 → case 全 na；未知维度入库但评分忽略。评分层有零除守卫（`scorer.py:47-51,125`）不会崩，但合法性问题被静默吞掉。

**改动（[评审修订] L1）**：`set_agent_weights`/`set_interface_weights` 循环内校验：`dim in ACCURACY_DIMENSIONS`（`constants.py:16`，scorer 只消费 accuracy 维权重，非 accuracy 现状即被静默忽略）+ `0 <= w <= 100`（**`try/except (TypeError, ValueError)` 包 `float(w)` 防未捕获 500**）；非法 → `ApiError(E_VALIDATION, "非法维度/权重，合法维度: ...；权重接受 0-100 或 0-1 任一量纲", 400)`。targets 同理（`target ∈ [0,100]`；20 倍数校验已在 set_targets，不重复）。

**测试**：`test_agents.py` +3——非法 dim 400、负权重 400、合法部分维度通过。

> ⚠️ **待确认**：weights 允许只设部分维度（现有用法即部分 upsert）——校验只拦「key 非法 + 值越界」，不强制全维度。

---

## 批次 C：配置/死代码/一致性清理

### C1 五个注册但永不消费的死配置（已核验 grep：全仓仅 seed 命中）

**证据**：`seed.py:357 contract_check_timeout`、`:359 sse_idle_timeout`、`:371 error_rate_block`、`:383 breaker_half_open_probe`、`:385 retry_backoff_max`。scope=run 且被 `_snapshot_run_config`（`runs.py:70-73`）快照进 run_config JSON 增重。

**改动（[评审修订] H3/L4，跨层原子变更）**：
1. `seed.py` 删 5 条注册；
2. **`frontend/src/constants/configMeta.js` 删对应 5 个 key**（第 10/11/17/22/23 行）——`test_frontend_meta_sync.py:32-40` 断言 configMeta.js 与 DEFAULT_SYSTEM_CONFIG **双向严格相等**，只删 seed 必红 CI；
3. 存量库一次 `DELETE FROM system_config WHERE \`key\` IN (...)`（参照 P2-8 先例）。

机制澄清（L4）：删 seed 后 `put_global_config` 因 `DEFAULT_SYSTEM_CONFIG.get(key).get("meta") is None` 被 `sysconfig_schema.py:15-16` 拒改 → key「冻结」；运维 DELETE 才是清 `GET /config/global` 与 run_config 快照残留。**backend seed + frontend configMeta + 运维 DELETE 必须同一变更集**。

**测试**：`test_frontend_meta_sync.py`（双向相等，删 key 自动适配）+ `test_seed.py`/`test_sysconfig_schema.py` 回归（`verify_fresh_start.py` 从 seed 源码动态提取 key 数，自动适配）。

> ⚠️ **待确认**：删除 vs 保留（error_rate_block 名似「错误率熔断」规划）。建议删——配置即真相，前例已确立。

### C2 meta.py 审计写 `"-"` target_id

**证据**：`meta.py:33` `write_audit(..., "eval_run", "-", ...)`；对照 `scanner.py:132-135` 同 `data_cleanup` 已修 `None`（2026-08-20 因数值比较 CAST 1292 修复）。`target_id` 是 String(64) 当前不崩，但属同类隐患。

**改动**：`meta.py:33` `"-"` → `None`。

### C5 create_price 时区 aware/naive 混用

**证据**：`config.py:80` 默认 `datetime.now(timezone.utc)`（aware）vs `fromisoformat(body.effective_from)` 可能 naive；全库 naive UTC 约定（`orchestrator.py:48-50`）。

**改动**：统一 naive UTC——默认 `datetime.utcnow()`（或 effective_from 补 UTC 后转 naive）。

### C6 list_runs 负 limit 未钳下界

**证据**：`runs.py:282` `stmt.limit(min(limit, 200))`——`offset` 有 `max(offset, 0)`，`limit` 无下界。`limit=-50` → `LIMIT -50`（MySQL 行为不定）。

**改动**：`stmt.limit(max(0, min(limit, 200)))`。

---

## 批次 D：并发健壮性 + 性能

### B5 导出一次性 token 下载 TOCTOU + 导出文件永不清盘（已核验）

**证据**：`exports.py:152` 读 ExportToken 无 FOR UPDATE；`:156` 检查 used + `:166` 置位非原子（并发两次下载都过校验）；`:125-126` 文件写 EXPORT_DIR 后无清理（`run_cleanup` 只清 DB 不清文件）→ 磁盘增长。

**改动**：① `:152` 加 `.with_for_update()`（串行化并发下载，与 B6 同款）；② download **与 create_export** 时惰性清理 EXPORT_DIR 中 mtime 超过 `EXPORT_TTL_HOURS` 的文件（[评审修订] L5：create 也扫，低流量系统不再等下次下载才触发；低频、不阻塞主流程）。

**测试**：`test_exports.py` +2——并发下载仅一次成功；过期文件被清理。

### B7 dashboard 聚合 N+1 + gate 无界加载（已核验）

**证据**：`dashboard.py:134-153` `_agent_dim_series` 对每个 run 单独 `select(EvalResult)`（N+1，run 数千时显著变慢）；`:42-43` gate 全量加载所有非 held_out run 无 limit。

**改动（[评审修订] M3，⚠️3 已核实）**：① `_agent_dim_series` 改一次 **列投影** `select(EvalResult.run_id, EvalResult.score_per_dimension).where(EvalResult.run_id.in_(run_ids))` 批量取（**不拉 MEDIUMTEXT answer/reasoning**，否则峰值内存反而更高），Python 层按 run_id 分组；② gate 的 run 查询加 `status.in_(TERMINAL_STATUS)` 终态过滤——**已核实为纯性能下推、语义零变化**：`build_gate_cards`（dashboard_rules.py:28）本就在 Python 层按 `TERMINAL_STATUS={completed, partial_failed, scoring_failed, timeout, cancelled}`（不含 scoring）过滤，SQL 侧下推不破坏「最新版本门禁分」。不加时间窗/limit。

**测试**：`test_dashboard.py` +1——mock DB 捕获 `_agent_dim_series` 的 stmt 含 `run_id.in_`。

### C4 judge worker 捕获过宽（已核验）

**证据**：`worker.py:185` 与 `:203` 均 `except (JudgeError, Exception)`——`aggregate_verdicts`、DB 错误等真实 bug 被当「judge 失败」重试/标 failed，掩盖缺陷。

**改动（[评审修订] H4/M4）**：内层 `:185` 收窄为 `except JudgeError`（client.judge 判分失败）——**实施时先确认 JudgeClient 抛错边界**（`core/http.py`：httpx 超时/连接错若裸抛则需单列 httpx 异常，若已包 JudgeError 则无需）；外层 `:203` 保留宽捕获但改 `logger.exception`（非 JudgeError 完整栈可见，attempts 终态保护不破坏）。**注**：`aggregate_verdicts`（:190）在外层 try 内（:145 起）、内层（:167-185）之外——内层收窄不影响它的错误处理。

**测试**：`test_judge.py` +1——`aggregate_verdicts` 抛非 JudgeError → 走 `logger.exception`（完整栈可见）且 `t.attempts` 按现有逻辑推进（断言「不静默当单次判分失败」，而非「不重试」）。

### C7 sse_parser `_buf` 无界增长

**证据**：`sse_parser.py:37-44` 无换行数据持续累积 `_buf`；ResultAssembler answer/reasoning 同理（受 MEDIUMTEXT 16MB 约束）。

**改动**：`_buf` 累积上限（如 64KB，超限 `ERROR_CONTRACT`）；answer/reasoning 累积上限（16MB 保护，超限报契约错误）。

**测试**：`test_sse_parser.py` +1——超长无换行块报契约错误。

### C8 超时封顶可误杀大 run

**证据**：`orchestrator.py:62` `RUN_TIMEOUT_CAP_S=7200`；`:65-79` estimate_run_timeout 封顶；`seed.py:364` `perf_repeat_count` meta max=100。大 suite × 高 repeat 估算被封顶 → scanner 按 hard_deadline 误杀合法 run。

**改动**（最小，不改行为）：`:79` 封顶命中时（`exec_s + judge_s > cap`）`logger.warning`「估算超封顶，run 可能被 scanner 回收，请显式配置 run_timeout 或调小 perf_repeat_count」。

**测试**：estimate_run_timeout 单测 +1——超封顶返回 cap 且打 warning。

> ⚠️ **待确认**：`perf_repeat_count` meta max 100→20 收紧（改热改校验与 seed 契约）vs 仅 warning。建议仅 warning，避免行为变更。

---

## 改动文件清单

| 文件 | 改动项 |
|---|---|
| `backend/app/runner/orchestrator.py` | A1（原子条件更新状态初始化 + 删 pop + 取消检查前置 set_run_limits 之前 + _finish/_fail_run 补 pop + 类型注解）、B3（probe 失败 drop_run_limits）、C3（删 generation 传参）、C8（封顶 warning） |
| `backend/app/api/runs.py` | C6（limit 钳制） |
| `backend/app/api/users.py` | B1（password_changed_at=None） |
| `backend/app/api/auth.py` | B6（with_for_update） |
| `backend/app/api/agents.py` | B4（维度/值域校验） |
| `backend/app/api/exports.py` | B5（with_for_update + 惰性清理） |
| `backend/app/api/dashboard.py` | B7（N+1 批量 + gate 终态过滤） |
| `backend/app/api/meta.py` | C2（"-"→None） |
| `backend/app/api/config.py` | C5（naive UTC） |
| `backend/app/seed.py` | C1（删 5 死配置） |
| `backend/app/judge/worker.py` | C4（内层异常收窄 + 外层 logger.exception） |
| `backend/app/core/sse_parser.py` | C7（buf/answer 上限） |
| `frontend/src/constants/configMeta.js` | C1（删 5 个死配置 key，与 seed 同批） |
| 测试 | test_run_lifecycle（新）、test_auth +1、test_agents +3、test_exports +2、test_dashboard +1、test_sse_parser +1、test_judge +1、test_seed/test_sysconfig_schema 适配、estimate_run_timeout +1 |
| `上线待办.md` | 批次 A-D 回填 |

## §6 待确认点汇总（2026-08-31 用户已全部定夺，均按评审推荐）

| # | 待确认 | 定夺结果 | 依据 |
|---|---|---|---|
| 1 | **B1** admin 重置密码是否强制改密 | ✅ 置 `None`（强制改密）**+ 同批撤销该用户 refresh token（M2）** | 与 create_user（:64-68 保持 None）、`deps.py:59`、`auth.py:94` 口径一致；「用 admin 已知密码永久登录」正是 B1 要修的安全洞 |
| 2 | **C1** 五个死配置删 vs 留 | ✅ **删除**（P2-8 先例「配置即真相」）| 不删则配置中心展示无效项、run_config 快照增重；必须与 configMeta + 运维 DELETE 同批 |
| 3 | **B7** gate 是否加终态过滤 | ✅ **直接做，无风险**（已核实 Python 层本就在过滤，SQL 下推纯性能优化）| 已从方案去掉「评估中」标注 |
| 4 | **C8** perf_repeat_count 上限收紧 vs 仅 warning | ✅ **仅 warning**（改 meta max 100→20 会 400 掉存量 21-100 合法配置）| warning 文案给可执行兜底：显式配 `run_timeout` 或调小 `perf_repeat_count` |

## 验证

1. 分批次实施 + 各批单测（批次 A 含最高危 A1，先做先回归）
2. 全量回归：`cd backend && python -m pytest -p no:html tests/ -q`（当前 699 passed / 92 skipped 基线）
3. C1 存量 DELETE 为运维（上线目标库一次），不在本次动库
4. 无 schema 变更（无 alembic 迁移）

## 风险

- **A1**：守卫放错位置可能误拦正常 pending run——实施时在 :186 前置 running 前严格「pending 才可进」，回归重点覆盖正常流程。
- **B6/B5**：锁定读增加 refresh/download 串行化，单 token 高频场景影响可忽略。
- **B7**：gate 终态过滤若漏「正在 scoring 的 run 仍应参与」语义——终态过滤只排除 pending/running，scoring 属终态前？`TERMINAL_STATUS` 需核实含 scoring 与否（实施时核对，scoring 是终态前的判分态，若 excluded 会漏判分中 run 的门禁卡——需确认 build_gate_cards 对 scoring run 的依赖）。
- **C1**：删除配置 key 后存量 run_config 快照含旧 key 无害（历史数据）；配置中心不可再改这些 key（预期内）。
