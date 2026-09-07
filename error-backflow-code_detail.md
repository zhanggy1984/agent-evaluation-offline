# 线上观测平台 error 回流 —— offline 侧开发就绪编码详设（error-backflow code_detail）

> **定位**：Task #4 批 1 + 批 2 的**编码级详设**（= `backend/` 平台仓改造的实施规格）。**叠加基线**：批 1 = `error-backflow-phase1.md` **v0.2.2**；批 2 = `error-backflow-phase2.md` **v0.7.2**；消费契约 = online `solution_detail.md` **v1.9** / `solution.md` **v3.5.9** / `task.md` 环 0（R-13~R-24 实现清单登记，L49）。
>
> **本稿承载范围**（一次性对齐终态语义，不再区隔批 1/批 2 时序——见 §1.3 实施顺序门）：error 回流整链在 offline 仓的代码落点 = ① DDL/迁移 ② 收单链路 pull_loop + inbox ③ error suite / error case 装载 / 断言固化（含 backfill） ④ error_regression run 自动触发（§5.2 consumed 锚 + R-3 差集对账） ⑤ error run 执行语义（orchestrator error 分支 / error 并发槽池 §8.3（limiter 共享 per-agent 层，M5 前置）/ 独立收尾 / scanner 短路，含 R-22 收尾 error_type 钉死） ⑥ 判定器 executor 复现 + verifier no_fallback（run_assertions 新调用点 + **R-12 text.py 空答 fail 修补——R-20 pre-scan 报告门禁先行**） ⑦ 平台只读面（R-4 cap 溢出列表 / R-5 agent 已见版本 / na_case+per-case error_type） ⑧ 统计/清理/读面隔离收口。**实施护栏**：R-19 code 一致性单测（executor error_type 常量 ⊆ phase2 §6.4 标注清单）+ R-22 收尾回填 error_type 单测（task 环 0 L49 登记）。
>
> **实施前置（关键）**：截至本稿（2026-09-07）`backend/` 全仓**零 error-backflow 落地**——`case_type / is_error_suite / error_backflow_inbox / error_regression / trigger_signal_id / excluded_case_ids / no_fallback` 均无标识符；`EvalRun.trigger_type` = `("manual","held_out")`；`TestCase.expected/assertions/metrics` 仍 `nullable=False`；无平台只读面。**本文 §2 的"现码事实"全部来自原文逐行核读（行号锚点），实施时以当时 HEAD 为准再对一遍**。
>
> **状态**：**v0.3 草稿**（2026-09-07：v0.1 首稿 → v0.2 终审拍板 → v0.3 并发槽池裁决 A：§8.3 由 v0.2「接受叠加 ≤4 路」改为 **M5 前置 = limiter 进程级共享 per-agent 层**，向 phase2 §6.2 step4 R2 权威对齐；连带 M5 落点 / §2.5 结论 3 / §8.2 / §12 #23 / §13 行 3）。基线文档引用：文中「批 1 §x.y / 批 2 §x.y」= phase1 v0.2.2 / phase2 v0.7.2 章节号（phase1 引用其 v0.2.2 现行语义、phase2 引用其 v0.7.2 现行语义，含各自 v0.x 修订注记覆盖后的终态）；「detail §x.y」= solution_detail v1.9。双端未 commit（CLAUDE.md 禁忌），待用户终审。
>
> **本文档为非权威工作稿 → 权威口径不变**：所有语义以 phase1 v0.2.2 / phase2 v0.7.2 / detail v1.9 / solution v3.5.9 四份为最终裁判，本稿仅做**代码级落点转译**；若本文与权威文档冲突，以权威文档为准并回改本文。

---

## 0. 读前须知（本文如何被使用）

1. **读者 = backend 实施工程师**。每节给出：目标文件（现码行号锚点）→ 改动语义（为什么）→ 函数/类/迁移签名与字段级规格 → 测试护栏。不重述权威文档已有的大段机制推导，只做落点转译与缺口补齐。
2. **三份现码锚点报告已核读**（本稿 §2 引用）：data/API 层、执行层、case_loader/config 均按原文行号核过。`executor.py` 错误常量与 `RETRYABLE_ERRORS`、`scorer.py` 断言调用点、`limiter.py` 信号量作用域等**事实性结论**（如 "`run_assertions` 在 `assertions/run.py:12` 而非 scorer"）直接内化为本文落点前提，不再逐条标出处。
3. **术语速查**：`error suite` = `TestSuite.is_error_suite=true`；`error case` = `TestCase.case_type='regression_error'`；`error run` = `trigger_type='error_regression'` 的 EvalRun；`error_type` = EvalResult 列的 na 分类值（§3.1 权威字面量域）；`na` = 「未取得可判终值」运行期缺判定态（五源矩阵 §3.2）。
4. **na 载体全链口径（R-19/R-22 护栏的判别物）**：`EvalResult.error_type` 是**分类枚举**（String(32)，非实文），进平台只读面（na case 必带）；**字面量必须与 `executor.py` 常量逐字相等**（§3.1），否则 online 消费侧把 case 级错误当未知类型默认从严触发 unclean_run 风暴。
5. **门禁先行项**：R-12（`KeywordNotContainsOp` 空答 fail）**不得先于 R-20 pre-scan 报告上线**（§9.4）；pre-scan 报告产物入本仓，owner = offline 实施。

---

## 1. 范围与实施顺序

### 1.1 交付面（按代码文件归组）

| # | 交付 | 主落点（目标文件） | 权威规格 |
|---|---|---|---|
| M1 | 数据模型扩列 + 迁移 | `models/case.py`、`models/run.py`、新增 `models/error_backflow_inbox.py`、`alembic/versions/*`（3~4 个新迁移） | 批 1 §5.1/§5.2/§5.3 + 批 2 §6.1（trigger_signal_id + excluded_case_ids，R-4） |
| M2 | 收单链路 pull_loop + inbox 状态机 + ack/对账/R-8 节流 + error suite upsert + error case 装载 | 新增 `runner/pull_loop.py`、`core/error_payload.py`、inbox DAO（如 `repositories/inbox.py` 或并入 pull_loop）、`main.py` startup | 批 1 §2/§3/§5.2/§6 + R-8（phase2 §2.3 v0.7 注④ / phase1 §5.2/§6.5 修订） |
| M3 | error case 断言固化 + 存量 backfill + 词级净化 + keyword_not_contains 发布登记 | `pull_loop` 建 case 路径、`runner/backfill.py`（或并入补偿对账）、`core/constants.py`、`seed.py` | 批 2 §7.3/§7.4 + 批 1 §5.1 |
| M4 | error_regression run 自动触发 + 补偿对账（R-3 差集）+ consumed 锚 | 新增 `runner/auto_schedule.py`（maybe_auto_schedule + 差集对账）、`api/runs.py`（version 共享域校验、互斥计数排除）、`runner/orchestrator.py`（信号终态挂接） | 批 2 §5 + R-3（phase2 §5.3 v0.7 块） |
| M5 | error run 执行语义 | `runner/orchestrator.py`（error 分支、独立收尾）、`core/limiter.py`（**进程级共享 per-agent 层**，M5 前置 §8.3）、`runner/case_loader.py`（error 分支）、`runner/scanner.py`（短路 + 替代收尾 + pending 回收 + R-22） | 批 2 §2.2/§6（R2 共享槽池）+ D8 |
| M6 | 判定器 executor 复现 + verifier no_fallback + R-12 | `runner/` error 分支逐 case 调用 `executor.execute_case` + `run_assertions`（`assertions/run.py`）；**R-12 改 `assertions/ops/text.py`** | 批 2 §7.1/§7.2 + R-12/R-20 |
| M7 | 平台只读面 `/api/v1/runs` + results + versions + excluded_case_ids | 新增 `api/platform_runs.py`、`core/security.py` 服务 token helper、`main.py` 挂载 | 批 1 §8 + 批 2 §9.3 + R-4/R-5（phase2 §9.3 v0.7 注 / detail §8.7） |
| M8 | 统计/占位/人类读面隔离收口 + cleanup 豁免 | `api/dashboard.py`、`api/runs.py`、`runner/cleanup_rules.py`、人类 run 读面过滤 | 批 1 §5.3/§5.4 + 批 2 §9.1/§9.2/§9.4/§6.5 |
| M9 | 测试 + 护栏 + 环 1/环 2 桩 | `backend/tests/*`、`backend/tests/integration/*` | 批 1 §12 + 批 2 §11 + R-19/R-22 护栏（task 环 0 L49） |

### 1.2 明确不做（本稿不落代码，二期/在线侧）

- online 侧全部实现清单（R-13 同簇刷新复制、R-16 excluded 自动消费、R-21 root_late_complement、R-24 requeue guard）——归 online 仓；本稿仅保证 offline 只读面字段位与之对齐（M7）。
- 传输 Kafka（HTTP pull 保持）；error case/inbox 长期驻留归档策略；`inconclusive` 三值判定；离线触发覆写（false-fixed 收敛 reentry/admin reopen）；跨端 deactivate / case 归档闭环。
- 前端只读标注（error suite 只读样式）属批 1 收尾小改，归前端仓，本稿不落。
- error run 无信号自动重跑（partial_failed/timeout 带退避自动补跑）留二期（批 2 §12 开放项）。

### 1.3 实施顺序门（工程依赖）

> 说明：phase2 v0.7.2 前置依赖原为「批 1 代码 → 批 2 代码」。**本稿一次性对齐终态语义实施**（批 1/批 2 语义在 M1~M9 中合并落），不产「批 1-only 中间态」；但仍保留 gate 顺序，保证**单测/集成分步全绿**。

| 门 | 完成标志 | 覆盖交付 |
|---|---|---|
| G0 | R-20 pre-scan 报告产出（§9.4） | M6（R-12 text.py 改动的前置，**未过 G0 不动 text.py**） |
| G1 | 迁移可空跑/回滚（upgrade → downgrade）全绿 + 三新模型 ORM 可建表 | M1 |
| G2 | `validate_envelope`/映射/inbox 状态机/守卫纯函数单测全绿（不触库） | M2/M3 纯函数层 |
| G3 | 激活器 pull_loop 打通（fake-online stub）→ 建 error suite/error case + ack 闭环 | M2/M3 集成 |
| G4 | error run 触发 + 执行 + 收尾 + 判定单测全绿（mock execute_case） | M4/M5/M6 逻辑层 |
| G5 | 环 2 双端走查（error-backflow-phase2 §11.2 场景 1-22 offline 侧真跑 + online fake） | M2~M8 |
| G6 | 护栏核对：R-19 code 一致性单测 + R-22 收尾 error_type 单测并入 CI | M9 |

---

## 2. 现码事实基线（锚点，2026-09-07 核读）

> 实施前重对 HEAD；行号为核读时点值。本节只为本文 §4~§10 提供落点定位，非权威方案。

### 2.1 模型层（`backend/app/models/`）

- **run.py**：`RUN_STATUS` L13 = `("pending","running","scoring","scoring_failed","completed","partial_failed","timeout","cancelled")`；`TRIGGER_TYPE` L14 = `("manual","held_out")`（**需扩 `error_regression`**）；`PASS_FAIL` L15 = `("pass","fail","error","na")`。`EvalRun` L20-60：`agent_id/suite_id/version String(64)/trigger_type Enum/status/lease_until/hard_deadline/total_case/pass_case/fail_case/error_case/na_case/agent_score/judge_incomplete/pinned/env_snapshot/run_config JSON/case_ids JSON`。**缺**：`error_regression` 枚举值、`trigger_signal_id`、`excluded_case_ids`（JSON，R-4）。`EvalResult` L63-97：`uk_result` L67 UniqueConstraint(run_id,case_id)；`error_type String(32)` L89（**已存在，非 ENUM，可装新错误码 ≤32**）；`error_detail Text` L90；`assertion_results/judge_results JSON`。索引 `idx_result_run_pf(run_id,pass_fail)` L70。
- **case.py**：`TestSuite` L11-18（同文件，**本仓无 models/test_suite.py**）；`TestCase` L21-59：`expected/assertions/metrics` 均 `nullable=False`（**需 ALTER 放宽**）、`status Enum(draft,active,invalidated)`、无 `case_type/payload_id/backflow_envelope`。`CaseVersion` L62-75：`content_hash CHAR(64)` + `snapshot JSON`（无独立 content 列）。
- **alembic head** = `b1a2c3d4e5f6`（add_updated_at_onupdate）；迁移文件命名 = 16hex_rev + snake（见 `backend/alembic/versions/`）。无 error-backflow 迁移。

