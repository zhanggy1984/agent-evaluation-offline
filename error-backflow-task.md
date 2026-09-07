# agent-evaluation-offline error 回流改造任务拆解（error-backflow task / WBS）

> 目的：把 `error-backflow-solution_detail.md`（**v1.1 权威详设**，2026-09-07 由 code_detail v0.3 升格 v1.0 权威化 + §12.6 X 系列登记层增补至 v1.1）中"要落代码的事"，按实施顺序门 G0~G6 拆成**可执行任务项**，每项给三要素——内容（代码落点文件 + 权威详设章节）→ **验证目标**（单测断言 / §12.1 矩阵 # / 环场景 / R 护栏），指导排期、认领与验收。本文**不重述方案推导**，只做执行层拆解并锚定权威详设章节。
>
> 权威口径：实施规格 = error-backflow-solution_detail.md **v1.1**（本文唯一直接实施源，正文「详设 §x.y」即其章节）；语义基线链 = 批 1 `error-backflow-phase1.md` **v0.2.2** + 批 2 `error-backflow-phase2.md` **v0.7.2** + 消费契约 online `solution_detail.md` **v1.10**（= v1.9 语义 + §14.5 X 系列登记层，无语义变更）/ `solution.md` **v3.5.9** / online `task.md` 环 0~3（正文「批 1/批 2 §x.y」= 对应权威文档章节，仅当详设正文已引用时才附锚，不臆造次级锚）。原 `error-backflow-code_detail.md` v0.3 已退役归档（只读不维护），不作为实施源。
>
> 事实前提：**代码零落地**（截至 2026-09-07 详设核读，offline `backend/` 无 error-backflow 实现；本文为研发启动首拆）。**文件路径一律相对 `backend/app/`**（真实仓库布局，如 `assertions/ops/text.py`，勿照抄详设个别节标题的 `core/assertions/...` 笔误路径）；详设 §2 行号为核读时点值，**实施时以当时 HEAD 重对一遍再改**。

## 阶段总览（实施顺序门 = 阶段出口验收；任务编号 O-x 区分 online 仓 T-x）

> 执行轨道：阶段 B/C（G1/G2 数据与纯函数）不依赖阶段 A，可与 A 并行；**R-12 改动受 G0 门禁**（未过 G0 不动 `assertions/ops/text.py`，详设 §9.3/§9.4）——A 须在阶段 E 的 R-12 落点前完成即可；D（G3 集成）依赖 C（G2）纯函数绿；E（G4 逻辑层）内 **KeyedLimiter 共享 per-agent 层为 M5 前置**（先 O-E.4 后 O-E.5~O-E.7）；F（G5 环 2）依赖 E 出口；G（G6）护栏可与 E/F 并行补，终态并入 CI。

| 阶段 | 门 | 主题 | M 覆盖 | 出口（本阶段验收） |
|---|---|---|---|---|
| 阶段 A | G0 | R-20 pre-scan 报告（门禁立项，owner = offline 实施） | —（M6 R-12 前置） | `error-backflow-pre-scan-report.md` 产出 → **未过 G0 不动 text.py / R-12**（详设 §1.3 G0 / §9.4） |
| 阶段 B | G1 | M1 数据模型扩列 + 迁移 + 写校验收口 | M1（详设 §4） | 迁移可空跑/回滚（upgrade→downgrade）全绿 + ORM 建表 + `assert_normal_case_golden_fields` 单测绿 |
| 阶段 C | G2 | M2/M3 纯函数层 | M2/M3（详设 §5.3/§5.4/§6.1/§6.4） | `validate_envelope`/映射/状态机谓词/`sanitize_words`/op 登记纯函数单测全绿（**不触库**） |
| 阶段 D | G3 | M2/M3 集成 = pull_loop 打通 | M2/M3（详设 §5） | fake-online stub → error suite/case + ack 闭环 + R-8 节流 + requeue/manual_invalidate 竞态 |
| 阶段 E | G4 | M4/M5/M6 逻辑层 | M4/M5/M6（详设 §7/§8/§9） | 自动触发/执行/收尾/判定单测全绿（mock `execute_case`；含 R-3 差集、limiter per-agent 层、R-22、R-12） |
| 阶段 F | G5 | M7/M8 + 环 2 双端走查 | M7/M8 + M2~M8（详设 §10/§11） | 只读面 + 隔离 + config 就位；场景 1-22 offline 侧真跑 + online fake（R-4/R-5 透出核对） |
| 阶段 G | G6 | M9 护栏并入 CI | M9（详设 §12） | R-19 code 一致性单测 + R-22 收尾单测并入 CI + §12.1 矩阵全绿 |

---

## 阶段 A｜G0：R-20 pre-scan 报告（前置门禁）

> 对应 online task.md 环 0 L49 登记（详设 §9.4 同引）。R-12 是 offline `KeywordNotContainsOp` 空答语义修补——`text.py` 是 manual/held_out **共享判定算子**，收紧会让存量 keyword 断言从「空答 = 不命中 = pass」翻转为「空答 = fail」；须先证存量无不可承受依赖、白名单可豁免，再改判，防误伤。