### 2.2 API 层（`backend/app/api/`）

- **runs.py**：`_SEMVER` L42、`_MUTEX_STATUS=("pending","running")` L46、`RunCreate` L55-62（`trigger_type: Literal["manual","held_out"]="manual"` L60、`case_ids` L62，**无 pinned 参数**——pinned 仅 model 默认 False L51）、`_validate_case_ids` L93-112、`_ensure_suite_has_cases` L115-132、`_run_out` 白名单 L200-221（已含 na_case；只加不改前提成立）、`create_run` L224-273（semver 校验 L230-231、互斥 409 L249-255、lease 90s L264）、`list_runs` L276、`rerun_run` L354-396（复制 src.trigger_type L384、无 trigger 白名单检查）、`run_results` L402。互斥扫描第二处在 rerun 内 L375-381。
- **dashboard.py**：gate/trend/compare/perf/cost/baseline 六处聚合仅排除 held_out（现码行号 43/60/85-87/174/196/254）；`_agent_dim_series` L136-161。
- **annotations.py / scaffold.py**：`add_annotation`、`add_case_scenes` 需对 error case 403。
- **security.py**（93 行）：`create_access_token/decode_access_token`（人类 access token）；无 service token helper；无 platform m2m secret。
- **config.py**：只放 secrets + 基础设置；业务参数在 `seed.py` `DEFAULT_SYSTEM_CONFIG` L345-422（per_agent_concurrency=3 L351、case_timeout=120 L353、breaker_failure_threshold=5 L376、max_retries=1 L382、max_active_runs_per_agent=1 L389、heartbeat_interval=30 L391）。

### 2.3 执行层（`backend/app/runner/`）

- **orchestrator.py**（588 行）：`_run` L135-253（现早退点：run None 139-141 / agent None 或禁用 179-184 / 无 cases 185-191 / 状态非 pending 207-209 / 已取消 215-217 / probe 失败 238-244；`run_config` 冻结读 147-177）；`_run_one` L255-291（limiter acquire → breaker → `_execute_with_retry` → release）；`_execute_with_retry` L335-383（含 `agent_mutex(agent.id)` 只串行 reset/seed L360-362）；`_probe_before_run` L293-333；`_call_once` L385-396（指数退避 `0.5*2**i` 封顶 10s，`outcome.error_type not in RETRYABLE_ERRORS or not retryable` 判重试）；`_save_result` L399-446（默认 pass_fail='error' 路径需 error 分支避开）；`_ensure_case_version` L448-474（惰性建快照，快照内容 L458-461 = {input,input_turns,file_ref,expected,assertions,metrics}）；`_heartbeat` L496-512；`_finish` L515-567（drop_run_limits L520 / FOR UPDATE 锁 L527 / external_terminal L531 / 对账条件 total<total_case L535 / 循环 `_load_run_cases` L536 / **对账直插点 L541-545** `pass_fail="error", error_type="cancelled"` / 统计 L548-554 / 状态判定 L557-559 / commit L560 / **scorer 触发 L566-567 `if final_status==SCORING: await score_run(run_id)`**）；`_fail_run` L569-585。模块单例 L588 `orchestrator = RunOrchestrator()`。
- **scanner.py**（171 行）：`_SCAN_INTERVAL=15/_LEASE_GRACE=10/_CLEANUP_INTERVAL=3600` L27-29；`_reap_one_pass` L36-119（① lease 超时回收 L44-48 含 pending ② hard_deadline L50-54 ③ scoring 超时 L71-92 ④ scoring 卡死 L96-106；条件 UPDATE L59-66 rowcount==1 才记 reaped；`score_run_salvage` 调用点 L112/L115、`score_run` L118）；`scanner_loop` L141-170（GET_LOCK('scanner_loop',0) L155 单例 → RELEASE_LOCK L167）。
- **case_loader.py**：`_load_run_cases` L20 签名 `(db, run) -> list[TestCase]`；过滤 L26-32 `suite_id==run.suite_id AND status=="active" AND is_held_out==(trigger=="held_out")` + `case_ids` 子集叠加；无 case_type 谓词、无 ORDER BY。四处调用：orchestrator `_run` L146 / `_probe_before_run` L304 / `_finish` L536、scorer salvage 内联延迟 import L385。
- **cleanup_rules.py**：pinned 排除 L38-39 `if r.pinned: continue`；`_TERMINAL` L23 = (completed,partial_failed,scoring_failed,timeout,cancelled)；retain 从 system_config `retain_runs` 读缺省 50（L61-65）；`run_cleanup` L79-104 分批 CASCADE。

### 2.4 判定/断言/并发基建

- **executor.py**（215 行）：错误常量全集 L22-31 = `no_done L22 / no_usage L23 / timeout L24 / pool_error L25 / http_error L26 / http_client_error L27 / connect_error L28 / sse_parse_error L29 / body_too_large L30 / contract_error L31`；**`RETRYABLE_ERRORS` L38 = {timeout, connect_error, sse_parse_error, http_error, no_done}**；非 RETRYABLE = {pool_error, http_client_error, body_too_large, no_usage, contract_error}。`CaseOutcome` L50-60（`ok` property L58-60 = `error_type is None`）；`execute_case` L67-72 `(adapter, client, case, timeout_s=120.0) -> CaseOutcome`。重试在 orchestrator（`_call_once`），executor 单次。
- **assertions/ops/text.py**：`KeywordNotContainsOp` L33-62；run() L46-62——path 缺失/None/非 str → False（fail）；**空 str/纯空白 → hits 空 → ok=True → 判 pass（R-12 修补点）**；args：`keywords` 必填非空（L47-49）、`path` 默认 "answer"（L50）、`match` 默认 all（L58）。
- **assertions/ops/__init__.py**：`KeywordNotContainsOp` 已在 L11 注册（内存注册即可执行，无需 DB 行）。
- **assertions/run.py**：`run_assertions` L12-40，签名 `(unified: dict, assertion_defs: list[dict]) -> list[dict]`，**吃 unified dict 不吃 CaseOutcome**。
- **assertions/path.py**：`resolve(unified, path)` 取目标文本。
- **core/constants.py**：`_ASSERTION_OPS` L36-47（10 items，**无 KeywordNotContainsOp**）；`ALLOWED_CLASS_PATHS` L59；`ASSERTION_OP_CLASS` L62-73（无 keyword_not_contains 键）。
- **scorer.py**：`_enabled_dims` L172-177（metrics 空 → 全 accuracy dims 启用）；`_score_executed_results` L284-358（L301-303 `if r.pass_fail == "error": continue`、**L306-307 `run_assertions(_unified(r), snapshot.get("assertions") or [])`**、L335 评分异常、L354 partial_failed/completed）；`score_run_salvage` L361-421；`_ensure_salvage_version` L424-445；`_unified` L448-457。
- **core/lock.py**：`agent_mutex` L20-36（SELECT Agent FOR UPDATE；create/rerun 建单互斥 + orchestrator reset 用；不包执行期）。
- **core/limiter.py**：`WeightedLimiter` L11-32（`_global=Semaphore(global_limit)` + `_per_agent: dict[str, Semaphore]` 惰性建，acquire L26-28 先 agent 后 global）；`KeyedLimiter` L35-67（per-run 桶 `_buckets: dict[int, WeightedLimiter]`，set_run L49-52 / drop_run L54-56 / acquire L63-64 / 未 set_run 走 `_default` L58-61）。
- **core/case_rules.py** L26：`check_owner_denies_golden`（最接近 assert_normal_case_golden_fields 的邻居）。
- **core/db.py**：真 MySQL（mysql+aiomysql），无 sqlite（config docstring "sqlite 内存" 为遗留描述）。
- 测试基建：单测纯函数/依赖注入不触库（httpx.MockTransport 例：test_executor.py L98）；集成测试 `backend/tests/integration/`（真 MySQL + `RUN_INTEGRATION=1`，conftest 提供 db/env fixture + `_reset_orchestrator` autouse；helpers.py `make_agent/make_suite/make_case/make_run/create_chain`）；asyncio_util.py `run_in_isolated_loop`（禁裸 asyncio.run）。无 fake-online 服务（批 1 §12.2 需新建 stub 基建）。

### 2.5 关键事实结论（设计前提）

1. `_run` **无 trigger 分发/error 早退闸** → 需新增分支；`_finish` 对账直插 L541-545 与 scorer 触发 L566-567 是「error 不得走」的现成对照。
2. error run 所需六态 {pending,running,completed,partial_failed,timeout,cancelled} **全现成**，状态机不改枚举；只扩 `TRIGGER_TYPE`（models/run.py L14）与 `RunCreate.trigger_type` Literal（runs.py L60）→ 均为显式加入 `error_regression`。
3. per-agent 并发信号量现成但**嵌套在 per-run KeyedLimiter 桶内**（limiter.py L35-67），**桶间不共享**；phase2 权威要求「同 agent manual+error 共享池、并发不叠加」（§6.2 step4 R2）→ 需 **M5 前置 = KeyedLimiter 增进程级共享 per-agent 层**（§8.3，2026-09-07 裁决 A）；manual 无 error 并存行为等价、须回归。
4. `run_assertions` 吃 unified dict；scorer 对 `pass_fail=="error"` 行 skip → error 分支必须**自行组织断言执行**（verifier 是 run_assertions 新调用点）。
5. `_ensure_case_version` 惰性快照是 error 分支落 result 的既有路径（D6 维持惰性，不预建、不抽 helper）。
6. `case_ids` 子集语义：error run 创建时固化；读面反查 JSON_CONTAINS 精确命中，null 视为未记录。
---

## 3. 字面量域与状态机（R-19/R-22 护栏判别物）

> 本节的表是 **code 唯一字面量来源**。实现时 error_type 值一律引用 `executor.py` 常量 + 本表调度/断言值，**禁止内联字符串魔法值**（R-19）。na case 必带 error_type 的不变量由 §8.6/§8.7 收尾路径覆盖（R-22）。

### 3.1 error_type 权威字面量域（分类枚举全集，R-19）

**单一来源**：executor 十值直接引用 `backend/app/runner/executor.py` L22-31 常量（改名即联动测试红）；调度/断言值以本表为准集中定义于 `runner/error_codes.py`（见下）。

| 值（字面量） | 源 | 处置 | 影响域 | 是否 RETRYABLE |
|---|---|---|---|---|
| `timeout` | executor L24 | 重试 → 重试尽 → na | **case** | 是 |
| `connect_error` | executor L28 | 重试 → 重试尽 → na | **case** | 是 |
| `sse_parse_error` | executor L29 | 重试 → 重试尽 → na | **case** | 是 |
| `http_error` | executor L26 | 重试 → 重试尽 → na | **case** | 是 |
| `no_done` | executor L22 | 重试 → 重试尽 → na | **case** | 是 |
| `pool_error` | executor L25 | **不重试、不累计熔断 → 直接 na** | **环境** | 否 |
| `http_client_error` | executor L27 | 不重试 → 直接 na | **环境** | 否 |
| `body_too_large` | executor L30 | 不重试 → 直接 na | **case** | 否 |
| `no_usage` | executor L23 | 不重试 → 直接 na | **case** | 否 |
| `contract_error` | executor L31 | 不重试 → 直接 na | **case** | 否 |
| `circuit_open` | 调度层（error 复现独立 breaker，§8.4） | 显式归 na（**不落默认 'error' 行**） | **环境** | — |
| `interface_disabled` | 调度层 | 显式归 na | **环境** | — |
| `missing_assertion` | 判定素材缺失（§9.5 防御兜底） | 主路径加载过滤前置；运行期仅防御 na + 告警 | **case** | — |
| `assertion_shape` | 判定素材缺失（断言形态被 403 守卫篡改） | 同上 | **case** | — |
| `scheduler_unexecuted` | 调度回收（scanner 回收 / orchestrator cancel / run 级超时收尾三路径回填，**R-22**） | 替代收尾回填 na | **环境** | — |