- **O-A.1 R-20 pre-scan 现网扫描（三问三答 + 处置登记）**：扫现库 `assertion_results` / `case.assertions` / keyword 算子，**产出 `error-backflow-pre-scan-report.md`**（存仓库根或 docs/），内容 = 详设 §9.4 三问：① 存量 keyword_contains/not_contains 断言下空字段回答被放行（PASS）的用例数、suite/agent/case 依赖面；② 哪些 **manual/held_out 存量普通 suite/case**（非 error case）的合法业务语义允许空答 → **豁免白名单**逐 suite 评估登记（载体不进断言逻辑、不加 per-case 断言分支，实施裁决按受影响面定：小→直接收紧 / 大→豁免清单先行分批合入，详设 §13 行 12）；③ 上线形态 = 非白名单空答显式判 FAIL、白名单走豁免。代码落点 = 不改 backend 代码。**验证目标**：报告三问有结论 + 白名单豁免落位 → **G0 出口**（详设 §9.4），未过 G0 阶段 E 的 R-12 任务（O-E.9）不得开工。

## 阶段 B｜G1：M1 数据模型与迁移

> 详设 §4 改动全在 `models/case.py` / `models/run.py` / 新 `models/error_backflow_inbox.py`。**普通 case 语义不变**（error 哨兵全 nullable/NULL），迁移可回滚是硬要求。

- **O-B.1 TestSuite + TestCase 扩列（`models/case.py`）**：`TestSuite` 加 `is_error_suite`（BOOLEAN，default False，详设 §4.2）；`TestCase` 加 `case_type`（VARCHAR(32) NULL，v1 唯一值 `'regression_error'`）/ `payload_id`（VARCHAR(64) NULL + `uk_case_payload_id`）/ `backflow_envelope`（JSON NULL），且 `expected/assertions/metrics` 三列由 `nullable=False` **ALTER 放宽**（详设 §4.1；放宽后普通 case 必填由 §4.6 守卫保证）。**验证目标**：ORM 建模编译过；新列全 NULL 下旧 normal case 行为不变。
- **O-B.2 EvalRun 扩列（`models/run.py`）**：`TRIGGER_TYPE` L14 → `("manual","held_out","error_regression")`；加 `trigger_signal_id`（BIGINT NULL，consumed 锚，记触发信号 run id）/ `excluded_case_ids`（JSON NULL，R-4 cap 截断挤出的最老溢出 case id 列表；**诚实标记 = 非空**，详设 §4.4）。**核对 R-10**：`input_truncated` 为 online needs_review 侧 reason 4 标记，offline **EvalRun 不加截断列**、无对应列（只核对不落码，详设 §4.4 L242）。**验证目标**：ORM 建表；trigger 值域含 error_regression。
- **O-B.3 新模型 `models/error_backflow_inbox.py`（error_backflow_inbox 表）**：全列 + `uk_inbox_payload` 唯一约束 + `idx_status_ack` 索引，DDL 对齐批 1 §5.2（详设 §4.3）。**验证目标**：ORM 可建表。
- **O-B.4 alembic 迁移（4 个新迁移，顺序 + 可回滚，续 head `b1a2c3d4e5f6`）**：rev1 `add_error_backflow_case_cols`（case.py 加列 + 三列 DROP NOT NULL + is_error_suite，down = 删列 + 回 NOT NULL + 撤 uk）/ rev2 `create_error_backflow_inbox`（inbox 全列 + uk + idx，down = drop）/ rev3 `extend_run_trigger_and_anchor`（ALTER ENUM 含 error_regression + trigger_signal_id + excluded_case_ids，down 还原）/ rev4 `backfill_case_columns`（数据迁移，确认无现网 error_backflow 数据，零落地前提）（详设 §4.5）。**验证目标**：`upgrade head` 后 ORM `metadata.create_all` 不冲突（跑 backend/tests 建表冒烟）；`downgrade -1` 逐级回滚成功（**G1 门**）。
- **O-B.5 写校验收口（`core/case_rules.py` 新增纯函数，批 1 评审 A-I6）**：`assert_normal_case_golden_fields(case_type_is_null, fields)`——普通 case（case_type IS NULL）必填三列非空；error case 锁 expected/metrics 恒 NULL、放行 assertions（详设 §4.6）；接入 `api/cases.py` `create_case`/`update_case` 两处普通分支（error 分支专属校验留 pull_loop 建 case，O-D.3；普通 input schema 不得驳回 error case）。**验证目标**：纯函数单测——普通 case 三列缺一抛 ApiError、error case 传非 NULL expected/metrics 抛、放行 assertions。

**阶段出口（G1）**：O-B.1~O-B.4 迁移可空跑/回滚 + ORM 建表 + O-B.5 单测绿（详设 §1.3 G1）。

## 阶段 C｜G2：M2/M3 纯函数层单测（不触库）

> 纯函数先行：内容可脱离 DB/对端单测，G2 全绿后才进 D 集成。