- **影响域归类语义（R-2 / R-19，phase2 §6.4 权威）**：环境级 = {circuit_open, interface_disabled, scheduler_unexecuted, http_client_error, pool_error} → 整 run 复现会话不可靠 → run 内 pass 行不置 verify passed → online unclean_run 批；case 级 = 其余十值 → 只说明该 case 无证据、不牵连他 cluster；**无标注新 error_type 默认从严（环境级）**。
- **集中定义建议**（新文件 `backend/app/runner/error_codes.py`，防两处漂移）：
  - `RETRYABLE` = 引用 executor `RETRYABLE_ERRORS`（不重列字面量）；
  - `ENV_IMPACT = frozenset({"circuit_open","interface_disabled","scheduler_unexecuted","http_client_error","pool_error"})`（http_client_error/pool_error 来自 executor 常量引用）；
  - `CASE_IMPACT` = 其余全集（executor 十值 − {pool_error,http_client_error} + {missing_assertion,assertion_shape}）；
  - `BACKFILL_ERROR_TYPES = frozenset({"scheduler_unexecuted"})`（§8.6/§8.7 收尾回填专用，R-22）。
- **R-19 code 一致性单测**（G6 护栏，宿主单测 `tests/test_error_codes_consistency.py`）：
  1. `assert {e for e in executor 十值} == error_codes 域`（新增 executor 常量必进表 → 测试红促评审影响域）；
  2. `assert set(executor.py RETRYABLE_ERRORS) == {"timeout","connect_error","sse_parse_error","http_error","no_done"}`（防误改）；
  3. `assert ENV_IMPACT ⊆ phase2 §6.4 环境级清单`、`CASE_IMPACT ⊆ case 级清单`（清单作为注释常量内联，防漂移）；
  4. 全部字面量 `len ≤ 32`（EvalResult.error_type String(32) 约束）。

### 3.2 na 五源矩阵（§6.4 代码镜像）

| 源 | 触发 | 落 error_type | 主路径 |
|---|---|---|---|
| ① executor 技术失败·可重试 | RETRYABLE 十值中前 5 | orchestrator 重试尽 → na | §8.2 |
| ② executor 技术失败·非可重试 | pool_error 等 5 | 不重试直接 na | §8.2 |
| ③ 调度层未执行 | breaker 开 / interface disabled | 显式 na（拦截默认 'error' 落库） | §8.2 |
| ④ 判定素材缺失 | 空/坏断言 | 加载过滤前置（不进 run）；运行期防御 na | §8.1/§9.5 |
| ⑤ 调度回收·未执行 | 回收/中断时未完成 case | 替代收尾回填 na = `scheduler_unexecuted`（R-22） | §8.7 |

- 素材缺失源（④）经加载过滤前置后 **v1 不可达**（主路径），运行期仅防御兜底；na 源 ①②③⑤ 为 run 判定内可达。
- 边界（**非 na 源**）：词表净化（§6.1 拒空串/纯空白/超长词/大小写归一）与 R-12 空答 fail——空答**落 verifier fail 不落 na**（R-20 定性 = leakage 空话术复发证据，§9.4）。

### 3.3 run / pass_fail / trigger / inbox 状态机

- **EvalRun.status**：error run 取值域 = {pending, running, completed, partial_failed, timeout, cancelled}（**不经 scoring/scoring_failed**）；字面量与 RUN_STATUS 同值（枚举值已含）。
- **EvalResult.pass_fail**：error run 取值域 = {pass, fail, na}，**绝不落 'error'**（§8.2/§8.6 防击穿）。
- **EvalRun.trigger_type**：扩为 {manual, held_out, error_regression}（§4.4）。
- **version 字面量域校验**（批 2 §2.4，放宽 strict semver）：非空、≤64、无空白/控制字符；`^\d+\.\d+\.\d+$` 强校验在 create_run/rerun_run 校验点移除（`_SEMVER` L42 用法废止）。同 agent 乱序版本仅告警不拒。
- **inbox 两维状态机**（批 1 §5.2 不变 + R-8 修订）：

| 维度 | 值域 | 迁移 |
|---|---|---|
| `status`（本地决策） | `new` → `case_created`（记 case_id）/ `rejected`（记 reject_code） | 无 `acked` 值；复位规则见下注 ①（细则 §5.8/§5.9） |
| `ack_status`（远端闭环） | `none` / `pending` / `acked` / `blocked` | 闭环终态 = case_created+acked 或 rejected+acked |

- **复位规则（含 R-8 收窄）**：① 仅当 `ack_status ∈ {none, pending}`（首次/对账未闭环）或收到 requeue/内容刷新时才复位 `ack_status='none'` 重处理；② **cap_gap 自愈对已 acked 的 invalidated 闭环行不复位、不重发 invalidated**，进「等待接入」探测态（每小时本地重跑自检+映射；补齐 → 建 case + 单次 ack active；仍缺 → 静默等轮）——**勿按 phase1 v0.2.1 旧语义实现**（phase2 §2.3 v0.7 注④ / H7-7）。
- **非法组合**（单测断言）：case_created 无 case_id / rejected 无 reject_code / status=new 且 ack_status=acked。

### 3.4 收尾回填 error_type 不变量（R-22，护栏判别物）

- 收尾三路径（scanner 回收 §8.7 / orchestrator cancel / run 级超时收尾，后两者见 §8.6 行 3）对「实跑集内未终值 case」回填 na 一律 `error_type='scheduler_unexecuted'`；**timeout 只作 case 级 na 源 + run 终态字面量，不作回填 error_type**。
- **G6 护栏单测**：三条收尾路径各自触发后，断言 ① 每行回填 na 的 `error_type` 非空且 == `scheduler_unexecuted`；② **已 pass/fail 终值行不被改写**；③ 回填仅作用于「实跑集内尚无 EvalResult」的 case（§12.4）。

---

## 4. 数据模型与迁移（M1）

### 4.1 TestCase 扩列（`models/case.py`）

在 `TestCase`（L21-59 现列基础上）新增：

| 列 | 类型 | nullable | 默认 | 约束/语义 |
|---|---|---|---|---|
| `case_type` | VARCHAR(32) | True | NULL | online 白名单原字面量（v1 唯一值 = `'regression_error'`）；普通 case = NULL。**应用层白名单校验，非 DB ENUM** |
| `payload_id` | VARCHAR(64) | True | NULL | online uuid4 幂等键；普通 case = NULL；`UniqueConstraint("payload_id", name="uk_case_payload_id")`（MySQL 唯一索引允许多 NULL） |
| `backflow_envelope` | JSON | True | NULL | 信封原文只读副本（自检通过时快照，不改写；requeue 刷新后覆盖留档） |
| `expected` | 原列 → **nullable=True** | — | — | ALTER 放宽；普通 case 必填由守卫保证（§4.6/§5.5） |
| `assertions` | 原列 → **nullable=True** | — | — | 同上；error case 由激活器写恰一条 keyword_not_contains（§6.2） |
| `metrics` | 原列 → **nullable=True** | — | — | 同上 |

- 装载约束（error case 语义，§5.5 装载 + §6.5 写守卫 + §6.2 断言装配）：`is_gold=False`、`is_held_out=False`；`case_type='regression_error'` 时 `expected/metrics` 恒 NULL。
- **CaseVersion 不预建**（批 2 D6）：error case 激活不建 v1 快照；error run 落 result 走既有 `_ensure_case_version` 惰性快照（§8.8）。

### 4.2 TestSuite 扩列（同文件 `models/case.py`）

| 列 | 类型 | nullable | 默认 |
|---|---|---|---|
| `is_error_suite` | BOOLEAN | False | False |

### 4.3 error_backflow_inbox 新表（新模型 `models/error_backflow_inbox.py`）

对齐批 1 §5.2 DDL：

| 列 | 类型 | 约束 | 语义 |
|---|---|---|---|
| `id` | BIGINT | PK AUTO_INCREMENT | — |
| `payload_id` | VARCHAR(64) | NOT NULL, UK `uk_inbox_payload` | online uuid4 幂等键 |
| `schema_version` | VARCHAR(16) | NOT NULL | — |
| `case_type` | VARCHAR(32) | NOT NULL | — |
| `envelope_json` | JSON | NOT NULL | 信封原文留档（不改写） |
| `status` | VARCHAR(24) | NOT NULL | new / case_created / rejected（§3.3） |
| `reject_code` | VARCHAR(32) | NULL | online_content_gap / offline_cap_gap / manual_invalidate |
| `reject_detail` | VARCHAR(512) | NULL | 缺哪个字段 / 原因补注 |
| `case_id` | BIGINT | NULL | 建成 error case id；作废后置 NULL |
| `ack_status` | VARCHAR(16) | NOT NULL | none / pending / acked / blocked |
| `last_error` | VARCHAR(512) | NULL | 最近处理异常摘要 |
| `received_at` | DATETIME | NOT NULL DEFAULT CURRENT_TIMESTAMP | — |
| `updated_at` | DATETIME | NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE | — |

- 索引：`KEY idx_status_ack (status, ack_status, updated_at)`（对账 / 自愈 / 卡住重扫三类扫描位）。

### 4.4 EvalRun 扩列（`models/run.py`）

| 列/变更 | 值 |
|---|---|
| `TRIGGER_TYPE` L14 → `("manual", "held_out", "error_regression")` + MySQL ENUM 迁移（§4.5 rev3） |
| `trigger_signal_id` | BIGINT NULL（批 2 §6.1 consumed 锚；建单记所据信号 manual/held_out run id） |
| `excluded_case_ids` | JSON NULL（**R-4**：cap 截断挤出的最老溢出 case id 列表；NULL/[] = 未截断。诚实标记 = 非空；溢出计数 = len；供只读面响应透出，§10.1） |

- **R-10 截断标记不在本表**：detail v1.9 R-10 `input_truncated` 是 **online needs_review 侧** reason 4 附加标记；offline error case 的 input ≤8K 截断快照边界由 **online 装配侧保证**（§5.5 注），offline 不截断、EvalRun 无对应列。此前草稿误引的 `case_truncated` 列已清除（§1.1/§2.1/header 同步）。
- `pinned` 已存在（model 默认 False）——内部创建器显式置 True。
- EvalResult **不加列**：`error_type String(32)` / `error_detail Text` / `assertion_results` / `judge_results` 均已有，够用。

### 4.5 alembic 迁移拆分（4 个新迁移，顺序 + 可回滚）

> 当前 head = `b1a2c3d4e5f6`。每个迁移写 `downgrade` 完整回滚；**单一迁移内操作同表同事务**。

1. **rev1 `add_error_backflow_case_cols`**（down = 删列 + 三列回 NOT NULL + 撤 uk_case_payload_id）：
   - `case.py`：加 case_type/payload_id/backflow_envelope + uk_case_payload_id；ALTER 三列 `DROP NOT NULL`；`is_error_suite` 加列。
2. **rev2 `create_error_backflow_inbox`**：建 `error_backflow_inbox`（全列 + uk_inbox_payload + idx_status_ack）。down = drop table。
3. **rev3 `extend_run_trigger_and_anchor`**：`ALTER TABLE eval_run MODIFY COLUMN trigger_type ENUM('manual','held_out','error_regression') NOT NULL`（SQLAlchemy `sa.Enum` 原生 enum 名保持 `trigger_type`）；加 `trigger_signal_id BIGINT NULL`、`excluded_case_ids JSON NULL`。down = 还原 ENUM + 删两列。
4. **rev4 `backfill_case_columns`**（数据迁移，可选合并进 rev1 数据步）：把现有普通 case 的 `case_type` 保持 NULL（无需数据动作）；确认无现网 'error_backflow' 数据（零落地前提）。