- **O-C.1 `validate_envelope` 纯函数（新 `core/error_payload.py`）**：信封结构校验（schema_version 支持 / case_type 白名单 / 必填字段 / 类型）+ verdict 常量（content_gap/version_drift 判定产出）（详设 §5.3，文件归属 §5.1；D19 信封 schema 锚批 1 §2.1）。**验证目标**：畸形信封 → (False, errors, verdict)、合法 D19 信封 → (True, [], ok) 纯函数单测矩阵。
- **O-C.2 映射纯函数 `resolve_agent` / `resolve_interface`**：信封 → offline 实体名归一（interface 归一独立、无别名依赖）（详设 §5.4 + 批 1 §6.7 source 映射 C4）。**验证目标**：agent/interface 命中与不命中（→ offline_cap_gap 前置）单测。
- **O-C.3 inbox 状态机迁移校验纯函数**：`_mark` 非法组合校验（状态/ack_status/字段组合合法性，集中迁移入口）（详设 §5.6 + §3.3 状态机表）；三扫描谓词常量 `NEEDS_ACK` / `STUCK_NEW` / `CAP_GAP_PROBE` 定义（详设 §5.6）。**验证目标**：状态机转移矩阵单测——非法迁移拒绝、合法迁移通过、谓词行选择正确（acked/blocked 不重放）。
- **O-C.4 词级净化 `sanitize_words` 纯函数**：strip + 拒空串/拒纯空白/拒超长词 + 大小写归一；净化后空 → 报错拒激活 fail-closed（详设 §6.1）。**验证目标**：空词/纯空白/超长/混大小写单测；净化空列表 → 拒。
- **O-C.5 keyword_not_contains 发布一致性登记（静态侧，`core/constants.py`）**：`_ASSERTION_OPS` 增补 `"assertions.ops.text.KeywordNotContainsOp"` + `ASSERTION_OP_CLASS` 增补 `"keyword_not_contains"` 键 + `ALLOWED_CLASS_PATHS` 含其 class_path（详设 §6.4 步 1/3；**DB 行 + seed 幂等留阶段 D**）。**验证目标**：registry 能解析/构造 KeywordNotContainsOp（不触 DB）。

**阶段出口（G2）**：O-C.1~O-C.5 纯函数单测全绿（不触库，详设 §1.3 G2）。

## 阶段 D｜G3：M2/M3 集成 = pull_loop 打通

> 依赖阶段 C 纯函数绿 + 阶段 B 迁移。建 fake-online stub（O-D.5）先行驱动。

- **O-D.1 pull_loop 骨架（新 `runner/pull_loop.py` + inbox 数据访问层）**：`BackflowPullLoop` + 模块单例 + `pull_loop_worker()`——`while True` 仿 scanner `scanner_loop` + `asyncio.sleep(pull_interval)`；**五步单轮**：GET_LOCK('backflow_pull_loop') 单例互斥 → ① ack 对账扫描 → ② inbox 卡住重扫 → ③ 增量拉取 → ④ 低频自愈扫描；每步独立 try/except（详设 §5.1）；inbox DAO `_upsert_inbox` / `_mark`（状态迁移入口）/ 扫描谓词（详设 §5.6）。**验证目标**：GET_LOCK 单飞 + 一步炸不退出循环 + upsert 幂等（payload_id 命中返回既有行）单测。
- **O-D.2 增量拉取（逐 agent + R1 assembled_ts 锚）**：游标 since_ts **per-agent 独立**（`{agent_name: iso8601}`，存 `backflow_pull_token`/`backflow_since_ts` config，空 = 该 agent 未拉过）；拉取 → inbox upsert 去重；slow agent 水位独立不回退（详设 §5.2 + §11.2 config keys + 批 1 §6.2）。**验证目标**：stub 多 payload 增量不漏不重；慢 agent 独立水位（§13 风险 14 消解）。
- **O-D.3 逐 payload 处理 + 建 error case/suite（同事务）**：inbox upsert → `validate_envelope` 判 content_gap/version_drift → rejected + ack invalidated；`resolve_agent/interface` 不命中 → rejected + `offline_cap_gap` + ack invalidated；命中 → **同事务** error suite upsert（`agent_id AND is_error_suite=true` 定位，不依赖名字）+ 插 error case（case_type='regression_error'/payload_id/input 纯装载不改写/input_type conversation|text/assertions=激活器同事务写恰一条 keyword_not_contains（O-C.5 登记后））+ inbox → case_created + ack active；**顺序铁律 = 本地先落库 → 再发 ack**（详设 §5.5，字段装载表 + 批 1 §6.4 两阶段崩溃洞）。**验证目标**：stub 驱动下单 payload → error suite/case 建出、assertions 恰一条、inbox case_created；两崩溃洞顺序断言（commit 前不发 ack）。
- **O-D.4 ack 发送/对账 + R-8 节流 + requeue/manual_invalidate 竞态**：`send_ack`（active/invalidated，前置矩阵）；404 ERR_PULL_0003 → blocked + 人工核查（与 400 分开）；400 ERR_CLUSTER_0003 解析 offline_status/invalidate_reason → manual_invalidate 走 §5.9 竞态对账 / 否则有界重试落 blocked；对账扫描重放 NEEDS_ACK（幂等 200 即 acked）；**R-8 cap_gap 探测态节流**：rejected+offline_cap_gap 已 acked 行每小时本地重跑映射——补齐 → 建 case + 单次 ack active（契约 R2 `invalidated(offline_cap_gap)→active`）/仍缺 → 静默等轮，**不复位 ack_status、不重发 invalidated**（复位重处理仅限 ack_status ∈ {none,pending} 或收到 requeue/内容刷新）；online_content_gap 等 requeue 重拉覆盖；manual_invalidate 本地作废已建 case + inbox 落 rejected+manual_invalidate+acked（详设 §5.7/§5.8/§5.9）。**验证目标**：对账幂等不重发；R-8 探测态不风暴（节流计数断言，低频每小时）；requeue 后重扫自愈；manual_invalidate 本地作废。
- **O-D.5 fake-online stub 基建（环 1，`backend/tests/`）**：fixture 回放 online pull/ack 响应 + 双向认证形状（R1~R3 + R-4~R-24 修订契约 shape），供 O-D.2~O-D.4 驱动（详设 §12.5 验证层级 + 批 1 §12.2 环 1：fixture + fake-online，无真实 online，C3 只入测试基建）。**验证目标**：stub 驱动收单/建 case/ack 全链路仓内全绿。

**阶段出口（G3）**：pull_loop 打通 → error suite/case + ack 闭环 + 详设 §12.6 X-1~X-7（环 1 集成异常/边界用例）全绿（详设 §1.3 G3）。

## 阶段 E｜G4：M4/M5/M6 逻辑层

> **M5 前置**：O-E.4（KeyedLimiter 进程级共享 per-agent 层）先于 O-E.5~O-E.7（详设 §1.1 M5 / §8.3）。O-E.9（R-12）受 G0 门禁。

- **O-E.1 信号挂接 + `maybe_auto_schedule`（M4，新 `runner/auto_schedule.py`）**：信号 = 到达终态（非 pending/running）的 `trigger_type ∈ {manual, held_out}` run（error_regression run 自身不作信号，防递归）；orchestrator `_finish` L560 commit **之后** fire-and-forget（`asyncio.create_task`，失败仅记日志；**不得在持 eval_run 行 FOR UPDATE 锁事务内调用**——反向锁序死锁）；`maybe_auto_schedule(db, agent_id, version, signal_run)`：agent 行锁 → error suite 门禁（C1：无 active case → 不建、latest 保持 None）→ consumed 锚（latest.trigger_signal_id==signal_run.id → return）→ 活跃闸 1（同 agent 活跃 error run ≥1 → 不建）→ 坏终态（timeout/cancelled）仅当新信号未消费 → 补建；runnable 谓词与 §8.1 执行加载**同一谓词**（详设 §7.1/§7.2，批 2 §5.2 终态语义 + 锁序约定）。**验证目标**：§12.1 #1（信号终态 → 建 error run + trigger_signal_id 正确）/ #2（活跃闸不建第二条）/ #3（consumed 锚同信号重复不建）/ #4（坏终态 + 新信号补建）/ #6（C1 门禁 latest 保持 None）单测。
- **O-E.2 R-3 版本差集对账 worker（M4）**：低频任务（并入 scanner 周期或独立 task）对**每个有 active error case 的 agent**：`diff = 该 agent 已到终态 manual/held_out distinct version（排除 error_regression 自身）− 已有 error run 的 version（含 timeout/cancelled 视为有 run）`；对 diff 内每缺版逐版补建（每版取最近终态 run 为锚 → maybe_auto_schedule），**活跃闸：一周期至多补 1**；吸收态与差集正交（坏终态不矛盾）；兼任 §6.3 backfill 清零对账（`assertions IS NULL` 的 active error case 特权补写幂等直到清零，清零后 C1 放行；**特权重写 + 仅空值哨兵**，绕过 `update_case` 403，新函数如 `runner/backfill.py::backfill_error_case_assertions`，详设 §6.3/§7.3）。**验证目标**：§12.1 #5（v1+v2 有 run、v3 无 → 补 v3、v1/v2 不再建）；backfill 幂等（仅补 NULL，不覆盖激活器已写）；洪峰 3 版连发 → 3 run 全建出（环 2 场景 17 前置）。
- **O-E.3 内部创建器 `_create_error_regression_run` + api 配套（M4）**：内部直插 EvalRun（**不走公开 create_run**，绕过 trigger Literal/`_max_active_runs`/semver）+ case_ids newest-active-first 取至 cap + `excluded_case_ids` 截断诚实 + 初始 lease（now+90s）+ error-run 专用 run_config 预算（case_timeout=600/max_retries=2/hard_deadline 含闸等待）+ 发 `orchestrator.start_run(run_id)`（详设 §7.4 参数表）；`api/runs.py` 配套：version `_SEMVER` 强校验移除 → 共享域校验、活跃计数扫描排除 error run（create_run L251-253/rerun L378 加 `trigger_type != 'error_regression'`）、error suite 拒建 manual run、rerun 对 error_regression 显式拒绝（详设 §7.5 项 1-4 + 批 2 §9.2/§6.7）。**验证目标**：创建参数表逐字段断言（含 excluded_case_ids cap 溢出、pinned、lease 非空）；error run 不占 `_max_active_runs` 名额不堵 manual。
- **O-E.4 KeyedLimiter 进程级共享 per-agent 层（M5 前置，`core/limiter.py`）**：现码 = per-agent sem 嵌套在各 run 独立桶内（桶间不共享）；改造 = `KeyedLimiter` 增 `_agent_sem: dict[str, asyncio.Semaphore]`（key = agent.name，容量 = 首次执行时 `per_agent_concurrency`，默认 3）；`acquire(run_id, agent)` 序 = **shared-agent → 桶内**，失败/异常逆序释放，`release` 逆序；未 `set_run` 走 `_default` 桶的调用带 agent 亦先取 shared-agent；manual/held_out **业务零改动** + 行为等价（无 error run 并存时 manual 单 run 仍 ≤ per_agent_concurrency）；error 桶 = `set_run_limits(run_id, global_limit=1, per_agent=1)`，同 agent manual 占满时 error FIFO 排队、槽释放即进 → **同 agent 总并发恒 ≤ 上限、error ≤1 路、不叠加超限**；容量不可变 → 运行期改并发需重启 worker；排队饿死兜底 = error-run hard_deadline 超时回收 + §8.5 心跳续租（详设 §8.3 步 1-5，撤销 v0.2「接受叠加 ≤4」取向）。**验证目标**：§12.1 #23（同 agent 总并发 ≤ 上限 + error ≤1 + manual 并发回归等价）；FIFO 排队不超限单测。
- **O-E.5 case_loader error 分支（M5，`runner/case_loader.py` L20-32 改写）**：`_load_run_cases` 加 error_regression 分支——`suite_id==run.suite_id AND status='active' AND case_type IS NOT NULL AND is_held_out==False AND assertions IS NOT NULL 且形态 keyword_not_contains`（§9.5 素材缺失前置过滤，加载即剔除不建 run case）；返回集按加载时持久化或按同一谓词重算，使 §8.7 回填对象边界确定（详设 §8.1 + §1.1 M5）。**验证目标**：§12.1 #7（哨兵 case 不进 manual 加载）/ #8（assertions 缺失/形态错 → 剔除、不建 case、无结果行）单测。
- **O-E.6 orchestrator error 分支 `_run_error` + 熔断隔离 + 心跳续租（M5）**：`_run` L135 后按 `trigger_type=='error_regression'` 分发独立 `_run_error(run_id)`（不复用普通 `_run` 后续，复用 `_run_one` 之下机制）：D8 前置校验（pinned==True + case_ids 显式非空 + suite 属该 agent error suite，不满足 → skip + 告警不真打被测 agent）；跳过 `_probe_before_run`（探活对 error 零增益）；执行循环——槽 acquire（O-E.4 共享层）+ 独立 breaker key `f"{agent_id}:error_regression"`（不共用 manual breaker，打开 → circuit_open → na）+ `executor.execute_case` 复现（成功 → verifier 判 pass/fail 一次写终值；失败 error_type 非空 → RETRYABLE 重试尽 → na + error_type 如实落，不改判）；调度层未执行（circuit_open/interface_disabled 默认 'error' 落库路径）→ **拦截** → na + error_type 原值（不落默认 'error' 行）；落 EvalResult 走 `_ensure_case_version` 惰性快照（§8.8，不预建不旁路）；**心跳续租固定时间片**（间隔 ≪ lease TTL，如 15s，覆盖单 case 执行窗口 + 排队等待期，不得仅以每 case 完成为粒度）（详设 §8.2/§8.4/§8.5/§8.8）。**验证目标**：§12.1 #9（复现成功命中 → fail）/ #10（不命中 → pass）/ #11（timeout/connect_error 重试尽 → na + error_type 如实）/ #12（circuit_open → na + circuit_open 不落 'error'）集成单测（mock execute_case）。
- **O-E.7 独立收尾 `_finish_error_regression` + scanner 短路 + R-22（M5）**：新增 `_finish_error_regression`（**不复用共享 `_finish`**——其对账回填 'error'/全 na 落 completed/按 'error' 计 error_case 与 error 语义冲突）：0 na → completed / 有 ≥1 na → partial_failed / 未跑完被取消超时 → timeout|cancelled + 已完成 case 保留终值 + 未完成回填 na；na_case 计数、error_case 恒 0、agent_score 恒 NULL、completed 仅当 0 na；**实跑口径**（实跑集 < case_ids 的 case → results 返回空，online 按缺行≠na 轮询）；实跑集为空防御（C1）→ 不落 completed 落 cancelled + 告警；na 回填与 run 终态**同事务同 db session 直插**（沿用共享 `_finish` L536-546 FOR UPDATE 锁模式，uk_result 幂等兜底）；**终态写守卫** = 终态后迟到 EvalResult 一律拒绝（uk_result 撞键丢弃迟到行）；**R-22 = 回填 na 的 error_type 钉死 `scheduler_unexecuted`，timeout 只作 case 级 na 源不作回填 error_type**（详设 §8.6 行 3 + §3.4 不变量）；scanner `_reap_one_pass` L112/L115 对 `score_run_salvage` 加守卫 `if run.trigger_type=='error_regression': 走 error 替代收尾（短路 salvage，不产 agent_score）`；替代收尾置 run timeout + 实跑集内未完成 case 回填 na（含 pending 卡死回收特判 + running 心跳停回收，lease 未过期豁免）——**R-22 三路径同源**（scanner 回收 / orchestrator cancel / run 级超时，详设 §8.7）。**验证目标**：§12.1 #14（0 na → completed / 有 na → partial_failed）/ #15（全 na 不落 completed）/ #16（scanner 回收 timeout + 未完成 na）/ #17（R-22 三路径回填 error_type 钉死 scheduler_unexecuted）/ #13（终态写守卫拒迟到行）单测。
- **O-E.8 verifier no_fallback 调用点 + 素材缺失运行期防御（M6）**：error 分支成功复现后调 `run_assertions(run, case, unified)`（锚 `assertions/run.py` L12-40），输入 = 复现 unified dict（case/answer/pass_fail 域 + case.assertions 已装配链 keyword_not_contains），返回布尔化 → pass/fail **一次写终值**（不走 `_save_result` 默认 pass_fail='error' 路径、无「先写 pass 占位再修正」）；判定 = executor 成功 ∧ no_fallback 布尔化，**复现成败恒 > 素材真假**（复现挂了 = na 无关 answer）（详设 §9.1/§9.2 + 批 2 §7.1/§7.2 D1/D2）；运行期兜底 = 加载后残留缺失/形态不符 case 按 `missing_assertion`/`assertion_shape` na、run 走 partial_failed 不 crash、**空断言（[]）绝不判 pass**（详设 §9.5）。**验证目标**：no_fallback 命中 → fail / 不命中 → pass / 空词表 fail-closed；na 源 error_type 在值域内（O-G.1 R-19 前置核对）。
- **O-E.9 R-12 text.py 空答 fail（依赖 G0 已过；未过 G0 不开工）**：`assertions/ops/text.py` —— `KeywordNotContainsOp.run()`（L33-62）对空/纯空白 answer：现况天然 PASS（`keyword_contains` PASS、not_contains 未收紧）→ 改 `if not isinstance(text, str) or not text.strip(): return FAIL（空/纯空白应答 = 复现空答 leakage 证据）`，其余原逻辑不变（keywords 归一 → 检查命中 → 命中即 FAIL）；**语义钉死**：空答 fail = leakage 空话术复发证据，**不是 na**（na = 复现没跑到，fail = 跑到且空答，两路径不可混，phase2 §7.2 定性）；共享算子 → 白名单豁免用例走 O-A.1 产物（详设 §9.3 + §9.4 + §12.2）。**验证目标**：空答/纯空白 → FAIL；非空不命中 → PASS；命中 → FAIL；白名单豁免用例仍 PASS（回归）。**G0 放行后才允许合入**。