- **验证**：`alembic upgrade head` 后 ORM `metadata.create_all` 不与迁移冲突（跑 `backend/tests` 建表冒烟）；`alembic downgrade -1` 逐级回滚成功（G1 门）。

### 4.6 写校验收口（`core/case_rules.py` 新增纯函数，批 1 评审 A-I6）

```python
def assert_normal_case_golden_fields(case_type_is_null: bool, fields: dict) -> None:
    """普通 case（case_type IS NULL）必填三列非空；error case 锁 expected/metrics 恒 NULL、放行 assertions。违反抛 ApiError。"""
```

- 调用点收敛三处：`create_case` / `update_case` / pull_loop 建 error case（error 分支专属校验，**不得用普通 case input schema 驳回 error case**）。
- **D1 修订**（批 2 §7.3 声明）：error case 分支锁 expected/metrics 恒 NULL、放行 assertions 由激活器写入（§6.2）；语义与普通 case 分支显式分叉。
---

## 5. 收单链路 pull_loop（M2）

### 5.1 文件结构与启动

新增文件（仿 `runner/scanner.py` 骨架）：

- `backend/app/runner/pull_loop.py`：`BackflowPullLoop` 类 + 模块单例 + `pull_loop_worker()`。
- `backend/app/core/error_payload.py`：纯函数 `validate_envelope` + verdict 常量（无 IO，可单测）。
- `backend/app/models/error_backflow_inbox.py`：ORM（§4.3）。
- inbox 数据访问层：并入 `pull_loop.py` 或独立 `backend/app/repositories/inbox_repo.py`（§5.6 函数集）。
- `backend/app/main.py`：startup 受 `BACKFLOW_ENABLED` 门控起 `pull_loop_worker` + 挂 `platform_runs` router（§10）。

`pull_loop_worker()` 仿 scanner：`while True: try: ... except: logger.exception` + `asyncio.sleep(pull_interval)`；周期默认 60s（`BACKFLOW_PULL_INTERVAL_SECONDS`）。**五步单轮**（仿批 1 §6.1）：

1. `SELECT GET_LOCK('backflow_pull_loop', 0)` 单例互斥（同 session 内 RELEASE，finally）。
2. ① **ack 对账扫描**（§5.7）→ ② **inbox 卡住重扫**（status='new' 且 last_error 非空/超时）→ ③ **增量拉取**（逐已注册 agent，§5.2）→ ④ **低频自愈扫描**（每小时，§5.8 R-8）。
3. 每步独立 try/except（一步炸不退出循环）。

### 5.2 增量拉取（R1 assembled_ts 锚 + 逐 agent）

- **游标持久化**（`system_config` 键，**不新增 sysconfig schema 类型**）：`backflow_pull_token`（next_token，online 端分页游标）；增量下界 **逐 agent 独立** `backflow_since_ts` = `{agent_name: iso8601}` 映射（JSON 存单键，§11.2）——**不用单一全局水位**。
- **为什么逐 agent**（水位单调性论证）：每 agent 各自以上次成功登记点为下界、空轮不前移，慢 agent 不会被同轮快 agent 已登记的高水位拖过「已装配未拉取」区间 → **杜绝「快 agent 前移单全局水位 → 慢 agent 新 payload（assembled_ts < 新水位）被永久跳过」**。单全局水位下该漏拉窗口真实存在（phase1 L301 语义固有边界，评审 D-3），v1 直接以 per-agent 消解，不必等 online 契约改。
- **推进规则（per agent）**：仅当该 agent 本轮「≥1 payload 成功登记入 inbox」才把其 `since_ts` 推进到本批该 agent 已登记 payload 的 `max(assembled_ts)`；该 agent 空轮 → 该 agent 水位不动。乱序/未来 assembled_ts：按登记成功序记账，`max` 推进；迟到乱序值落入低侧由后续轮重覆盖，`payload_id` 幂等 upsert 兜底。
- **逐 agent 拉取**（Q4）：遍历 `Agent` 表（仅 enabled），逐个带 `agent=<name>` + 该 agent 自身 `since_ts` 请求 `POST {online}/pull/payloads`；请求体 `{schema_version:"1.0", case_type:"regression_error", agent, limit≤100, since_ts}`；响应逐 payload 附 `assembled_ts`（**online 时钟，禁用本地墙钟当 since_ts**）+ `next_token` 翻页（页间把 token 存回 system_config，续页用响应 assembled_ts 判断续拉范围）。
- 出站通道：**async httpx `AsyncClient`**（单进程 workers=1 共享 event loop，同步阻塞冻结全部协程——批 1 C-B2/A-I5）；peer URL = `BACKFLOW_ONLINE_API_BASE` + SSRF 允许名单（扩展 `core/http.py` allowlist 或新增 peer env，环 2 前落）。

### 5.3 `validate_envelope`（纯函数，`core/error_payload.py`）

```python
# 返回 (ok: bool, errors: list[str], verdict: str)
def validate_envelope(envelope: dict) -> tuple[bool, list[str], str]:
    # verdict ∈ {"ok", "content_gap", "version_drift"}
```

校验矩阵（批 1 §2.1 钉死，逐条实现）：

| 类别 | 规则 |
|---|---|
| 必填（缺即驳回 content_gap） | `schema_version` / `case_type` / `payload_id` / `source.agent` / `source.interface` / `source.trace_id` / `versions.trigger_version` / `evidence.input` / `assert.no_fallback.config_ref.wordlist_version` / `no_fallback_config` |
| 可空（缺/空**不得驳回**） | `versions.fix_version` / `evidence.output` / `evidence.session_snapshot` / `evidence.retrieve_hit` / `source.cluster_id` / `source.generation` |
| 硬校验 | `schema_version == "1.0"`（非 1.0 → **version_drift**）；`case_type == "regression_error"`（非白名单新值 → **version_drift**）；`assert.no_fallback.config_ref.wordlist_version == no_fallback_config.wordlist_version`（不一致 → content_gap）；空词表 / words 缺失 / wordlist_version 非法 → content_gap（**fail-closed，不静默 pass**） |

- verdict 语义：`content_gap` → reject_code=`online_content_gap`（detail 指明缺项）；`version_drift` → 同 reject_code 但 detail 注明「版本不识别，需 offline 升级/联调同步，requeue 不愈」（与空词表区分，批 1 B-V2）。
- **信封 JSON 样例**取自 online detail §7.1（本稿不复制防漂移）；单测 fixture 按该样例构造。

### 5.4 映射（`resolve_agent` / `resolve_interface`）

```python
def resolve_agent(db, source_agent: str) -> Agent | None:
    # Agent.name 精确匹配（Q2 砍别名）；不命中 → None → reject offline_cap_gap

def resolve_interface(db, agent: Agent, method: str, path: str) -> AgentInterface | None:
    # 剥前导 "METHOD " 前缀取 path；method 精确比较；path 逐段匹配：
    #   "{...}" 占位符段归一为通配、其余段精确；仍不命中 → None → offline_cap_gap
```

- 前置依赖（C4）：被测 agent 须已在 offline 完成 v2 接入（Agent + adapter_config + AgentInterface 就绪），否则 unregistered agent 进不了响应 / 已注册未接入 → offline_cap_gap。

### 5.5 建 error case（同事务，含 error suite upsert）

每 payload 处理步骤（批 1 §6.3）——inbox upsert 后走：

1. inbox upsert(payload_id)：已存在按 §5.9 判据重处理/幂等跳过；新 → status='new', ack_status='none', envelope_json=原文。
2. `validate_envelope` → content_gap/version_drift → status='rejected', reject_code=online_content_gap → **ack invalidated（§5.7）**。
3. `resolve_agent`/`resolve_interface` → 任一不命中 → status='rejected', reject_code='offline_cap_gap' → ack invalidated。
4. 命中 → **同事务内**：upsert error suite + 插 error case（字段装载见下）+ inbox → status='case_created', case_id, ack_status='none'。
5. commit 后发 ack active（带 case_id）。**顺序铁律：本地先落库 → 再发 ack**（两阶段崩溃洞，批 1 §6.4）。

**error suite upsert**（批 1 §5.3）：按 `agent_id AND is_error_suite=true` 查找（不依赖名字），不存在则建（name 形如「{agent.name}·error 回流哨兵集」）；撞名自动加后缀；人工普通 suite 不允许置 is_error_suite=true（§6.4 守卫）。

**error case 字段装载**（error 分支专属，绕过普通 case schema 校验）：

| 字段 | 值 |
|---|---|
| `case_type` | `'regression_error'` |
| `payload_id` | 信封 payload_id |
| `suite_id` | error suite id |
| `interface_id` | resolve_interface 结果 |
| `input` | `evidence.input`（**纯装载不改写**；≤8K 截断快照边界由 online 保证，offline 不截断——批 2 §2.3 v0.7 注⑤ / R-10） |
| `input_type` | `evidence.input` 含 turns → `'conversation'` + 落 `input_turns`；否则 `'text'` |
| `status` | `'active'` |
| `expected`/`metrics` | NULL |
| `assertions` | **批 2 D1：激活器同事务写恰一条 keyword_not_contains（§6.2）** |
| `backflow_envelope` | 信封原文快照（自检通过时；requeue 刷新后覆盖） |
| `is_gold` / `is_held_out` | False |

### 5.6 inbox DAO + 状态机迁移（函数集）

- `_upsert_inbox(db, envelope) -> InboxRow`（payload_id 命中则返回既有行）。
- `_mark(db, row, *, status=..., case_id=..., reject_code=..., ack_status=...)`：集中迁移入口，校验非法组合（§3.3 单测断言）。
- 扫描谓词常量：`NEEDS_ACK = (status.in_(["case_created","rejected"]), ack_status.in_(["none","pending"]))`（对账只扫此集，acked/blocked 不重放——A-B3）；`STUCK_NEW = (status == "new", last_error 非空或超时)`；`CAP_GAP_PROBE = (status == "rejected", reject_code == "offline_cap_gap", ack_status == "acked")`（R-8 探测态，§5.8）。

### 5.7 ack 发送 + 对账（R3 契约 / R1 增量）

- `send_ack(payload_id, action∈{draft,active,invalidated}, case_id?, reason?)`：`POST {online}/pull/ack`；出站携 offline 自签服务凭证（`scope="backflow:pull"`，§10.3）。
- 前置矩阵（批 1 §2.3）：`active`=online 当前 ∈ {assembled,draft} 且必带 case_id；`invalidated`=online 当前 ∈ {assembled,draft} 且必带结构化 reason；`draft` 预留不用。
- **错误分流**：
  - 404 `ERR_PULL_0003`：即时告警端到端连通性，ack_status=`blocked` + last_error 注明，人工核查（**400 与 404 处置分开**，B-S1）。
  - 400 `ERR_CLUSTER_0003`：解析响应体 `offline_status` + `invalidate_reason`（**R3**）→ 若 `invalidate_reason == "manual_invalidate"` → 走 §5.9 竞态对账（不入 blocked）；否则有界重试（连续 N 次）后落 `blocked`。
  - 5xx/网络错：指数退避重试，ack_status=`pending`，不置 blocked。
- **对账扫描（每轮循环第①步）**：`NEEDS_ACK` 集重放 ack（幂等，200 即置 acked）；持续失败 → last_error 累计 + 日志告警不阻塞。`acked` 才视为闭环。

### 5.8 R-8 cap_gap 探测态节流（自愈扫描，低频每小时）

对 `status='rejected' AND reject_code='offline_cap_gap'` 行：

- **首次 reject 闭环**（ack_status ∈ {none,pending}）→ 照常发 invalidated ack（幂等 200）→ 进探测态（ack_status='acked'）。
- **已 acked 探测态行**：每小时本地重跑 `validate_envelope` + 映射（**不复位 ack_status、不重发 invalidated**）：
  - 补齐（映射命中）→ 建 case + **单次** ack active（复用 payload_id，契约 R2 `invalidated(offline_cap_gap)→active`）→ status=case_created。
  - 仍缺 → 静默等轮。
- **判据收窄**（勿按 phase1 v0.2.1 旧语义）：复位重处理仅限 `ack_status ∈ {none,pending}`（首次/对账未闭环需重发）或收到 requeue/内容刷新（online_content_gap 重拉覆盖 envelope）。