**阶段出口（G4）**：O-E.1~O-E.9 逻辑层单测全绿（mock `execute_case`，详设 §1.3 G4）+ R-12（O-E.9）受 G0 放行已合入。

## 阶段 F｜G5：M7/M8 + 环 2 双端走查

- **O-F.1 runs list/detail 只读输出扩展（M7，`api/runs.py` `_run_out` L200-221 基座）**：error_regression run 透出 `trigger_type='error_regression'` / `version`（fix_version）/ `trigger_signal_id`（R-16 缺行比对）/ `excluded_case_ids`（**R-16 落位点**：online runs list/detail 取此判「窗口外欠测」）/ `suite_id` / per-case 结果行（pass/fail/na + na 的 error_type）；`_run_out` 增列**只加不改**（不 rename 不重排，结果明细沿用既有 per-case 通道 EvalResult，不加 offline 专属终态列，R-6 载体承诺）；普通 suite/case 只读面排除 error 对象（详设 §10.1）。**验证目标**：error run 透出字段与 online detail §8.7 消费契约逐字段对拍（环 2 用 online fake 核对）；普通面零 error 污染。
- **O-F.2 R-4/R-5 只读检索面（M7，error 侧显式端点）**：新增 error 侧显式只读面（**不与普通 suite/case 面混**，文件可新增如 `api/platform_runs.py` + `main.py` 挂 router，详设 §5.1）：runs 过滤 `trigger_type='error_regression' AND suite_id=:sid`（判 K 连续、R-16 缺行）；error case 面 = error suite 下 active case 的 `case_type/payload_id/assertions`（装配后链）只读；**R-4** = `excluded_case_ids` 溢出列表透出（诚实标记非空、溢出计数 len）；**R-5** = agent 已见版本读面（error run distinct version，判 K 序列数据源）；白名单过滤谓词统一 = §6.5 三谓词只读面同一份（详设 §10.2 + 批 2 §9.3/§9.4）。**验证目标**：R-4/R-5 响应形状与 online detail §8.7 / R-4/R-5 判据字段对拍。
- **O-F.3 外部检索鉴权（M7，`core/security.py` 扩）**：现有 security 之上加 service token 校验——JWT HS256、`type=service`、scope 内（claim 面/error 面分 scope），签发/轮换走 system_config 或本地密钥文件**不入库明文**；error 只读端点须带 service token（防人工误检索旁路隔离语义）；manual 人工面现有认证不动（详设 §10.3 + 批 1 §9 service token helper）。**验证目标**：service token 可访问 error 只读端点、人工 token 拒绝；无 token 401。
- **O-F.4 存量评测语义零污染（M8 三层隔离 + dashboard 排除 + cleanup 豁免）**：数据面（error case 带哨兵不进 manual/held_out 加载，O-E.5 已覆盖）；行为面（error suite 拒建 manual run / error_regression 拒公开 start/rerun，O-E.3 已覆盖，核对）；只读面 active-count/名额互斥计数排除 error run（O-E.3 已覆盖）+ dashboard 数字口径排除——`api/dashboard.py` 聚合若走 runs 表会含 error run 行 → 与 active-count 同排（口径注记，批 2 §9.1 全聚合排除）；`runner/cleanup_rules.py` 免清理核对（error run/error case 不占清理位，批 2 §6.5）；写守卫谓词单一来源 = §6.5 全量写守卫 + §10.2 只读过滤谓词收敛共享常量集（`CASE_TYPE_IS_NULL`/`IS_ERROR_SUITE_FALSE`/`TRIGGER_NOT_ERROR_REGRESSION`，一处定义 api/case_loader/只读面复用，防「api 挡了 loader 没挡」，详设 §11.1/§11.4 + §6.5）。**验证目标**：dashboard 无 error run 混入；cleanup 不误清 error case；error suite/case 全写端点 403（§6.5 全清单核对：update_case/invalidate_case/add_annotation/add_case_scenes + create_suite/update_suite 禁人工置 is_error_suite + list_suites 透出标注）。
- **O-F.5 config keys + 启动挂接（M8，`seed.py` DEFAULT_SYSTEM_CONFIG L345-422 段 + `main.py` startup）**：system_config seed 加 `backflow_enabled`（false 总开关，false 停 worker/调度/差集对账保留已建数据）/ `backflow_pull_token` / `backflow_since_ts`（json 逐 agent）/ `backflow_poll_interval`（60）/ `backflow_hard_lease_sec` / `backflow_heartbeat_sec`（15）+ error-run 预算/breaker/并发组（按 adapter）；seed 缺省关闭态、代码路径不做隐式 on；`main.py` startup 门控 `backflow_enabled`：迁移检查（表/列/索引在，缺失 → fail fast）+ 注册 pull_loop worker + 差集对账低频 task + cap_gap probe task；shutdown 取消 task 幂等游标不丢（详设 §11.2/§11.3）。**验证目标**：关闭态零副作用（未配 backflow 前 DB 空表可跑）；开启后 worker 注册/停 worker 幂等。
- **O-F.6 环 2 双端走查（offline 侧真跑 + online fake）**：本地共享 infra（MySQL 分库）下走 phase2 §11.2 场景 1-22 offline 侧 + online fake 消费（R-3 差集补建 / R-4 excluded / R-5 versions 透出核对 / R-16 缺行比对输入侧），对应 online task.md 环 2 出口（E-1~E-29 中 offline 侧用例，E-23~E-29 修订包端到端）（详设 §12.5 验证层级 + 批 2 §11.2）。**验证目标**：场景 1-22 offline 侧全走通；online fake 读到 R-4/R-5/R-16 字段位。