### 5.9 requeue 重处理 / manual_invalidate 竞态

- **online_content_gap**：等 online admin requeue → requeue 刷新 assembled_ts → 重拉命中 → inbox 复位 `ack_status='none' + status→'new'` + 覆盖 envelope_json → 重走 §5.5 2~5（本平台不自愈造次品）。
- **manual_invalidate 竞态**（R3）：ack active 收 400 且 invalidate_reason=manual_invalidate → 本地作废已建 case（case_id 置 NULL、case 本体 invalidated 软删语义）→ inbox 落 `rejected + manual_invalidate + acked`（远端已 invalidated 不需再 ack）→ admin requeue 后重处理自愈。
- **重处理 vs 幂等跳过判据**（汇总）：`case_created` → 幂等跳过；`new` 卡住 → 本次跳过 + §5.6 STUCK_NEW 重扫恢复；`rejected` → 仅按 §5.8/§5.9 判据复位重处理（R-8）。

---

## 6. 断言固化 / keyword_not_contains 登记 / 写守卫（M3）

### 6.1 词级净化（转录 `no_fallback_config.words` 前）

```python
def sanitize_words(raw: list[str]) -> list[str]:
    # 对每词：strip + 拒空串/拒纯空白/拒超长词 + 大小写归一
    # 净化后为空 → 报错拒激活（fail-closed，不落空断言）
```

- 净化/命中规则须与执行端 `KeywordNotContainsOp` 子串匹配语义对齐（**大小写敏感核对并归一**，批 2 §7.2 命中一致性 / 安全 4），防「边界词致恒 fail」或「大小写不一致致漏判恒 pass」。

### 6.2 断言装配 + 写入时机（D1 / 批 2 §7.3）

- 建 error case 同事务内写：`assertions = [{"op": "keyword_not_contains", "args": {"path": "answer", "keywords": sanitize_words(no_fallback_config.words)}}]`；`expected/metrics` 恒 None。
- **结构自检前置**：`validate_envelope` 已保证 words 非空（fail-closed）→ 净化后空词表拒激活只作防御。
- 结果：error case 激活成功后 `assertions` 恒非空（D1 收窄批 1 §5.1「三列允许空」）；nullable 保留作 fail-safe。

### 6.3 存量 backfill（特权直写 + 幂等 + 并入补偿对账）

- 范围：批 1→批 2 间隙产生的 active error case（`assertions IS NULL`）。**写路径必须特权内部直写**（`update_case` 对 case_type IS NOT NULL 一律 403，§6.5）——新函数（如 `runner/backfill.py::backfill_error_case_assertions(db, case)`）**仅当 `assertions IS NULL` 哨兵**下执行（空值幂等，不覆盖激活器已写内容）。
- **并入 §7.3 低频差集对账**持续对账直到清零（可重跑幂等）；清零前该类 case 不进 error run 判定（加载过滤，§8.1）。backfill 幂等 = 仅补 `assertions IS NULL`。

### 6.4 keyword_not_contains 发布一致性登记（批 2 §7.4 降格）

1. `core/constants.py` `_ASSERTION_OPS` 增补 `"assertions.ops.text.KeywordNotContainsOp"`（frozenset 注释「禁止运行时增删」随本批上线）。
2. `ASSERTION_OP_CLASS` 增补 `"keyword_not_contains"` 键；`seed.py _seed_assertion_ops` 幂等插 DB 行（已有行则跳过）。
3. **同批核验**：`ALLOWED_CLASS_PATHS` 含其 class_path + `AssertionOpDef` 有行——白名单与 DB 行必须同批上线，防 registry 启动抛错。
4. 激活器写断言前若校验 DB op，须确保行已存在（同批部署顺序）。

### 6.5 写守卫落点（error case / error suite 403 全清单）

**error case（case_type IS NOT NULL）单体写守卫——一律 403**：

| 端点/函数 | 文件 |
|---|---|
| `update_case` | `api/cases.py` |
| `invalidate_case`（DELETE 作废） | `api/cases.py` |
| `add_annotation` | `api/annotations.py` |
| `add_case_scenes` | `api/scaffold.py` |

**error suite（is_error_suite=true）级防护**：

- 禁止人工删除 suite、禁止人工增/改普通 case、禁止置入 golden/held_out；
- `create_suite`/`update_suite` 禁人工置 `is_error_suite=true`（D-15）；
- `create_run`/`rerun_run` 对 is_error_suite 拒绝（§7.1，白名单 manual/held_out 不动）；
- `list_suites` 透出 `is_error_suite`（只读标注，D-15）。

**统一过滤谓词常量**（集中定义防漂移，批 1 §5.4）：`CASE_TYPE_IS_NULL`、`IS_ERROR_SUITE_FALSE`、`TRIGGER_NOT_ERROR_REGRESSION`。
---

## 7. error_regression run 触发与创建（M4）

### 7.1 信号与挂接（批 2 §5.1/§5.3）

- 信号 = 到达**终态**（非 pending/running）的 `trigger_type ∈ {manual, held_out}` run（携带 agent_id + version，version = §3.3 共享域校验值）——被测 agent 完成一次该版本评测 = 发版登记。held_out 复测同版本 / rerun 同版本均命中幂等不重复建单；**error_regression run 自身不作信号**（防递归）。
- 新文件 `backend/app/runner/auto_schedule.py`：`async def maybe_auto_schedule(db, agent_id, version, signal_run)` + 差集对账 worker。
- **挂接点**：orchestrator `_finish` L560 commit **之后** fire-and-forget（`asyncio.create_task`，失败仅记日志）——**不得在持 eval_run 行 FOR UPDATE 锁的事务内调用**（maybe_auto_schedule 建 run 需取 agent 行锁 → 反向锁序死锁，批 2 §5.2 锁序约定）。

### 7.2 `maybe_auto_schedule`（批 2 §5.2 终态语义 + C1 + consumed 锚）

```python
async def maybe_auto_schedule(db, agent_id, version, signal_run):
    agent = await agent_mutex(agent_id, db)          # 取 agent 行锁（FOR UPDATE）
    suite = error suite of agent (agent_id + is_error_suite=true)
    if suite is None or runnable_case_count(suite) == 0:  # C1 创建前门禁
        return                                          # 不建空 run、latest 保持 None
    latest = 最近 error_regression run（(agent,version)，锁后重读）
    if latest is not None and latest.trigger_signal_id == signal_run.id:
        return                                          # consumed 锚：该信号已消费
    if latest is None:
        _create_error_regression_run(...); return
    if latest.status in {"pending","running"} or latest.status in {"completed","partial_failed"}:
        return                                          # 防重 / 已有可判终态不重复回归
    if latest.status in {"timeout","cancelled"}:
        _create_error_regression_run(...)               # 坏终态仅当本信号为新（未被消费）
    # 释放 agent 行锁由上下文管理
```

- **runnable 谓词**（与 §8.1 执行加载**同一谓词**）：error suite 下 `status='active' AND case_type IS NOT NULL AND assertions 非空且形态 keyword_not_contains`。
- **洪峰收敛 = 活跃闸 1**：同一 agent 同时最多 1 条活跃（pending/running）error run（含跨 version）。校验在内部创建器**取 agent 行锁后、插建前**：查「该 agent 活跃 error_regression run ≥ 1 → 不建、返回」。
- **并发防重**：读 latest 必须发生在取 agent 行锁后；不加 runs 唯一索引（MySQL 无法表达仅 error_regression 的 (agent,version) 唯一），行锁足够。锁序约定：error-run 路径先取 agent 行锁，持锁期间不取同 agent eval_run 行锁做更新。

### 7.3 R-3 版本差集对账（取代「单最新锚」历史语义）

低频任务（并入 scanner 周期或独立 task，量级小），对**每个有 active error case 的 agent**：

```
signal_versions = 该 agent 已到终态 manual/held_out run 的 distinct version
                  （显式排除 error_regression run 自身）
have_versions   = 该 agent 已有 error run 的 version（含 timeout/cancelled 坏终态：视为有 run）
diff = signal_versions − have_versions
# 对 diff 内每个缺版逐个补建：每版取该版最近终态 manual/held_out run 为锚 → maybe_auto_schedule
# 遵守活跃闸：一周期至多补 1 个（补建后该版本即进入 have_versions，下周期差集自然前移）
```

- 吸收态（`trigger_signal_id==signal.id → return`）继续拦截已消费版本；差集管「从未建过版本」，两机制正交。**坏终态版本不矛盾（§7.2 vs §7.3）**：差集侧把 timeout/cancelled「视为有 run」只保证 diff 不重复补建；重建仍由 §7.2 行 timeout/cancelled 分支触发——条件是「该版出现比 `latest.trigger_signal_id` 更新的新信号」；consumed 锚前移后吸收态再次拦截 → 同一坏 run 至多重建一次、无自动重跑循环（phase2 §5.2 L237 / §5.3 L260 权威双路径同此，非缺陷）。
- 兼任 **§6.3 backfill 清零对账**：对 `assertions IS NULL` 的 active error case 特权补写（幂等），直到清零（清零后 C1 门禁放行 → 差集内版本 run 自然覆盖存量 case，不依赖下次发版信号）。
- 环 2：洪峰 3 版连发 → 3 run 最终全建出、中间版 claim 可回查（批 2 §11.2 场景 17）。

### 7.4 内部创建器 `_create_error_regression_run`（批 2 §6.1 参数表）

| 字段 | 值 |
|---|---|
| agent_id / suite_id | 信号 run 的 agent / 该 agent error suite |
| case_ids | error suite 下 `status='active' AND case_type IS NOT NULL` 的 case **newest-active-first 取至 cap**（溢出最老侧；**显式非空**） |
| cap | 由 error-run 专用预算反推：`cap ≈ H_run/(case 平均复现时长 × 尾因子)`（示例 H_run=2h、avg 10s/case、尾因子 ×2 → cap≈360；按 agent adapter 实际上限 + 历史 case 时长定） |
| version | 信号 run 的 version（共享域原样承载 = fix_version） |
| trigger_signal_id | 触发本 run 的信号 manual/held_out run id（consumed 锚） |
| trigger_type | `'error_regression'` |
| pinned | True |
| status | `'pending'` |
| lease_until | **非空初始租约**（now + 90s 起步；pending 卡死可被 scanner 回收，§8.7） |
| run_config | **error-run 专用预算**（不落缺省 case_timeout=120/max_retries=1）：按 adapter 上限冻结，如 case_timeout=600 / max_retries=2 / hard_deadline = 总预算含闸等待估值（实施依 adapter 实际上限定） |
| 名额 | 不检查、不计数 `_max_active_runs`（公开 create_run 白名单不动） |

- **截断诚实**：cap 截断时 `excluded_case_ids` = 被挤出的最老溢出 case id 列表（§4.4 列）；溢出 case 不进 run、无结果行、不落 na、不触发重建（守卫驻留，批 2 §6.1 注 2）。
- **创建不走公开 create_run**（绕过 trigger Literal + `_max_active_runs` + semver），直接插 EvalRun 行 + 初始 lease + 发 `start_run`（`orchestrator.start_run(run_id)`）。

### 7.5 api 层配套（`api/runs.py`）

1. **version 共享域校验**：`_SEMVER` 强校验（create_run L230-231 / rerun 同）移除 → §3.3 域校验（非空 ≤64 无空白/控制字符）；同 agent 乱序仅告警不拒（质量防线，不靠格式）。
2. **活跃计数扫描排除**（批 2 §9.2）：create_run L251-253 / rerun L378 互斥计数加 `AND trigger_type != 'error_regression'`（error run 不占名额、不堵 manual）。
3. **error suite 拒绝**：create_run/rerun_run 对 `suite_id` 属 error suite → 400（§6.5）。
4. **rerun 对 error_regression 显式拒绝**（批 2 §6.7）：若批 1 守卫只拦 create_run，本项补 `if src.trigger_type == "error_regression": 拒绝`（error run 不暴露公开 start/rerun 通道）。

---