**阶段出口（G5）**：环 2 走查全绿 + 详设 §12.6 X-8~X-11（环 2 双端异常/只读面对拍用例）全绿（详设 §1.3 G5）。

## 阶段 G｜G6：M9 护栏并入 CI

> 护栏 = 防系统性误伤的**行为锁**（非一次性核对），随对应代码落地即写、并入 CI。

- **O-G.1 R-19 error_codes 一致性单测（护栏）**：`runner/error_codes.py` 字面量 + 分类表（§3.1 error_type 权威字面量域）**必须逐字符等于** executor.py 常量全集（详设 §2.4：executor 错误常量 L22-31 + `RETRYABLE_ERRORS` L38）；na 值域 ⊆ phase2 §6.4 na 分类矩阵值；单测钉死任一 key 漂移（拼写/长短名/大小写）→ 测试红（防 executor 加常量漏分类 → 未知 error_type 默认从严环境级 → 假 unclean_run 风暴复发）（详设 §3.1/§9.6 + 批 2 §6.4 R-19）。**验证目标**：§12.3 单测并入 CI，常量增改即红。
- **O-G.2 R-22 收尾回填 error_type 单测护栏**：scanner 回收 / orchestrator cancel / run 级超时收尾三路径回填 `scheduler_unexecuted` 非空 + **已终值行不改写** + 回填只落实跑集无结果 case 断言（详设 §3.4 + §12.4 R-22）。**验证目标**：§12.4 单测并入 CI（复用 O-E.7 断言，抽为独立护栏文件）。
- **O-G.3 §12.1 测试矩阵全绿核对**：详设 §12.1 测试矩阵逐条通过（#1-17 + KeyedLimiter #23 + R-8/R-12 用例 §12.2 等行）登记到集成报告（详设 §12.1/§12.2）。**验证目标**：矩阵全绿 = G6 出口。