## 8. error_regression run 执行语义（M5）

### 8.1 `_load_run_cases` 分支（`runner/case_loader.py`，L20-32 改写）

```
error_regression:  suite_id==run.suite_id AND status=='active'
                   AND case_type IS NOT NULL AND is_held_out==False
                   AND assertions IS NOT NULL 且形态 keyword_not_contains   # §9.5 素材缺失前置过滤
其它(manual/held_out): suite_id==run.suite_id AND status=='active'
                   AND is_held_out==(run.trigger_type=='held_out')
                   AND case_type IS NULL                                    # 普通评测永不带哨兵
case_ids 子集叠加逻辑不变
```

- error 分支返回集**按加载时持久化**或按同一谓词重算，使 §8.7 回填对象边界确定（实跑集 = 本 run 已加载且尚无 EvalResult 的 case）。

### 8.2 orchestrator error 分支（`runner/orchestrator.py` `_run` 新增）

`_run` L135 后按 `trigger_type=='error_regression'` 分发到独立 `_run_error(run_id)`（**不复用普通 `_run` 后续**，但复用 `_run_one` 之下机制）：

1. **D8 前置校验**：`pinned==True AND case_ids 显式非空 AND suite_id 为该 agent 的 error suite`，不满足 → 跳过并告警（`_fail_run` 语义不适用，记日志 + run 置 cancelled？——**按批 2 §6.2：skip + 告警**，非法直插脏行不真打被测 agent）。
2. **跳过 `_probe_before_run`**（探活 = 多一次真实调用，对 error 判定零增益）。
3. 加载 case（§8.1 error 分支）。
4. **执行循环**（每 case）：
   - **槽 acquire**（§8.3 共享层：shared-agent → error 桶 global=1；同 agent 总并发 ≤ per_agent_concurrency、error ≤1 路，manual 占满时 FIFO 排队）；排队期间循环心跳续租（§8.5）。
   - **breaker**：独立 key `agent_id:error_regression`（§8.4）；打开 → `circuit_open` → na。
   - `executor.execute_case(adapter, client, case, timeout_s=run_config.case_timeout)` 复现：
     - 成功（`CaseOutcome.error_type is None`）→ verifier（§9.2）→ pass/fail 一次写终值；
     - 失败（error_type 非空）→ 按 §3.1 RETRYABLE 重试（复用 `_call_once` 退避语义，max_retries 取 error-run run_config）→ 重试尽 → `pass_fail='na'` + error_type 如实记录；
     - 调度层未执行（circuit_open/interface_disabled，`_run_one` L270-273/L340-343 默认落 'error' 路径）→ **拦截**：记 `pass_fail='na'` + error_type 原值（不落默认 'error' 行）。
   - 落 `EvalResult` 走 `_ensure_case_version` 惰性快照（§8.8）+ **一次写终值**（不走 `_save_result` 默认 pass_fail='error' 路径，去掉「先写 pass 占位」）。
5. 独立收尾 `_finish_error_regression`（§8.6）——**不经 SCORING、不建 judge 任务、不产 agent_score**。

### 8.3 error run 执行并发槽池（M5 前置：limiter 进程级共享 per-agent 层，2026-09-07 裁决 A）

**现码事实（limiter.py L35-67 核读）**：`KeyedLimiter` 的 per-agent 信号量**嵌套在每个 run 独立桶内**——`_buckets: dict[int, WeightedLimiter]`，每桶自带 `_per_agent: dict[str, Semaphore]` + `_global` sem。manual 桶与 error 桶 = 两个独立 `WeightedLimiter` 实例，**桶间 per-agent sem 不共享**。

**权威要求（phase2 §6.2 step4 = R2 共享槽池语义，现行 v0.7.2，L305/L209/L522）**：manual/held_out 与 error 复现**共享同 agent 的执行并发槽池**——同 agent 并发总数 ≤ per-agent 上限（默认 3）、error 复现至多 ≤1 路、**不叠加出超限路数**；manual 占满槽位时 error case FIFO 排队、槽释放即进；manual 业务侧零改动（phase2 L306）。**现码无此「跨 run 同 agent 共享池」→ 须实现层补齐 = M5 前置（共享执行基建，A 级；2026-09-07 用户裁决方案 A）**：

1. **改造点（最小侵入）**：`KeyedLimiter` 增**进程级共享 per-agent 层** `_agent_sem: dict[str, asyncio.Semaphore]`（key = agent.name，容量 = 该 agent 首次执行时的 `per_agent_concurrency`，默认 3）。`acquire(run_id, agent)` 序 = **shared-agent → 桶内**（相对序同现码「先 per-agent 后 global」）；桶内额度成功获取才算占位，任一步失败/异常须逆序释放已取信号量；`release` 逆序。未 `set_run` 走 `_default` 桶的调用带 agent 亦先取 shared-agent（同池，防旁路）。
2. **manual/held_out 业务零改动 + 行为等价回归**：manual 侧 orchestrator acquire 调用点不变，改动透明于 limiter 内部。无 error run 并存时 manual 单 run 仍 ≤ per_agent_concurrency 路（shared 层与旧桶 per_agent 同值同限）→ **现网全部现状场景行为不变**，须回归 manual 并发单测（§12 #23）。“manual/held_out 侧零改动”（phase2 L306）= 业务入口零改动，本改造不触 manual 执行代码。
3. **error run 侧**：error 分支 `set_run_limits(run_id, global_limit=1, per_agent=1)` 建桶（error 内部逐 case 串行、每 case 一槽）；执行前 acquire 经 shared-agent 层 → 同 agent manual 占满 3 时 error 该 case **FIFO 排队**（`asyncio.Semaphore` 公平等待），manual 任一 case 释放即进；manual 只用 2 路时 error 用余量 1 路 → **同 agent 总并发恒 ≤ per_agent_concurrency、error ≤1 路、不叠加出超限**（phase2 L305 逐字兑现）。
4. **配置与生命周期**：`asyncio.Semaphore` 容量不可变——`_agent_sem` 容量取该 agent **首次执行时** config 读值；运行期改 `per_agent_concurrency` 需重启 worker 生效（重建 sem = 丢弃在途等待者；简单取舍：重启生效，联调期以重启切值）。
5. **排队饿死边界（承接 phase2 §6.6）**：error 排队期 lease 持续刷新（§8.5 心跳），不被 scanner 误收；排队无上界兜底 = error-run `hard_deadline`（含排队估值）超时回收（phase2 L307；坏终态 + 新信号可重建，§7.2）。
- **跨进程前提**：默认单进程 workers=1；多进程下进程内 shared 信号量失效，需 agent 行轻量执行标记/乐观校验（实施前核对 worker 模型，§13 行 4）。
- **撤销 v0.2 取向**：「不改 limiter、接受叠加 ≤4 路」与 phase2 R2 权威冲突，本版向权威对齐。也不宣称「同 agent 全局 ≤1 路」（manual 多路 + error 余量并存于池，phase2 §6.2 明示）。

### 8.4 熔断隔离

error 复现**不与 manual/held_out 共用 agent breaker**（共用会使 error 连败熔断波及其它评测，反之亦然）。独立 key `f"{agent_id}:error_regression"` + 独立阈值（默认同 `breaker_failure_threshold=5`，实施可配）；breaker 打开 → `circuit_open` → na → partial_failed，不污染 manual。

### 8.5 心跳续租（固定时间片，批 2 §6.6 v0.5.2 钉死）

error 分支执行循环以**固定时间片**（间隔 ≪ lease TTL，如 15s，覆盖单 case 执行窗口）持续刷新 `lease_until`——**不得仅以「每 case 完成」为粒度**（per-case 预算可长于 lease TTL，逐 case 粒度会让 executor 阻塞期活 run 心跳过期 → 被 scanner 误收）。**排队等待期间亦刷新**（acquire 前等待 loop 内周期续租）。复用 `_heartbeat` L496-512 机制 + interval 自 error-run run_config 或固定值。

### 8.6 独立收尾 `_finish_error_regression`（D5，不动共享 `_finish`）

新增函数（orchestrator 内），**不复用共享 `_finish`**（其对账 L541-545 回填 'error'、全 na 落 completed、按 'error' 计 error_case 均与 error 语义冲突）：

| 条件 | run.status | 落库 |
|---|---|---|
| 全部 case 有 pass/fail 终值（0 na） | `completed` | — |
| 存在 ≥1 na case | `partial_failed` | — |
| 未跑完被取消/超时（cancel/scanner，§8.7） | `timeout`/`cancelled` | 已完成 case 保留 pass/fail 终值；未完成 case 回填 na |

- 计数：`na_case` = pass_fail=='na' 计数；`error_case` = =='error' 计数（error run 恒 0）；`agent_score` 恒 NULL。`completed` 仅当 0 na。
- **实跑口径**：汇总以实跑集计（case_ids 创建时固化；实跑集 < case_ids 的 case → results 返回空，online 按缺行≠na 轮询）。
- **实跑集为空防御**（C1 兜底）：执行时实跑集仍为空 → **不落 `completed`**，落 `cancelled` + 告警。
- **事务约束**：na 回填与 run 终态推进**同事务、同 db session 直插 EvalResult**（沿用共享 `_finish` L536-546 注释模式：持 eval_run FOR UPDATE 锁时经自开 session 的 `_save_result` 会 FK S/X 互锁 1205）；`_ensure_case_version` 若自开 session，收尾路径先取 case_version 再同事务插。`uk_result` 唯一约束作幂等兜底。**收尾自洽**：同事务内先插 na 行、后置 run 终态。**run 终态推进不另造 CAS**——沿用共享 `_finish` 同款 FOR UPDATE 行锁（orchestrator L527）+ scanner 条件 UPDATE rowcount==1 判胜（scanner L59-66），两写入路径已有行级串行化，error 收尾复用同模式。
- **终态写守卫**：run 已入终态（completed/partial_failed/timeout/cancelled）后，EvalResult 写入一律拒绝（uk_result 撞键 = 丢弃迟到行 + 记日志）——防 orchestrator 长跑与 scanner 回收竞态下把 run 弄成半终态；守卫只拦「已提交终态之后」的迟到写，不拦同事务自身回填。**在途判定丢失**显式接受：宁 na 不半终态（个案假 needs_review 由下版重测收敛）。
- **R-22**：行 3 未完成 case 回填 na 的 error_type **钉死 `scheduler_unexecuted`**；timeout 只作 case 级 na 源 + run 终态字面量，不作回填 error_type。

### 8.7 scanner 短路 + 替代收尾（`runner/scanner.py`，批 2 §6.6）

- `_reap_one_pass` L112/L115 对 `score_run_salvage` 的调用前加守卫：`if run.trigger_type == 'error_regression': 走 error 替代收尾（短路 salvage，不产 agent_score）`。
- **替代收尾**（幂等，已终态 run 跳过）：置 run `timeout` + 实跑集内未完成 case 回填 `pass_fail='na', error_type='scheduler_unexecuted'`（§8.6 事务约束 + §8.1 回填对象界定——**不含**被加载过滤剔除的素材缺失 case）。
- **pending 卡死回收特判**：`status='pending'` 且 lease 过期/缺失的 error_regression run 补回收（内部创建器已写初始 lease；防 pending 永久卡死毒化 §7.2 skip 集）。
- **running 回收判据 = 心跳**：scanner 对 `running` 且 lease 过期（心跳停）的 error run 回收（置 timeout + 回填）；lease 未过期（活执行/活排队，§8.5）豁免。普通 hard_deadline 仅作运行总预算兜底，不作回收主判据。
- **R-22 三路径同源**：scanner 回收 / orchestrator cancel（§8.6 行 3）/ run 级超时收尾三路径回填 na 一律 `scheduler_unexecuted`。

### 8.8 CaseVersion 惰性接通（D6）

error 分支落 EvalResult 必须走 `_ensure_case_version`（orchestrator L448-474 同路径），不得旁路（防 `EvalResult.case_version_id` 非空 FK 断裂）；**不预建、不抽 `_content_hash` helper**。

### 8.9 rerun/start 拒绝（批 2 §6.7）

见 §7.5 项 4：`rerun_run` 对 trigger_type='error_regression' 显式拒绝；error run 不暴露 start/rerun 公开通道。
---

## 9. 判定层：复现语义 + R-12 修补 + R-20 pre-scan 门禁（M6）

### 9.1 复现 + 判定语义

error 分支对每个复现 case **不产 judge**，判定 = `run_assertions` 布尔化：

```
executor.execute_case 成功（CaseOutcome.error_type is None, 有 answer）  → verifier（9.2）
失败（error_type 非空）  → RETRYABLE 重试 → 重试尽 → pass_fail='na' + error_type 如实落（不改判，防分类 key miss 假成败）
调度层未执行（circuit_open/interface_disabled 默认 'error' 落库路径） → 拦截 → na（§8.2 行 4）
```

复现成败恒 > 素材(断言) 真假：复现挂了 = na（无关 answer），复现成 = 按断言判 pass/fail。

### 9.2 verifier 调用点（`run_assertions` 新调用点，锚 `assertions/run.py` L12-40）

- error 分支成功复现后调用 `run_assertions(run, case, unified)`，输入 = 复现 unified dict（case/answer/pass_fail 域 + case.assertions 已装配链），返回 `CaseOutcome(pass_fail, assertion_results)` 布尔化 → `pass`/`fail`，一次写终值（§8.2 行 4 / §8.6 终态写守卫）。
- **断言形态约定（装配见 §6.2）**：error case 断言 = `[{"op": "keyword_not_contains", "args": {"path": "answer", "keywords": [...]}}]` 的合法 assertion 链；verifier 输入含该链 → **命中 = leakage 话术复发（fail），不命中 = pass**。
- 与 `_run_one` 主判定的区别：error 分支**不**「先写 pass 占位再按 pass_fail 修正」，直接一次落终值；不走 `_save_result` 默认 `pass_fail='error'` 语义路径。

### 9.3 R-12：`KeywordNotContainsOp.run()` 空答收紧（`core/assertions/text.py` L33-62）

现况：`KeywordNotContainsOp` 对空/纯空白 answer 返回 **PASS**（`keyword_contains` 天然 PASS、not_contains 需显式收紧）。R-12 修 `run()`：

```python
text = self._extract(...)   # 原取值
if not isinstance(text, str) or not text.strip():
    return OpResult(False, "空/纯空白应答 → 复现空答（leakage 空话术证据）")  # FAIL
# 其余原逻辑不变：keywords 归一 → 检查命中 → 命中即 FAIL
```

- **语义钉死**（phase2 §7.2 / R-20 定性）：空答 fail = leakage 空话术**复发证据**（error_regression 复现空答 → fail → 直接判 leakage），**不是** na（na = 复现没跑到，fail = 跑到且空答）——两条路径不可混。
- **共享算子风险 + 前置门禁**：`KeywordNotContainsOp` 是 **manual/held_out 共享判定算子**（§6.4 注册表同键）。空答收紧会让**现有** keyword_not_contains 断言语义从「空答 = 不命中 = pass」翻转为「空答 = fail」。必须 **R-20 pre-scan（§9.4）通过后**才改（G0 门禁）；改完即写 R-19 护栏锁定字面量行为（§12.4）。**生产联动**：error case 判定自首个 error run 起就依赖本收紧语义（复现空答 → fail = leakage 证据；不修 R-12 则空答被现 op 判 pass → 漏报），故 `backflow_enabled` 上线隐含 G0 已过 / R-12 已合入——pre-scan 未过期间不得开 backflow（§11.2 配置说明同步）。

### 9.4 R-20 pre-scan 报告门禁（owner = offline impl，task 环 0 L49）

R-12 落地前产出 **pre-scan report**（现库实扫 + 处置登记），门禁内容 = 三问三答：

1. **存量依赖盘点**：扫现有 `assertion_results` / case.assertions / text.py 关键词算子（keyword_contains/keyword_not_contains）——现网**空字段回答**被 keyword 断言放行（PASS）的用例有多少、依赖面在哪（哪些 suite/agent/case）。
2. **合法空答白名单**：哪些 **manual/held_out 存量 suite/case**（**非 error case**）的**合法业务回答语义上允许为空**（如无异议流程空答 = 正常）。为何是存量普通 case：R-12 收紧经**共享算子** `KeywordNotContainsOp` 影响所有用它的 suite——error case 空答必 fail（= 检测目标）不豁免，存量 keyword 断言 suite 现空答 PASS、收紧后 FAIL，才是需白名单的受影响面。白名单登记成机制侧**豁免**，载体**不进断言逻辑、不加 per-case 断言分支**：pre-scan 逐受影响 suite 评估后登记清单，G0 按「受影响面小 → 直接收紧 / 面大 → 豁免清单先行、分批合入」裁决（§13 行 12），**不在本稿写死豁免实现**（避免与 error case 装载过滤混用）。
3. **上线形态**：非白名单 case 空答从「keyword_contains 天然 PASS / keyword_not_contains 误 PASS」收紧为显式判定；白名单 case 空答走豁免。

**G0 门禁**：报告三问有结论 + 白名单豁免落位 → 才允许 §9.3 改动合入。报告本身 = 一次性交付物（存 `error-backflow-pre-scan-report.md`，实施首步产出）。

### 9.5 素材缺失运行期防御（兜底，双保险）

- **加载前置过滤**已挡大部分（§8.1 error 分支 `assertions IS NOT NULL 且形态合法`——加载即剔除、不建 run case）。
- **运行期兜底**（加载后到执行前的毫秒窗口 + 形态不符残留）：case.assertions 缺失/形态不符 → 该 case 按 `missing_assertion`/`assertion_shape` **na**（§3.1 值域内 na 源），run 走 partial_failed，不 crash；**空断言（[]）绝不判 pass**（漏判定比 na 危险，na 可回查、误 pass 毒化 auto-fixed 判据）。

### 9.6 R-19 字面量一致性护栏（随 §3.1 代码落）

`runner/error_codes.py` 字面量 + 分类表**必须逐字符等于** executor.py 常量（§3.1 表）。§12.4 单测钉死：任一 key 漂移（拼写/长短名/大小写）→ 测试红（防 executor 加常量漏分类 → unclean 风暴复发）。

---

## 10. 平台只读面与外部鉴权（M7）

### 10.1 runs list/detail 只读输出扩展（`api/runs.py` `_run_out` L200-221 基座）

普通 run 输出 schema 之上，error_regression run 透出（online 检索判据消费所需；字段全部只读）：

| 字段 | 值 | 消费方 |
|---|---|---|
| `trigger_type` | `'error_regression'` | online 识别 error run |
| `version` | 信号 run 的 fix_version（共享域原样） | R-5 versions / 判 K 序列 |
| `trigger_signal_id` | 触发信号 manual/held_out run id | consumed 锚 + R-16 缺行比对 |
| `excluded_case_ids` | 创建时 cap 截断挤出的 case id 列表 | **R-16 落位点**（online runs list/detail 取此判「窗口外欠测」） |
| `suite_id` | error suite id（随 run schema 既有字段承载） | online 反查 error suite |
| per-case 结果行 | pass/fail/na + na 的 error_type | R-1/R-2 判据消费（claim 级、影响域） |

- `_run_out` 增列**只加不改**：既有字段不 rename、不重排；error run 结果明细沿用现有 per-case results 通道（EvalResult 已含 pass_fail/error_type/assertion_results），**不加 offline 专属终态列**（判据纯派生，R-6 载体承诺）。
- **error suite / error case 只读过滤**：普通 suite/case 只读端点（platform 检索）**排除** error suite（`is_error_suite=true`）与带哨兵 case（`case_type IS NOT NULL`）；error 对象仅经显式 error-suite 关联面可见（online 消费走 runs 面 + error suite 显式查）。**dashboard 数字口径容忍**：run 状态聚合若走 runs 表会含 error run 行——口径注记，不与 active-count 混（11.1）。

### 10.2 R-4/R-5 只读检索面（error 侧显式端点，批 2 §9.3/§9.4）

新增 error 侧显式只读面（**不与普通 suite/case 面混**）：
- runs 过滤：`trigger_type='error_regression' AND suite_id=:sid`（按 agent/suite 拉 error run 序列 → 判 K 连续、R-16 缺行）。
- error case 面：error suite 下 active case 的 `case_type / payload_id / assertions（装配后链）` 只读（实施与 R-3 对账 / online 诊断复用）。
- **白名单过滤谓词统一**（§6.5 三谓词，只读面同一份，防只读/写路径谓词漂移）。

### 10.3 外部检索鉴权（service token，`core/security.py` 扩）

online→offline 检索走**服务身份**，不冒充人工会话：
- 现有 security.py 之上加 service token 校验：JWT HS256、`type=service`、scope 内（claim 面/error 面分 scope）。签发/轮换走 system_config 或本地密钥文件，**不入库明文**。
- manual 人工面与 service 面分开：error 只读端点须带 service token（防人工误检索误差隔离语义被旁路）；人工面向现有认证不动。
---

## 11. 只读隔离 + 配置/启动（M8）

### 11.1 存量评测语义零污染（三层隔离总表）

| 面 | 隔离规则 | 落点 |
|---|---|---|
| ① 数据面 | error case 带哨兵（case_type/payload_id）→ **不进** manual/held_out 加载（§8.1 普通分支）；普通 case 永不带哨兵 | case_loader |
| ② 行为面 | error suite 拒建 manual run、error_regression 拒公开 start/rerun、error 断言特权补写（§6.5/§7.5/§8.9） | api/runs.py |
| ③ 只读面 | 普通 suite/case 只读排除 error 对象（§10.1）；**active-count / 名额互斥计数排除 error run**（§7.5 项 2） | api + platform 检索 |

- **active-count 口径**：error_regression 消费 agent 执行名额但**非对外发版评测** → 平台「对外服务中评测」活跃计数**排除** error run（含 10.1 dashboard 聚合若引用 active-count 时同排）。
- 人工只读查看 error run 结果：允许（结果本身 = 发版观测数据，online 诊断消费），但经 **error suite 显式关联面 + 10.3 鉴权**，不与普通评测面混排。

### 11.2 config keys（system_config，seed 值落 `config/seed.py` DEFAULT_SYSTEM_CONFIG L345-422 段内）

| key | 类型 | 缺省 | 说明 |
|---|---|---|---|
| `backflow_enabled` | bool | false | 总开关；false 停 worker + 停调度触发 + 停差集对账，**保留已建数据**（幂等重启可续） |
| `backflow_pull_token` | str | 空 | 增量拉取游标（消费进度，§5.2） |
| `backflow_since_ts` | json（逐 agent iso8601） | 空 | 增量拉取下界：`{agent_name: iso8601}` **逐 agent 独立**（§5.2）；空 = 该 agent 未拉取过 |
| `backflow_poll_interval` | int(秒) | 60 | pull_loop 周期 |
| `backflow_hard_lease_sec` | int | 见 §7.4 | error run 初始/心跳租约基准 |
| `backflow_heartbeat_sec` | int | 15 | error 执行固定心跳片（§8.5） |
| error-run 预算/breaker/并发 | 组 | 依 adapter | case_timeout/max_retries/hard_deadline/breaker 阈值/per_agent_concurrency 收敛值（§7.4/§8.3/§8.4） |

- seed 缺省 = 关闭态；实施/联调期由 infra 配置显式开启。**代码路径不做隐式 on**：未配 backflow 前，DB 可空表、功能零副作用。

### 11.3 启动挂接（`main.py` startup + worker 生命周期）

```
startup:
  if config.backflow_enabled:
      run migration 检查（表/列/索引在）→ 缺失 = 启动失败（fail fast，防半挂特性）
      注册 pull_loop worker（§5.1，后台 task，GET_LOCK 单飞）
      注册差集对账低频 task（§7.3，周期 ≥ pull_loop）
      注册 R-3 cap_gap probe 低频 task（§5.8）— 或并入差集对账同一周期协程
shutdown: 取消 task（幂等，游标已落 config，不丢进度）
```