**阶段出口（G6）**：R-19/R-22 护栏单测并入 CI + §12.1 矩阵全绿（详设 §1.3 G6）。

---

## 横切实现/核对项登记（独立立项 + CI 护栏，来源 = online task.md 环 0 L49 登记 / 详设注记）

| R | 内容 | 性质 | 落点任务 | 门 | 验收锚 |
|---|---|---|---|---|---|
| R-3 | 版本差集对账（online 已发版无 error run 迟到补建） | offline 机制 | O-E.2 | G4 | §12.1 #5；环 2 场景 17；online E-27 输入侧 |
| R-4 | cap 溢出 excluded_case_ids 列表透出（诚实标记 = 非空） | offline 只读面 | O-F.2（列在 O-B.2 模型） | G5 | 响应字段对拍 detail §8.7 |
| R-5 | agent 已见版本只读面（error run distinct version） | offline 只读面 | O-F.2 | G5 | versions 响应 = claim 软校验/K 序列数据源 |
| R-8 | cap_gap 探测态节流（invalidated ack 只发一次 + 本地探测补齐） | offline ack 语义 | O-D.4 | G3 | 探测节流断言；§12.1 R-8 行 |
| R-10 | offline 零机制（`input_truncated` = online needs_review 侧 reason 4，EvalRun 无截断列） | 零机制核对锚 | O-B.2 | G1 | §4.4 L242 核对（EvalRun 不加截断列，仅核对不落码） |
| R-12 | text.py 空答 fail 修补 | offline code（G0 门禁先行） | O-A.1 → O-E.9 | G0→G4 | 空答 FAIL 单测 + 白名单豁免回归；§12.2 |
| R-19 | error_type 字面量一致性单测护栏 | CI 护栏 | O-G.1 | G6 | §12.3 单测并入 CI，常量增改即红 |
| R-20 | pre-scan 报告门禁（三问三答 + 处置登记） | 实施门禁（owner = offline 实施） | O-A.1 | G0 | 报告产出 = G0 出口 |
| R-22 | 收尾回填 na 统一 `scheduler_unexecuted` 单测护栏 | CI 护栏 | O-E.7 + O-G.2 | G4+G6 | §12.4 三路径回填非空断言并入 CI |