- worker 即 app 内后台协程（**默认单进程** workers=1 前提，§8.3 注）；执行恢复扫描复用 orchestrator 既有重启扫描（error run 恢复入 §8.7 scanner 周期判据）。

### 11.4 写守卫/谓词单一来源

§6.5 全量写守卫 + §10.2 只读过滤谓词收敛为**共享常量集**（`case_type IS NULL` / `is_error_suite=false` / `trigger != 'error_regression'` 一处定义、api/case_loader/只读面复用）——防「api 挡了、loader 没挡」类缺口。

---

## 12. 测试与护栏（M9）

### 12.1 测试矩阵（单元，核心 = 机制逻辑；phase2 §11.1 场景 1-22 收敛为可编码断言）

| # | 测点 | 断言 | 落点 |
|---|---|---|---|
| 1 | 信号触发（manual 终态 → 建 error run） | pending + trigger_type/version/trigger_signal_id 正确 | auto_schedule 单测 |
| 2 | 活跃闸 1（已有活跃 error run） | 不建第二条 | 同上 |
| 3 | consumed 锚（同 signal_id 重复信号） | return 不建 | 同上 |
| 4 | 坏终态 + 新信号 | 补建 | 同上 |
| 5 | R-3 差集对账（v1+v2 有 run、v3 无） | 补建 v3、v1/v2 不再建 | 对账 worker 单测 |
| 6 | C1 门禁（error suite 无 active case） | 不建 + latest 保持 None | auto_schedule |
| 7 | 哨兵 case 不进 manual 加载 | manual run 不含 case_type 非空 case | case_loader 单测 |
| 8 | error 加载过滤（assertions 缺失/形态错） | 剔除、不建 case、无结果行 | case_loader |
| 9 | 复现成功 + keyword_not_contains 命中 | fail（na_case==0 时 run completed） | orchestrator error 分支集成 |
| 10 | 复现成功不命中 | pass | 同上 |
| 11 | 复现失败 timeout/connect_error 重试尽 | na + error_type 如实落 | 同上 |
| 12 | 调度层未执行（circuit_open） | na + circuit_open（**不落默认 'error'**） | 同上 |
| 13 | 终态写守卫（终态后迟到 EvalResult） | 拒写 + 日志 | 同上 |
| 14 | 独立收尾（0 na → completed / 有 na → partial_failed） | 计数 + status 正确 | `_finish_error_regression` 单测 |
| 15 | 全 na 不落 completed | partial_failed / cancelled（实跑空防御） | 同上 |
| 16 | scanner 回收（pending 卡死 / running 心跳停） | timeout + 未完成 na | scanner 单测 |
| 17 | R-22 三路径回填（scanner/cancel/run 超时） | 未执行 case na + **error_type 钉死 scheduler_unexecuted** | 三路径单测 |
| 18 | error run 不进 active-count / 不占名额 | 互斥计数排除 | api 单测 |
| 19 | rerun 拒绝 error run / error suite 拒 manual 建 | 400 | api 单测 |
| 20 | R-8 节流（invalidated ack 只发一次 + 探测态 + 补齐单次 ack） | 计数/状态机断言 | inbox 单测 |
| 21 | R-3/R-8 ack 分类（400 body / 404 / 5xx） | 对应离线状态 + 重试/停表 | ack 单测 |
| 22 | 幂等重启（游标已落、GET_LOCK 重入） | 进度不丢、不重复消费 | pull_loop 单测 |
| 23 | 共享槽池（同 agent manual 占满 3 → error acquire FIFO 排队；manual 释放 1 → error 该 case 进入） | 计数断言：同 agent 总并发恒 ≤ per_agent_concurrency 且 error 并发 ≤1 | orchestrator error 分支 × limiter 集成 |

### 12.2 R-12/R-20 用例（core/assertions/text.py，phase2 §11.1 已登记）

| 输入 | 期望 |
|---|---|
| answer="" / 纯空白 | keyword_not_contains → **fail**（R-12 收紧） |
| answer 含 keyword | fail |
| answer 不含 keyword | pass |
| answer 缺失 path（复现无 answer 域） | 按 §9.5 防御（na 域，运行期兜底） |

### 12.3 R-19 code 一致性单测（护栏，随 §3.1/§9.6 落）

断言 `runner/error_codes.py` 分类表字面量 **逐字符等于** `executor.py` 常量（enum 取值反射比对，非复制表手抄）；防分类 key 漂移漏掉 → unclean 风暴复发（R-19 复发场景：executor 加新 error_type，分类表漏登记 → 无标注新值默认从严（环境级）→ 误判 unclean_run）。

### 12.4 R-22 收尾 error_type 单测（护栏）

三收尾路径（scanner 回收 / orchestrator cancel / run 级 timeout 收尾）落库断言：① 未执行 case 的 na 行 **error_type=='scheduler_unexecuted'** 且 **run.status∈{timeout,cancelled}**（timeout 永不落成回填 error_type 值，只作 run 终态/na 源字面量）；② **已 pass/fail 终值行不被回填改写**（复跑断言终值行 pass_fail 不变、error_type 仍 NULL）；③ 回填范围 = 实跑集内**尚无 EvalResult** 的 case（§8.7 替代收尾对象界定），已判行零触碰。

### 12.5 验证层级

- 单元：auto_schedule / error_codes / inbox / ack / assertions 逻辑（如上，mvn?——**pytest** 仓库，用 pytest 对齐现库单测目录）。
- 集成：case_loader/orchestrator error 分支/scanner 回收（依赖真 MySQL 的走容器联调环，见 §13 风险）。
- 端到端双端闭环 = 阶段 5 联调（task.md 联调环 0-3），本详设供码。

---

## 13. 风险与开放项

| # | 项 | 等级 | 处置/开放 |
|---|---|---|---|
| 1 | KeywordNotContainsOp 收紧影响存量共享用例 | 高 | R-20 pre-scan 门禁 G0 先行（§9.4），未过不落 R-12 |
| 2 | error run 长跑 × lease/scanner 回收竞态 | 高 | 固定心跳片（§8.5）+ 终态写守卫（§8.6）+ scanner 心跳判据（§8.7）；在途判定丢失显式接受 |
| 3 | 同 agent manual + error 并发槽位（现码 KeyedLimiter 桶间不共享 per-agent sem） | 高 | **M5 前置 = limiter 进程级共享 per-agent 层改造**（§8.3，2026-09-07 裁决 A，向 phase2 R2 权威对齐，不叠加超限）；manual 无 error 并存行为等价需回归；error 排队由 §8.5 心跳豁免 + hard_deadline 兜底；单进程前提 |
| 4 | 多进程 worker 未覆盖 | 中 | §8.3 注明；实施前核对 worker 模型，超 1 需 agent 行执行标记 |
| 5 | 字面量漂移复发 | 中 | R-19 单测护栏（§12.3） |
| 6 | MySQL ENUM ALTER / 迁移回滚 | 低 | §4.5 四迁移含 downgrade；灰度环境先验证 ENUM 语法 |
| 7 | backfill 特权补写覆盖已有 case | 低 | 仅 `assertions IS NULL`（§6.3）+ 幂等 + 补偿对账到清零；上线前确认存量空断言 case 数 |
| 8 | GET_LOCK 跨主机漂移 | 低 | 单机前提；多机需锁迁移方案（实施后另议） |
| 9 | dashboard 数字口径含 error run | 低 | §10.1 口径注记；显式区分 active-count（排除）与全量 run 聚合 |
| 10 | adapter 上限差异（timeout/并发/媒体形态） | 中 | §7.4 预算「依 adapter 实际上限冻结」开放待联调环收敛；媒体 case 复现另议（本稿不设计） |
| 11 | error case 装载 payload 历史积压首次量大 | 中 | 增量游标首拉限量（§5.2 empty-round 规则）+ 差集对账逐版节流（§7.3 至多 1/agent/周期） |
| 12 | R-20 pre-scan 报告结论与现状冲突 | 中 | 报告产出后按 G0 裁决；若存量依赖面大，R-12 可拆分豁免清单先行上线 |
| 13 | 与 online 侧实现清单（R-13/R-16/R-21/R-24）时序 | 中 | 本稿只定 offline 契约面（字段/状态/鉴权）；online 消费在 online 仓库实现清单完成，联调环 1 对齐 |
| 14 | 慢 agent 增量漏拉（单全局水位固有窗口） | 低 | v1 已按 per-agent 独立 since_ts 消解（§5.2）；残留仅 online 装配时钟乱序侧（幂等 upsert 兜底）；如需跨 agent 水位聚合告警，二期再议 |

## 14. 修订记录

| 版本 | 日期 | 内容 |
|---|---|---|
| v0.1 草稿 | 2026-09-07 | Phase B code_detail 首稿：M1-M9 到码映射（现码基线 §2 / 字面量 §3 / 数据模型 §4 / pull_loop §5 / 断言与守卫 §6 / 触发创建 §7 / 执行 §8 / 判定 R-12+R-20 §9 / 只读面 §10 / 隔离与配置 §11 / 测试护栏 §12）。基线 phase1 v0.2.1 + phase2 v0.7.2 + online detail v1.9 / solution v3.5.9 / task.md 环 0 L49。双端未 commit（CLAUDE.md 禁忌），待用户终审。 |
| v0.2 草稿 | 2026-09-07 | 终审逐条拍板落字：§5.2 增量水位改逐 agent 独立 since_ts（消解单全局水位慢-agent 漏拉）；§7.3 补坏终态双路径互补注（撤销「伪矛盾」误判，权威 L237/L260 同存）；§8.3 重写——现码 KeyedLimiter 桶间不共享 per-agent sem（limiter.py 核读事实），error 桶 per_agent=1 串行、叠加上限 manual(≤3)+error(1) 如实接受、limiter 核心不改、进程级 agent 共享闸列后续增强；§9.3 补 G0 与 backflow_enabled 生产联动；§9.4 白名单豁免载体改 pre-scan 逐 suite 评估（对象 = 存量 manual/held_out 用例，非 error case）；§3.4/§12.4 R-22 护栏补「已终值行不改写 + 回填只落实跑集无结果 case」断言；§1.1/§2.1/§4.4/header 清 case_truncated 幽灵（R-10 为 online needs_review 侧 reason 4，offline 无列）；§2.2/§10.1 `_run_out` 行号 193→200（核读值）；§8.6 补 run 终态行锁注（不另造 CAS）；§13 行 3 重写 + 行 14 新增。双端未 commit（CLAUDE.md 禁忌），待用户终审。 |
| v0.3 草稿 | 2026-09-07 | 并发槽池裁决（用户拍板 A）：§8.3 由 v0.2「不改 limiter、接受叠加上限 manual(≤3)+error(1)=4」改为 **M5 前置 = KeyedLimiter 增进程级共享 per-agent 信号量层**（`_agent_sem: dict[str, asyncio.Semaphore]`，容量 = per_agent_concurrency；acquire 序 shared-agent → 桶内；manual/held_out 业务零改动 + 行为等价回归；error ≤1 路 FIFO 排队、同 agent 总并发 ≤ 上限、不叠加超限）——向 phase2 §6.2 step4（R2 共享槽池）权威对齐，撤销 v0.2 叠加接受取向；连带 §1.1 M5 落点加 core/limiter.py / §2.5 结论 3 重写 / §8.2 槽 acquire 措辞 / §12.1 矩阵 + #23 / §13 行 3 重写。双端未 commit（CLAUDE.md 禁忌），待用户终审。 |

---

## 交付核对单（Task #53 回执用）

- [ ] §1 M1-M9 ↔ §4-§12 各章到码映射无悬空
- [ ] 内部交叉引用全部指向真实章节（§x.y 逐一核对，见一致性 pass 结果）
- [ ] 与 phase1 v0.2.1 / phase2 v0.7.2 / detail v1.9 / task.md 环 0 L49 对表：R-3/R-4/R-5/R-8/R-12/R-19/R-20/R-22 逐条命中
- [ ] 现码锚点（§2）行号 = 2026-09-07 实查值；任何实施前漂移需重核
- [ ] 交付回执（不 commit）：告知已写、未写、待用户裁决项