## 不做清单（归属明确，勿误入本 task 范围）

- **online 侧实现清单**：R-13 同簇刷新复制（detail §6.2）、R-16 excluded_case_ids 落 runs list/detail 字段位、R-21 `trace_judge_state.root_late_complement` + 消费 step4 root-late 补判、R-24 requeue guard 状态域——归 **online 仓**（online task.md 环 2 E-28/E-29 + L49 实现清单）；offline 仅 M7 只读面字段位对齐（O-F.1/O-F.2）。
- **二期**：L3 quality/会话型回归、Kafka 传输（HTTP pull 保持）、error case/inbox 长期驻留归档策略、`inconclusive` 三值判定、error run 无信号自动重跑、跨端 deactivate/case 归档闭环、离线触发覆写（详设 §1.2 + 批 1 §1.3/批 2 §1.2）。
- **前端只读标注**（error suite 只读样式）：归前端仓（详设 §1.2）。
- 平台本体（manual/held_out 评测主链）非 error 语义的既有行为**不回归性改动**——凡 O-* 触及的改动都以「manual/held_out 行为等价」为护栏（详设 §8.3 步 2 / §11.1 三层隔离）。

## 测试与联调环归属

- **环 0（契约）**：已由批 1 契约定稿（pull/ack/回写 + schema_version + case_type 白名单 + 双向认证 + 游标/ack 语义 + R1~R3/R-4~R-24 修订）——本 task 的前置输入，实施以环 0 契约形状造 fixture/stub 样例（O-D.5）。
- **环 1（单端 stub）**：offline 侧 = 阶段 C/D（O-C.* 纯函数 + O-D.5 fake-online stub + O-D.2~O-D.4 收单/建 case/ack 闭环），仓内全绿无需真对端（详设 §12.5 + 批 1 §12.2）；集成异常/边界验收用例 = 详设 §12.6 X-1~X-7。
- **环 2（双端集成）**：阶段 F（O-F.1~O-F.6），同机双端 + 本地共享 infra（MySQL 分库），走 phase2 §11.2 场景 1-22 + online E-1~E-29 中 offline 侧依赖用例；集成异常/边界验收用例 = 详设 §12.6 X-8~X-11（X-9/X-10 对 online X-11 对端面）。与 online task.md 环 2 出口一致。
- **环 3（真实流量灰度）**：不在本 task（属放量阶段，online task.md 阶段 5；backflow_enabled 上线隐含 G0 已过/R-12 已合入，详设 §9.3）。

## 风险与关注（本 task 层面）

- **G0 先行**：R-12（O-E.9）未过 G0 不得开工；pre-scan 与现网结论冲突时按详设 §9.4/§13 行 12 处置（受影响面大 → 豁免清单先行、分批合入，不在详设写死豁免实现）。
- **与 online 实现清单时序**：M7 只读面字段位（O-F.1/O-F.2）须与 online 消费侧（R-13/R-16/R-21/R-24）在环 2 前对齐；offline 只定契约面，机制在 online 仓。
- **现码行号漂移**：详设 §2/正文行号是 2026-09-07 核读值，实施以当时 HEAD 重对；任何漂移先改详设再动代码。
- **护栏回归**：KeyedLimiter 进程级层（O-E.4）、case_loader error 谓词（O-E.5）、共享 `_finish` 隔离（O-E.7）必须保住 manual/held_out 行为等价——回归断言是硬要求（详设 §8.3 步 2 / §12.1 #23）。
- **迁移与建表冲突**：O-B.4 迁移与 ORM `create_all` 双轨并存（详设 §4.5 验证行），先迁移后冒烟。
- **详设内部路径笔误提醒**：详设 §9.3 标题与 §12.2 标题的 `core/assertions/text.py` 与 §2.4/§6.4 class_path 的 `assertions/ops/text.py` 不一致——**真实文件 = `backend/app/assertions/ops/text.py`**（§2.4 L92 已实证；本 task 全篇以此为准；改码前先修详设这两处标题措辞防误导，属待办小修）。
