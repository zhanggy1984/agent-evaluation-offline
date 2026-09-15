# agent-evaluation-offline error 回流改造任务拆解（error-backflow task / WBS）

> 目的：把 `error-backflow-solution_detail.md`（**v1.2 权威详设**，2026-09-07 由 code_detail v0.3 升格 v1.0 权威化 + §12.6 X 系列登记层增补至 v1.1 + **v1.2 v1.23 契约反转回填**：§10 由「平台只读面 + service token」改写为「结果推送出站与鉴权」）中"要落代码的事"，按实施顺序门 G0~G6 拆成**可执行任务项**，每项给三要素——内容（代码落点文件 + 权威详设章节）→ **验证目标**（单测断言 / §12.1 矩阵 # / 环场景 / R 护栏），指导排期、认领与验收。本文**不重述方案推导**，只做执行层拆解并锚定权威详设章节。
>
> 权威口径：实施规格 = error-backflow-solution_detail.md **v1.2**（本文唯一直接实施源，正文「详设 §x.y」即其章节）；语义基线链 = 批 1 `error-backflow-phase1.md` **v0.3.0** + 批 2 `error-backflow-phase2.md` **v0.8.0** + 消费契约 online `solution_detail.md` **v1.23**（= v1.9 语义 + §14.5 X 系列登记层 + **v1.23 平台间契约反转：结果回传方向反转为 offline 主动推送**）/ `solution.md` **v3.5.9** / online `task.md` 环 0~3（正文「批 1/批 2 §x.y」= 对应权威文档章节，仅当详设正文已引用时才附锚，不臆造次级锚）。原 `error-backflow-code_detail.md` v0.3 已退役归档（只读不维护），不作为实施源。
>
> 事实前提：**代码零落地**（截至 2026-09-07 详设核读，offline `backend/` 无 error-backflow 实现；本文为研发启动首拆）。**文件路径一律相对 `backend/app/`**（真实仓库布局，如 `assertions/ops/text.py`，勿照抄详设个别节标题的 `core/assertions/...` 笔误路径）；详设 §2 行号为核读时点值，**实施时以当时 HEAD 重对一遍再改**。

## 阶段总览（实施顺序门 = 阶段出口验收；任务编号 O-x 区分 online 仓 T-x）

> 执行轨道：阶段 B/C（G1/G2 数据与纯函数）不依赖阶段 A，可与 A 并行；**R-12 改动受 G0 门禁**（未过 G0 不动 `assertions/ops/text.py`，详设 §9.3/§9.4）——A 须在阶段 E 的 R-12 落点前完成即可；D（G3 集成）依赖 C（G2）纯函数绿；E（G4 逻辑层）内 **KeyedLimiter 共享 per-agent 层为 M5 前置**（先 O-E.4 后 O-E.5~O-E.7）；F（G5 环 2）依赖 E 出口，且 **F 内 O-F.7/O-F.8（推送出站与鉴权）为 O-F.6（环 2 走查）的前置**（走查含推送载荷对拍，须先有出站实现）—— **勿按编号顺序误判为 O-F.6 在前**；G（G6）护栏可与 E/F 并行补，终态并入 CI。

| 阶段 | 门 | 主题 | M 覆盖 | 出口（本阶段验收） |
|---|---|---|---|---|
| 阶段 A | G0 | R-20 pre-scan 报告（门禁立项，owner = offline 实施） | —（M6 R-12 前置） | `error-backflow-pre-scan-report.md` 产出 → **未过 G0 不动 text.py / R-12**（详设 §1.3 G0 / §9.4） |
| 阶段 B | G1 | M1 数据模型扩列 + 迁移 + 写校验收口 | M1（详设 §4） | 迁移可空跑/回滚（upgrade→downgrade）全绿 + ORM 建表 + `assert_normal_case_golden_fields` 单测绿 |
| 阶段 C | G2 | M2/M3 纯函数层 | M2/M3（详设 §5.3/§5.4/§6.1/§6.4） | `validate_envelope`/映射/状态机谓词/`sanitize_words`/op 登记纯函数单测全绿（**不触库**） |
| 阶段 D | G3 | M2/M3 集成 = pull_loop 打通 | M2/M3（详设 §5） | fake-online stub → error suite/case + ack 闭环 + R-8 节流 + requeue/manual_invalidate 竞态 |
| 阶段 E | G4 | M4/M5/M6 逻辑层 | M4/M5/M6（详设 §7/§8/§9） | 自动触发/执行/收尾/判定单测全绿（mock `execute_case`；含 R-3 差集、limiter per-agent 层、R-22、R-12） |
| 阶段 F | G5 | M7/**结果推送出站**/M8 + 环 2 双端走查 | M7/M8 + M2~M8（详设 §10/§11） | **推送出站 + 鉴权** + 隔离 + config 就位；场景 1-22 offline 侧真跑 + online fake（**推送载荷字段对拍**；原 R-4/R-5 只读面随 v1.23 整组取消） |
| 阶段 G | G6 | M9 护栏并入 CI | M9（详设 §12） | R-19 code 一致性单测 + R-22 收尾单测并入 CI + §12.1 矩阵全绿 |

---

## 阶段 A｜G0：R-20 pre-scan 报告（前置门禁）

> 对应 online task.md 环 0 L49 登记（详设 §9.4 同引）。R-12 是 offline `KeywordNotContainsOp` 空答语义修补——`text.py` 是 manual/held_out **共享判定算子**，收紧会让存量 keyword 断言从「空答 = 不命中 = pass」翻转为「空答 = fail」；须先证存量无不可承受依赖、白名单可豁免，再改判，防误伤。

- **O-A.1 R-20 pre-scan 现网扫描（三问三答 + 处置登记）**：扫现库 `assertion_results` / `case.assertions` / keyword 算子，**产出 `error-backflow-pre-scan-report.md`**（存仓库根或 docs/），内容 = 详设 §9.4 三问：① 存量 keyword_contains/not_contains 断言下空字段回答被放行（PASS）的用例数、suite/agent/case 依赖面；② 哪些 **manual/held_out 存量普通 suite/case**（非 error case）的合法业务语义允许空答 → **豁免白名单**逐 suite 评估登记（载体不进断言逻辑、不加 per-case 断言分支，实施裁决按受影响面定：小→直接收紧 / 大→豁免清单先行分批合入，详设 §13 行 12）；③ 上线形态 = 非白名单空答显式判 FAIL、白名单走豁免。代码落点 = 不改 backend 代码。**验证目标**：报告三问有结论 + 白名单豁免落位 → **G0 出口**（详设 §9.4），未过 G0 阶段 E 的 R-12 任务（O-E.9）不得开工。

## 阶段 B｜G1：M1 数据模型与迁移

> 详设 §4 改动全在 `models/case.py` / `models/run.py` / 新 `models/error_backflow_inbox.py`。**普通 case 语义不变**（error 哨兵全 nullable/NULL），迁移可回滚是硬要求。

- **O-B.1 TestSuite + TestCase 扩列（`models/case.py`）**：`TestSuite` 加 `is_error_suite`（BOOLEAN，default False，详设 §4.2）；`TestCase` 加 `case_type`（VARCHAR(32) NULL，v1 唯一值 `'regression_error'`）/ `payload_id`（VARCHAR(64) NULL + `uk_case_payload_id`）/ `backflow_envelope`（JSON NULL；**O-F.7 出站 `trigger_signal_id` 的唯一取值来源 = 本列 `source.cluster_id`**），且 `expected/assertions/metrics` 三列由 `nullable=False` **ALTER 放宽**（详设 §4.1；放宽后普通 case 必填由 §4.6 守卫保证）。**验证目标**：ORM 建模编译过；新列全 NULL 下旧 normal case 行为不变。
- **O-B.2 EvalRun 扩列（`models/run.py`）**：`TRIGGER_TYPE` L14 → `("manual","held_out","error_regression")`；加 `trigger_signal_id`（BIGINT NULL，consumed 锚，记触发信号 run id；**同名非同物**：出站载荷（§9.3）同名字段是 **online cluster id**，非本列 —— 勿混用）/ `excluded_case_ids`（JSON NULL，cap 截断挤出的最老溢出 case id 列表；**列保留但 v1.23 后不再对 online 透出** —— 原 R-4 只读面整组取消，降级为 offline 本地诚实诊断，详设 §4.4/§10.3）。**核对 R-10**：`input_truncated` 为 online needs_review 侧 reason 4 标记，offline **EvalRun 不加截断列**、无对应列（只核对不落码，详设 §4.4 L242）。**验证目标**：ORM 建表；trigger 值域含 error_regression。
- **O-B.3 新模型 `models/error_backflow_inbox.py`（error_backflow_inbox 表）**：全列 + `uk_inbox_payload` 唯一约束 + `idx_status_ack` 索引，DDL 对齐批 1 §5.2（详设 §4.3）。**验证目标**：ORM 可建表。
- **O-B.4 alembic 迁移（~~4 个新迁移~~ → **实现为 1 个**，续 head `b1a2c3d4e5f6`）**：⚠️ **2026-09-14 批 A 实施订正**——实际落地 = **单个** `c3d4e5f6a7b8_add_error_backflow_ddl.py`（四段改动同批开发同批部署，拆开不产生独立可验收中间态；代价 = downgrade 粒度降为一次全回 + 两处已知不回退限制，**详设 §4.5 已转为偏离记录**）。下述 rev1~rev4 清单**保留有效**（四段 DDL 内容逐条仍准确），只是不再对应四个文件：rev1 `add_error_backflow_case_cols`（case.py 加列 + 三列 DROP NOT NULL + is_error_suite，down = 删列 + 回 NOT NULL + 撤 uk）/ rev2 `create_error_backflow_inbox`（inbox 全列 + uk + idx，down = drop）/ rev3 `extend_run_trigger_and_anchor`（ALTER ENUM 含 error_regression + trigger_signal_id + excluded_case_ids，down 还原）/ rev4 `backfill_case_columns`（数据迁移，确认无现网 error_backflow 数据，零落地前提）（详设 §4.5）。**验证目标**：`upgrade head` 后 ORM `metadata.create_all` 不冲突（跑 backend/tests 建表冒烟）；`downgrade -1` 回滚成功（**G1 门**；因实现合并为单迁移，`-1` = **一次全回**，「逐级」已不适用）。
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
- **O-D.5 fake-online stub 基建（环 1，`backend/tests/`）**：fixture 回放 online pull/ack 响应 + **入站鉴权（pull_token）形状**（出站静态预共享 secret 形状归环 2 O-F.7/O-F.8）（R1~R3 + R-4~R-24 修订契约 shape），供 O-D.2~O-D.4 驱动（详设 §12.5 验证层级 + 批 1 §12.2 环 1：fixture + fake-online，无真实 online，C3 只入测试基建）。**验证目标**：stub 驱动收单/建 case/ack 全链路仓内全绿。

**阶段出口（G3）**：pull_loop 打通 → error suite/case + ack 闭环 + 详设 §12.6 X-1~X-7（环 1 集成异常/边界用例）全绿（详设 §1.3 G3）。

## 阶段 E｜G4：M4/M5/M6 逻辑层

> **M5 前置**：O-E.4（KeyedLimiter 进程级共享 per-agent 层）先于 O-E.5~O-E.7（详设 §1.1 M5 / §8.3）。O-E.9（R-12）受 G0 门禁。

- **O-E.1 信号挂接 + `maybe_auto_schedule`（M4，新 `runner/auto_schedule.py`）**：信号 = 到达终态（非 pending/running）的 `trigger_type ∈ {manual, held_out}` run（error_regression run 自身不作信号，防递归）；orchestrator `_finish` L560 commit **之后** fire-and-forget（`asyncio.create_task`，失败仅记日志；**不得在持 eval_run 行 FOR UPDATE 锁事务内调用**——反向锁序死锁）；`maybe_auto_schedule(db, agent_id, version, signal_run)`：agent 行锁 → error suite 门禁（C1：无 active case → 不建、latest 保持 None）→ consumed 锚（latest.trigger_signal_id==signal_run.id → return）→ 活跃闸 1（同 agent 活跃 error run ≥1 → 不建）→ 坏终态（timeout/cancelled）仅当新信号未消费 → 补建；runnable 谓词与 §8.1 执行加载**同一谓词**（详设 §7.1/§7.2，批 2 §5.2 终态语义 + 锁序约定）。**验证目标**：§12.1 #1（信号终态 → 建 error run + trigger_signal_id 正确）/ #2（活跃闸不建第二条）/ #3（consumed 锚同信号重复不建）/ #4（坏终态 + 新信号补建）/ #6（C1 门禁 latest 保持 None）单测。
- **O-E.2 R-3 版本差集对账 worker（M4）**：低频任务（并入 scanner 周期或独立 task）对**每个有 active error case 的 agent**：`diff = 该 agent 已到终态 manual/held_out distinct version（排除 error_regression 自身）− 已有 error run 的 version（含 timeout/cancelled 视为有 run）`；对 diff 内每缺版逐版补建（每版取最近终态 run 为锚 → maybe_auto_schedule），**活跃闸：一周期至多补 1**；吸收态与差集正交（坏终态不矛盾）；兼任 §6.3 backfill 清零对账（`assertions IS NULL` 的 active error case 特权补写幂等直到清零，清零后 C1 放行；**特权重写 + 仅空值哨兵**，绕过 `update_case` 403，新函数如 `runner/backfill.py::backfill_error_case_assertions`，详设 §6.3/§7.3）。**验证目标**：§12.1 #5（v1+v2 有 run、v3 无 → 补 v3、v1/v2 不再建）；backfill 幂等（仅补 NULL，不覆盖激活器已写）；洪峰 3 版连发 → 3 run 全建出（环 2 场景 17 前置）。
  - ⚠️ **处置裁定：backfill 子项判「前提不可达」，不实现（2026-09-14 批 C6 取证后拍板）**。上文「兼任 §6.3 backfill 清零对账」这句**规格正文不改写**（`solution_detail §6.3` / `phase2 §7.3` 保持原样），实现现状以本注为准。四条取证：**①** 该子项成立的前提是「批 1 只建 case、批 2 才写断言」的分批上线留出的窗口，而本项目**从未这样上线过**——`pull_loop._activate:182-224` **在同一次建 case 的写入内就构造 assertions**（`:205-208`），词表净化后为空则 `_reject(REJECT_EMPTY_WORDS)` **根本不建 case**（`:149-153`，fail-closed）⇒ **结构性产不出 `assertions IS NULL` 的 error case**；**②** 真库存量实测 **= 0 条**（9 条 error case 全 `active`、全有 assertions）；**③** 规格要求「绕过」的那道墙（§6.5 `update_case` 对 error case 一律 403）**至今未实现**（`api/cases.py:310-311` 走通用 `setattr` 循环，既不识别也不拦截 `case_type`）；**④** 下游防护**已落地**——`case_loader._is_error_case:19-32` 把空/形态不符的 case 剔除、不加载（O-E.5），即 §6.3 承诺的「清零前不进判定」已达成，缺的只是「把断言补回来」这个动作。**判词 = 前提在当前实现下不可达**：**不是**「已实现」、**不是**「欠债」、**不是**「容量型」（勿套错处置习惯，见 memory `code-doc-gap-three-kinds`）。唯一能让它出现的手段是人工经 `update_case` 把 assertions 改成 NULL（**运维误改**），而那的正确处置是**补 §6.5 守卫**，不是让系统每周期扫一个恒空集自动"修回来"（设计原则：答不出「不做会出具体故障」就不做）。**留口子**：若 §6.5 写守卫落地、或出现新的 error case 写入路径（能产出 NULL 断言）⇒ **须重估本条**；本注**不含**任何补偿逻辑，真被误改成 NULL 只能人工恢复。**同批**：O-E.2 的差集对账主体已随批 C5 落地（`runner/reconcile_loop.py`，真库探针 18/18）。**本轮只登记 + 订正一处注释，未改任何代码逻辑。**
- **O-E.3 内部创建器 `_create_error_regression_run` + api 配套（M4）**：内部直插 EvalRun（**不走公开 create_run**，绕过 trigger Literal/`_max_active_runs`/semver）+ case_ids newest-active-first 取至 cap + `excluded_case_ids` 截断诚实 + 初始 lease（now+90s）+ error-run 专用 run_config 预算（case_timeout=600/max_retries=2/hard_deadline 含闸等待）+ 发 `orchestrator.start_run(run_id)`（详设 §7.4 参数表）；`api/runs.py` 配套：version `_SEMVER` 强校验移除 → 共享域校验、活跃计数扫描排除 error run（create_run L251-253/rerun L378 加 `trigger_type != 'error_regression'`）、error suite 拒建 manual run、rerun 对 error_regression 显式拒绝（详设 §7.5 项 1-4 + 批 2 §9.2/§6.7）。**验证目标**：创建参数表逐字段断言（含 excluded_case_ids cap 溢出、pinned、lease 非空）；error run 不占 `_max_active_runs` 名额不堵 manual。
  - ✅ **施行记录（2026-09-15，四项全部落地）**：项 2/3/4 随批 C7（`143e5fe`，真库探针 `705e6a6` 13/13）、项 1 随批 C8（真库探针 22/22）。**两处与上文的偏离**（依 status.md 约定，**不改上文原文**）：**①** 项 1 **未照「无空白/控制字符」实施**，改**白名单字符集** `^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}\Z` —— 照规格字面会放行 `1.2.3<script>` 与 `<script>alert(1)</script>`（两者都不含空白），等于撤掉 P2-C4 用 `\Z` 修好的 XSS 入库闸。放宽版本形态的目标达成，**「拒绝任何 HTML 注入形态」的语义不变**。**②** 上文「rerun 同」（semver 校验）**不成立**：`RerunBody` 只有 `case_ids`、无 version 入参，`rerun_run` 是 `version=src.version` 直接继承 ⇒ **rerun 从来没有校验点**，本项无 rerun 侧可改（**规格与实现不符**，非故障面）。详见 `error-backflow-status.md`「三、已落地」的 O-E.3 施行记录。
- **O-E.4 KeyedLimiter 进程级共享 per-agent 层（M5 前置，`core/limiter.py`）**：现码 = per-agent sem 嵌套在各 run 独立桶内（桶间不共享）；改造 = `KeyedLimiter` 增 `_agent_sem: dict[str, asyncio.Semaphore]`（key = agent.name，容量 = 首次执行时 `per_agent_concurrency`，默认 3）；`acquire(run_id, agent)` 序 = **shared-agent → 桶内**，失败/异常逆序释放，`release` 逆序；未 `set_run` 走 `_default` 桶的调用带 agent 亦先取 shared-agent；manual/held_out **业务零改动** + 行为等价（无 error run 并存时 manual 单 run 仍 ≤ per_agent_concurrency）；error 桶 = `set_run_limits(run_id, global_limit=1, per_agent=1)`，同 agent manual 占满时 error FIFO 排队、槽释放即进 → **同 agent 总并发恒 ≤ 上限、error ≤1 路、不叠加超限**；容量不可变 → 运行期改并发需重启 worker；排队饿死兜底 = error-run hard_deadline 超时回收 + §8.5 心跳续租（详设 §8.3 步 1-5，撤销 v0.2「接受叠加 ≤4」取向）。**验证目标**：§12.1 #23（同 agent 总并发 ≤ 上限 + error ≤1 + manual 并发回归等价）；FIFO 排队不超限单测。
- **O-E.5 case_loader error 分支（M5，`runner/case_loader.py` L20-32 改写）**：`_load_run_cases` 加 error_regression 分支——`suite_id==run.suite_id AND status='active' AND case_type IS NOT NULL AND is_held_out==False AND assertions IS NOT NULL 且形态 keyword_not_contains`（§9.5 素材缺失前置过滤，加载即剔除不建 run case）；返回集按加载时持久化或按同一谓词重算，使 §8.7 回填对象边界确定（详设 §8.1 + §1.1 M5）。**验证目标**：§12.1 #7（哨兵 case 不进 manual 加载）/ #8（assertions 缺失/形态错 → 剔除、不建 case、无结果行）单测。
- **O-E.6 orchestrator error 分支 `_run_error` + 熔断隔离 + 心跳续租（M5）**：`_run` L135 后按 `trigger_type=='error_regression'` 分发独立 `_run_error(run_id)`（不复用普通 `_run` 后续，复用 `_run_one` 之下机制）：D8 前置校验（pinned==True + case_ids 显式非空 + suite 属该 agent error suite，不满足 → skip + 告警不真打被测 agent）；跳过 `_probe_before_run`（探活对 error 零增益）；执行循环——槽 acquire（O-E.4 共享层）+ 独立 breaker key `f"{agent_id}:error_regression"`（不共用 manual breaker，打开 → circuit_open → na）+ `executor.execute_case` 复现（成功 → verifier 判 pass/fail 一次写终值；失败 error_type 非空 → RETRYABLE 重试尽 → na + error_type 如实落，不改判）；调度层未执行（circuit_open/interface_disabled 默认 'error' 落库路径）→ **拦截** → na + error_type 原值（不落默认 'error' 行）；落 EvalResult 走 `_ensure_case_version` 惰性快照（§8.8，不预建不旁路）；**心跳续租固定时间片**（间隔 ≪ lease TTL，如 15s，覆盖单 case 执行窗口 + 排队等待期，不得仅以每 case 完成为粒度）（详设 §8.2/§8.4/§8.5/§8.8）。**验证目标**：§12.1 #9（复现成功命中 → fail）/ #10（不命中 → pass）/ #11（timeout/connect_error 重试尽 → na + error_type 如实）/ #12（circuit_open → na + circuit_open 不落 'error'）集成单测（mock execute_case）。
- **O-E.7 独立收尾 `_finish_error_regression` + scanner 短路 + R-22（M5）**：新增 `_finish_error_regression`（**不复用共享 `_finish`**——其对账回填 'error'/全 na 落 completed/按 'error' 计 error_case 与 error 语义冲突）：0 na → completed / 有 ≥1 na → partial_failed / 未跑完被取消超时 → timeout|cancelled + 已完成 case 保留终值 + 未完成回填 na；na_case 计数、error_case 恒 0、agent_score 恒 NULL、completed 仅当 0 na；**实跑口径**（实跑集 < case_ids 的 case → results 返回空；**v1.23 后 online 按载荷缺行≠na 判定**，不再轮询）；实跑集为空防御（C1）→ 不落 completed 落 cancelled + 告警；na 回填与 run 终态**同事务同 db session 直插**（沿用共享 `_finish` L536-546 FOR UPDATE 锁模式，uk_result 幂等兜底）；**终态写守卫** = 终态后迟到 EvalResult 一律拒绝（uk_result 撞键丢弃迟到行）；**R-22 = 回填 na 的 error_type 钉死 `scheduler_unexecuted`，timeout 只作 case 级 na 源不作回填 error_type**（详设 §8.6 行 3 + §3.4 不变量）；scanner `_reap_one_pass` L112/L115 对 `score_run_salvage` 加守卫 `if run.trigger_type=='error_regression': 走 error 替代收尾（短路 salvage，不产 agent_score）`；替代收尾置 run timeout + 实跑集内未完成 case 回填 na（含 pending 卡死回收特判 + running 心跳停回收，lease 未过期豁免）——**R-22 三路径同源**（scanner 回收 / orchestrator cancel / run 级超时，详设 §8.7）。**验证目标**：§12.1 #14（0 na → completed / 有 na → partial_failed）/ #15（全 na 不落 completed）/ #16（scanner 回收 timeout + 未完成 na）/ #17（R-22 三路径回填 error_type 钉死 scheduler_unexecuted）/ #13（终态写守卫拒迟到行）单测。
  - ⚠️ **未落地子项：scanner 短路（2026-09-14 批 C 实码核读，用户拍板「就地补注、不新立 T 项」）**。上文本条已逐字写明实现点（`_reap_one_pass` L112/L115 加 `if run.trigger_type=='error_regression'` 守卫），**该守卫至今未实现**——`scanner.py:112/115` 两处 `score_run_salvage` 调用**无任何 trigger_type 过滤**，且 `scanner.py:71-92` 的回收选取条件里也没有（**已核上游无闸，非「不可达」**）。后果两项：**①** 被回收的 error run 进 `score_run_salvage` 后，仍按普通 run 语义走 `scorer.py:389-393` 给缺失 case 写 `pass_fail='error'`（违反本条自陈的「agent_score 恒 NULL」与 §8.2「error 链只落 pass/fail/na」）并回填 `agent_score`；**②** 该 run **不推送**——推送挂在 `_finish_error_regression` 收尾处，回收路径不经过它 ⇒ 对端 link 静默挂到 claim TTL。**触发条件** = error run 租约/硬超时被回收（worker 进程死亡、心跳失效）。**判据无需新造**：本条「验证目标」中的 **§12.1 #16** 即此项判据。**修复前置 = 无**——`O-F.7` 出站客户端**已随批 C2 落地**（`runner/error_push.py::fire_push`，触发点 = `_finish_error_regression` 收尾 commit 之后；`orchestrator.py:37` 导入），后果②的补推送**可直接复用 `fire_push`**，不存在必须先等的前置批次（**订正**：本注初稿曾写「依赖 O-F.7 落地、当前无载体」，属未回查即搬运旧结论，已作废）。**本轮只登记、未改任何代码**。
  - ✅ **施行记录（2026-09-15，scanner 短路已落地，上述后果①②均已消解）**：`scanner.py` 新增模块级 `_salvage_reaped_run(run_id, trigger_type)` —— error run 走 `orchestrator._finish_error_regression`、其余走 `score_run_salvage`；`_reap_one_pass` 的两处调用点（租约/硬超时、评分超时）**共用该函数**（规格要求两处加同一个守卫，内联两遍日后必漏一处）。选「复用 `_finish_error_regression`」而非另写替代收尾：其 `external_terminal` 分支（run 已是 timeout/cancelled）正为「外部先置终态」而写，且**顺带消解上文后果②**（推送挂在收尾尾部 `fire_push`，回收路径由此经过）。**代价 = 偏离 §6.6 字面**（规格只列两项动作、未列推送），属**有意偏离**。验证 = 新建 `tests/integration/test_integration_scanner_error_run.py` 三条真库用例（租约回收 / 硬超时回收 / 已终值行不改写），**判别力对照为实测**：临时去掉守卫 ⇒ 三条**全红**，红点分别在 `error_case==0`、结果行被重算（`pass→na`、`fail→na`）。**两处订正上文**：**①** 上文「并**回填 `agent_score`**」在实测中**未复现** —— 短路与否 `agent_score` 都是 None（该断言因此**无判别力、已撤除**，不留在测试里当假绿）⇒ 「agent_score 恒 NULL」这条**无法经 `agent_score` 观测量证伪或证实**，判别落在 `error_case` 与结果行两处；**②** 上文所指判据 **§12.1 #16** 已由上述三条用例承载。
  - ✅ **同批暴露的独立缺口 —— 已于 2026-09-15 修复（#260）**：`run.total_case` 原先**只在 pending→running 的接管 UPDATE 里被赋值**（`orchestrator.py`），**建单处 `_create_error_regression_run_locked` 不写该列** ⇒ **从未被接管的 pending error run 的 `total_case` 恒为模型默认 0**，于是 `_finish_error_regression` 的 `len(results) < run.total_case` 判据为假、**不触发 §6.6 要求的「未完成 case 回填 `pass_fail='na'`」**。即：**租约路径**下 error run 拿得到「终态 timeout + 不落 `'error'` + 不改写终值行」，**拿不到 na 回填**；**硬超时路径**（必经接管、`total_case` 已写）才拿得到 —— 这也是 scanner 短路批两条用例对 na 回填**分别处理**的原因（租约那条当时不断言回填）。
    **修法** = 在建单处补写 `total_case=len(selected)`（§7.4 建单 planned 数）。⚠️ 取的是**过滤前计划数**：`_is_error_case` 形态过滤与 `is_held_out` 都在 `_load_run_cases` 加载时才做，故此处**故意可能大于**接管处写入的 `len(cases)`。**这不产生多余结果行** —— 判据只拿它决定「要不要对账」，补行循环遍历的是**加载集**（已过滤）。语义分三阶段：**建单计划数 → 接管加载数 → 收尾结果数**。
    **验证** = `tests/integration/test_integration_scanner_error_run.py` 用例②（租约路径，run 经**真实建单器**产出、不手搓 `total_case`）断言 `total_case == 2` + 未完成 case 回填 `na` + `error_type='scheduler_unexecuted'`；**判别力对照实测**：去掉建单处那一行 ⇒ 该断言红。用例② 另修一处夹具缺陷 —— 建单器要求调用方**已持 agent 行锁**（再取即同进程自锁死），且 `db.add(sig)` 后**必须先 commit**（未提交事务在 `agent` 父行持 S 锁，另一 session 求 X 锁 ⇒ 跨 session 自锁、等锁 50s 超时）。
- **O-E.8 verifier no_fallback 调用点 + 素材缺失运行期防御（M6）**：error 分支成功复现后调 `run_assertions(run, case, unified)`（锚 `assertions/run.py` L12-40），输入 = 复现 unified dict（case/answer/pass_fail 域 + case.assertions 已装配链 keyword_not_contains），返回布尔化 → pass/fail **一次写终值**（不走 `_save_result` 默认 pass_fail='error' 路径、无「先写 pass 占位再修正」）；判定 = executor 成功 ∧ no_fallback 布尔化，**复现成败恒 > 素材真假**（复现挂了 = na 无关 answer）（详设 §9.1/§9.2 + 批 2 §7.1/§7.2 D1/D2）；运行期兜底 = 加载后残留缺失/形态不符 case 按 `missing_assertion`/`assertion_shape` na、run 走 partial_failed 不 crash、**空断言（[]）绝不判 pass**（详设 §9.5）。**验证目标**：no_fallback 命中 → fail / 不命中 → pass / 空词表 fail-closed；na 源 error_type 在值域内（O-G.1 R-19 前置核对）。
- **O-E.9 R-12 text.py 空答 fail（依赖 G0 已过；未过 G0 不开工）**：`assertions/ops/text.py` —— `KeywordNotContainsOp.run()`（L33-62）对空/纯空白 answer：现况天然 PASS（`keyword_contains` PASS、not_contains 未收紧）→ 改 `if not isinstance(text, str) or not text.strip(): return FAIL（空/纯空白应答 = 复现空答 leakage 证据）`，其余原逻辑不变（keywords 归一 → 检查命中 → 命中即 FAIL）；**语义钉死**：空答 fail = leakage 空话术复发证据，**不是 na**（na = 复现没跑到，fail = 跑到且空答，两路径不可混，phase2 §7.2 定性）；共享算子 → 白名单豁免用例走 O-A.1 产物（详设 §9.3 + §9.4 + §12.2）。**验证目标**：空答/纯空白 → FAIL；非空不命中 → PASS；命中 → FAIL；白名单豁免用例仍 PASS（回归）。**G0 放行后才允许合入**。
  - ✅ **施行记录（2026-09-15，已落地）**：G0 于当日判「过」（选项 A，`error-backflow-pre-scan-report.md` §5.1），本条解锁并落地。实现点 = `assertions/ops/text.py` 的 `KeywordNotContainsOp.run()`，在既有 `isinstance` 分支**之后**插入 `if not val.strip(): return False, "<空/纯空白应答 → 复现空答（leakage 空话术证据）>"`（`git diff --stat` = 5 增 0 删）。**三处与上文有出入，依本仓惯例「不改上文原文」，在此登记**：
    **①** `not text.strip()` 写入 `run()` 的**空值分支**而非与 `isinstance` 合并 —— 两者诊断语义不同（「非文本」vs「空答」），`actual` 是给人看的证据串，合并会丢区分度且让「非文本」分支成死代码。**② 规格 §9.3 的伪码 `return OpResult(False, ...)` 与真实签名不符**：基类 `AssertionOp.run` 返回 `tuple[bool, Any]`（`assertions/base.py`），落地按**真实签名**。**③ 上文引的行号已漂**（写 `L33-62`，现 `KeywordNotContainsOp` 在 `L61-95`）—— 属 P3 登记项，**只登记不改**；引用一律以符号锚（类名/方法名）而非行号。
    **验证**：① 单测 `TestKeywordNotContains::test_blank_answer_fails`（`""` / `"   "` / `"   \n\t "` 三形态）；② 防误伤正面判据 `test_nonempty_answer_without_keyword_still_passes`（只测空答会让「一刀切 return False」也全绿）；③ **判别力对照实测**：临时摘掉守卫 ⇒ 三形态**全红**、红在 `AssertionError: True is not false`（= 改前 PASS 的原形），而正面判据**仍绿**（独立成立）；④ 存量回归探针 `tests/integration/r12_empty_answer_probe.py`（**固化、只读、连跑三遍一致**）；⑤ 全量回归 **939 passed / 100 skipped / 14 subtests passed**（= 同期基线 937/100 + 本批 2 条，自洽）。
    **⚠️ 存量回归探针的一条附带结论（本批新增，指向新缺口）**：判据「空 `answer` 且带 `keyword_not_contains` = 0」成立，但**拆分 `trigger_type` 后那 108 条对照全部来自 `manual`，`error_regression` 零条** —— 因 `eval_result.assertion_results` 在 error 路径 **84/84 全 NULL**。根因是代码设计而非数据意外：`runner/orchestrator.py:582` 只落 `verdict_fn(...)` 的**终值字符串**，`_error_verdict`（同文件 `:136-144`）内部 `run_assertions` 的逐条 `results` **被丢弃**。⇒ **R-12 的语义修正所针对的那条路径，在落库面上本来就没有可视化证据**（`actual` 串不落任何列）。**本批只登记、不改码**（批内不掺下一批），是否补「error 路径逐条断言证据落库」另立条目裁。

**阶段出口（G4）**：O-E.1~O-E.9 逻辑层单测全绿（mock `execute_case`，详设 §1.3 G4）+ R-12（O-E.9）受 G0 放行已合入。

## 阶段 F｜G5：M7/M8 + 环 2 双端走查

- **❌ O-F.1（v1.23 整条取消）runs list/detail 只读输出扩展（M7，`api/runs.py` `_run_out` L200-221 基座）**：error_regression run 透出 `trigger_type='error_regression'` / `version`（fix_version）/ `trigger_signal_id`（R-16 缺行比对）/ `excluded_case_ids`（**R-16 落位点**：online runs list/detail 取此判「窗口外欠测」）/ `suite_id` / per-case 结果行（pass/fail/na + na 的 error_type）；`_run_out` 增列**只加不改**（不 rename 不重排，结果明细沿用既有 per-case 通道 EvalResult，不加 offline 专属终态列，R-6 载体承诺）；普通 suite/case 只读面排除 error 对象（详设 §10.1）。**v1.23 后 online 零 outbound、不再拉 runs —— 本项整组取消，字段改由 offline 主动推送承载（替代 = O-F.7）**。**验证目标（改）**：原「与 online detail §8.7 逐字段对拍」作废；仅保留「普通面零 error 污染」隔离护栏（归 O-F.4）。
- **❌ O-F.2（v1.23 整条取消）R-4/R-5 只读检索面（M7，error 侧显式端点）**：新增 error 侧显式只读面（**不与普通 suite/case 面混**，文件可新增如 `api/platform_runs.py` + `main.py` 挂 router，详设 §5.1）：runs 过滤 `trigger_type='error_regression' AND suite_id=:sid`（判 K 连续、R-16 缺行）；error case 面 = error suite 下 active case 的 `case_type/payload_id/assertions`（装配后链）只读；**R-4** = `excluded_case_ids` 溢出列表透出（诚实标记非空、溢出计数 len）；**R-5** = agent 已见版本读面（error run distinct version，判 K 序列数据源）；白名单过滤谓词统一 = §6.5 三谓词只读面同一份（详设 §10.2 + 批 2 §9.3/§9.4）。**v1.23 后 online 无拉取路径、无只读端点可建 —— 本项整组取消**：R-4 降级为 offline 本地诊断（`excluded_case_ids` 列保留，O-B.2）；R-5 由载荷两水位字段 `agent_latest_version`/`prev_terminal_version` 替代（O-F.7）。**验证目标（改）**：无 —— 本项不落码。
- **❌ O-F.3（v1.23 整条取消）外部检索鉴权 / service token helper（M7，原拟 `core/security.py` 扩）**：现有 security 之上加 service token 校验——JWT HS256、`type=service`、scope 内（claim 面/error 面分 scope），签发/轮换走 system_config 或本地密钥文件**不入库明文**；error 只读端点须带 service token（防人工误检索旁路隔离语义）；manual 人工面现有认证不动（详设 §10.3 + 批 1 §9 service token helper）。**v1.23 后 online 无入站请求、无只读端点可护**（三出站端点鉴权 online 侧已定为**静态预共享 secret**：无 scope 分置、无 JWT、无 `type=service`），`create_service_token`/`verify_service_token` **无需实现**。**替代 = O-F.8（出站鉴权）**。**验证目标（改）**：无 —— 本项不落码。
- **O-F.4 存量评测语义零污染（M8 三层隔离 + dashboard 排除 + cleanup 豁免）**：数据面（error case 带哨兵不进 manual/held_out 加载，O-E.5 已覆盖）；行为面（error suite 拒建 manual run / error_regression 拒公开 start/rerun，O-E.3 已覆盖，核对）；只读面 active-count/名额互斥计数排除 error run（O-E.3 已覆盖）+ dashboard 数字口径排除——`api/dashboard.py` 聚合若走 runs 表会含 error run 行 → 与 active-count 同排（口径注记，批 2 §9.1 全聚合排除）；`runner/cleanup_rules.py` 免清理核对（error run/error case 不占清理位，批 2 §6.5）；写守卫谓词单一来源 = §6.5 全量写守卫收敛共享常量集（`CASE_TYPE_IS_NULL`/`IS_ERROR_SUITE_FALSE`/`TRIGGER_NOT_ERROR_REGRESSION`，一处定义 api/case_loader 复用，防「api 挡了 loader 没挡」；**原「§10.2 只读过滤谓词复用」随只读面取消而删除**，详设 §11.1/§11.4 + §6.5）。**验证目标**：dashboard 无 error run 混入（**2026-09-15 部分达成 = P2-1**：`/gate` 与 `/trend` 两聚合已排 `error_regression`，判别力经**反事实对照**实测（摘掉谓词 ⇒ 探针 37 条 FAIL）；其余 5 面无对象，口径与纠正见 `error-backflow-status.md` O-F.4 行）；cleanup 不误清 error case；error suite/case 全写端点 403（§6.5 全清单核对：update_case/invalidate_case/add_annotation/add_case_scenes + create_suite/update_suite 禁人工置 is_error_suite + list_suites 透出标注）。
  - **✅ 2026-09-15 施行记录（批 5，本条目余项）**：
    ① **§6.5 写守卫 403 全清单落地** —— 六个写端点全挡，helper = `api/deps.py::require_normal_case`
    / `require_normal_suite`（`case_type IS NOT NULL` / `is_error_suite=True` ⇒ 403 + `E_NO_PERMISSION`）；
    落点 `cases.py` 四处 + `annotations.py::add_annotation` + `scaffold.py::add_case_scenes`。
    **读面（GET）一律不动。**
    ② **B7（禁置 golden/held_out）由 `create_case` 守卫吞掉** —— error suite 上人工建 case 已整体 403，
    再单判是**不可达代码**。**B8 显式不做**：`SuiteCreate`/`SuiteUpdate` 不含 `is_error_suite`
    ⇒ Pydantic 天然屏蔽，加守卫无可观测差异（「机制不同、已满足」）。
    ③ **`create_run`/`rerun_run` 的 400 不改 403** —— 符合 `solution_detail.md` 明文，改了打红既有测试。
    ④ **谓词常量三件套（`CASE_TYPE_IS_NULL` / `IS_ERROR_SUITE_FALSE` / `TRIGGER_NOT_ERROR_REGRESSION`）
    = 经评估显式不做**（决策非漏做）：规格给它的用途是「防 api 挡了 loader 没挡」，但实测缺口是
    **api 层一条都没挡** —— 共享常量只防「写法不一致」，救不了「根本没写」；且 20+ 站点是好几个
    runner 模块的零行为收益纯重构。**代价（承认）**：今后「只加 api 守卫、漏加 loader 守卫」无机制拦。
    ⑤ **B10（`list_suites` 透出 `is_error_suite`）不做** —— 规格标 D-15，但**无任何消费方**，
    按「答不出谁用就不做」否掉，登记为已知偏离。
    ⑥ **校验**：单测 12 条（6 正向 403 + 6 反向对照）；反事实对照实测（helper 改空实现 ⇒
    **恰好 6 红 6 绿**）；全量回归 **957 passed / 100 skipped**（批 3 基线 945/100 + 12）；ruff 逐规则
    对照 HEAD 零新增。**未验收**：前端收到 403 后的表现不在观测面内；「无内部调用方」是静态 grep 结论。
- **O-F.5 config keys + 启动挂接（M8，`seed.py` DEFAULT_SYSTEM_CONFIG L345-422 段 + `main.py` startup）**：system_config seed 加 `backflow_enabled`（false 总开关，false 停 worker/调度/差集对账/**出站推送**，保留已建数据）/ `backflow_pull_token` / `backflow_since_ts`（json 逐 agent）/ `backflow_poll_interval`（60）/ `backflow_hard_lease_sec` / `backflow_heartbeat_sec`（15）/ **出站推送：`backflow_online_endpoint` + `backflow_outbound_secret`（静态预共享 secret，详设 §10.2，O-F.8）** + error-run 预算/breaker/并发组（按 adapter）；seed 缺省关闭态、代码路径不做隐式 on；`main.py` startup 门控 `backflow_enabled`：迁移检查（表/列/索引在，缺失 → fail fast）+ 注册 pull_loop worker + 差集对账低频 task + cap_gap probe task；shutdown 取消 task 幂等游标不丢（详设 §11.2/§11.3）。**验证目标**：关闭态零副作用（未配 backflow 前 DB 空表可跑）；开启后 worker 注册/停 worker 幂等。
- **O-F.6 环 2 双端走查（offline 侧真跑 + online fake）**：本地共享 infra（MySQL 分库）下走 phase2 §11.2 场景 1-22 offline 侧 + online fake 消费（R-3 差集补建 / **推送载荷 10 字段与 online 接收侧逐字段对拍** / R-4/R-5 已随只读面取消、仅作 offline 本地核对），对应 online task.md 环 2 出口（E-1~E-29 中 offline 侧用例，E-23~E-29 修订包端到端）（详设 §12.5 验证层级 + 批 2 §11.2）。**验证目标**：场景 1-22 offline 侧全走通；online fake 正确接收并落库推送载荷（含两水位字段）。
- **O-F.7 结果推送出站客户端（M7，新 `core/backflow_client.py`；v1.23 新增，取代 O-F.1/O-F.2）**：error_regression run **终态 commit 之后**（**不在收尾事务内**）异步 fire-and-forget 发起 `POST /backflow/regression-results`（详设 §10.1 + 批 2 §9.3）：载荷 = `schema_version`/`agent`/`agent_version`/`run_id`/`run_status`/**`agent_latest_version`**/**`prev_terminal_version`**（必填但**值可为 null** = 该 agent 此前无任何终态 run）/`trigger_signal_id`/`finished_ts`/`cases[]`（10 字段，逐字段锚 online `api/backflow.py`；**接收方权威口径 = online `solution_detail.md` §8.7 载荷字段表**，**无 `case_truncated`/溢出列表**）；**两水位字段口径硬约束 = agent 级全部终态 run**（manual/held_out 的版本亦计入），**不得按 `trigger_type='error_regression'` 收窄**（误收窄 → 与 online「缺行中断」判据系统性错位 → 假中断），**且两字段须与 `prev_terminal_version` 同一次查询产出**；**出站前自检三条（否则整单被拒）** = `case_id` 在 `cases[]` 内唯一（重复 → online 整单拒 `ERR_PULL_0002`）/ `finished_ts` 合法 ISO8601 / `schema_version='1.0'`；**另加取值来源自检一条（不拒单、静默失败类）** = `trigger_signal_id` 取值来源 = `backflow_envelope['source']['cluster_id']`（online cluster id），不得填 `eval_run.trigger_signal_id` —— 同名非同物，填错不报错：撞上 pending link → 推进错簇，撞不上 → orphan 且 online 仍回 200 → 结果永久丢失；失败语义 = 超时 5s、重试 3 次（退避 1s/2s/4s），全败记 error 后放弃、**不阻塞 run 收尾**；幂等键 `uk_verify_run(link_id, run_id)`（重放 → 200 `duplicated:true`）；响应处理 = `links_advanced` 为真正发生终态迁移的 link，**`cases_dropped` 幂等重放不归零**（不得据此判断「没丢数据」，须看首次响应或 `duplicated`）。**验证目标**：载荷 10 字段逐字段断言（含两水位字段口径 + `prev_terminal_version` 首次 null 分支）；`case_id` 重复 / 非法 `finished_ts` / `schema_version` 不符在**出站前**被拦（不出网）；三次全败不抛不阻塞收尾；重放响应 `duplicated` 正确处理。**载荷契约常驻护栏 = R-25 / O-G.4**。
- **O-F.8 出站鉴权 + secret 配置项（M7，`core/config.py`；v1.23 新增，取代 O-F.3）**：三出站端点共用**静态预共享 secret**（`Authorization: Bearer <secret>`），**无 scope 分置、无 JWT、无 `type=service`、无签发/轮换链**；`BACKFLOW_INBOUND_SECRET` 整条作废、`create_service_token`/`verify_service_token` 不落地（详设 §10.2/§10.3）；secret 经 `system_config` / `core/config.py` 注入（key 见 O-F.5），**不入库明文、不进日志**；manual 人工面现有认证不动。**验证目标**：secret 缺失/错误 → 出站被拒且不重试；secret 实际值不出现在日志、异常消息与响应体。

  - **✅ 施行记录（2026-09-15，第三批）**——原判「部分落地：401 被重试 3 次」，落地时订正了**两处**：
    1. **范围比判词窄**：判词暗示 pull/ack 也在重试，但 `pull_loop.py:174/:257` 两处捕获**本就只是
       「记日志 + 留 pending + 下轮幂等重放」，无任何立即重试循环** ⇒「重试」只存在于 push 路径
       （`error_push._push_one`）。本批**未动 pull/ack**（动了反而改变「留 pending 下轮重放」语义）。
    2. **判据按码的类别，不写 401 特判**：`BackflowClientError` 增 `status_code: int | None`
       （网络层 = `None`），`error_push._is_retryable` = 「`None` 或 ≥500 或 ∈{408,429} ⇒ 可重试」，
       其余 4xx ⇒ **一次即止、不退避**。这样**不必先取证「online 对错误 secret 回 401 还是 403」**
       —— 该跨仓未知不落在本批正确性路径上。**代价（承认）**：`408/429` 归可重试是**按 HTTP 语义
       定的，无真机证据**；且 online 侧对错误 secret 的实际响应码本批**未取证**。
    3. **`status_code` 必须是可选参数**：`tests/test_pull_loop_reject.py:172` 以
       `BackflowClientError("400")` **单参位置**构造 ⇒ 设成必填会当场打红既有测试（已核，现仍绿）。
    4. **观测量取调用次数，不取日志文案**：反事实对照（`_is_retryable` 临时恒 `True`）下**恰好 3 条**
       新用例红（全部是「不重试」判据），而 5xx/网络/408/429 那几条**正向对照仍绿** ⇒ 有判别力，
       不是整片红；还原后复绿、全量 945 passed / 100 skipped（批 2 基线 939/100 + 本批新增 6 条，自洽）。
    5. **判据第二条（secret 不进日志/异常消息）为静态核对、非运行时取证**：6 处 raise 只带
       `type(exc).__name__`（异常**类名**，非 `str(exc)`，故不带 URL）与状态码；secret 唯一去向是
       `_headers()` 的 Authorization 头（`backflow_client.py:48`），**全仓 logger 不触碰该字段**。
    **显式不做的**：未加「secret 缺失」的本地守卫 —— 判据已由「非可重试 4xx 不重试」覆盖，
    本地守卫只把日志措辞从 401 换成「你没配 secret」，属可诊断性改善而非故障修复。

- **O-F.9 R-26 出站基址「容器名」不可达（`core/http.py` 既有缺陷；批 B 实测揭出，**登记不修**）**：`AllowlistAsyncClient.send`（`core/http.py:106-118`）对**不在 `allow_hosts` 但可解析**的主机名走「URL host 换成解析后 IP + 补回原始 Host」的重写路径，而补回写法是 `dict(request.headers)`（键已被 httpx 小写为 `host`）再赋 `["Host"]` ⇒ 出站请求携带**两个大小写不同的 Host 头**，raw 实测 `[(b'host', b'obs-backend:8000'), (b'Host', b'obs-backend')]` ⇒ h11 抛 `LocalProtocolError: Found multiple Host: headers`，**请求根本发不出去**。实测对照（同一 client、同一进程）：`host.docker.internal`（在 `DEFAULT_AGENT_HOSTS` 内，走 `http.py:85-86` 直接 return、**不重写**）→ 200；`obs-backend`（容器名，不在白名单）→ 必失败。**影响面不止本特性**：凡「按容器名调另一个容器」的出站在本客户端下**整体不可用**，根因在共享核心客户端，与 error-backflow 无耦合。**本批处置 = 绕过**：`core/backflow_client._client()` 按配置基址的 hostname 显式塞进 `extra_hosts`，走不重写那条分支；**代价（承认）** = 该分支**跳过 IP 解析校验**（`http.py:85-86` 只做 denied 检查），基址为运维配置固定值故可接受，但这是妥协不是修好。**正确修法**（未做，属 A 级核心改动）= 让 `send` 复用/替换原 Host 项而非新加大小写不同的第二个键。**验证目标**：回读 `_client()` 传给 `build_agent_client` 的 `extra_hosts`（回归护栏 `test_base_host_passed_to_allowlist`，去掉即红）；真机连通由批 B 端到端验收承担。

  **✅ 施行记录（2026-09-15 批 4）——根因已修、绕过已撤除**（本条的「登记不修」处置**已被推翻**）：`send()` 改为**摘掉原 Host 项再加**（旧写法 `dict(request.headers)` 的键已小写为 `host`，再赋 `["Host"]` ⇒ dict 内并存两键、进 `Headers` 归一为同名 ⇒ 实发**两个** Host）、复用**原 Host 项**（**含端口**，旧写法丢端口）、改 `multi_items()`（`dict(Headers)` 会把同名多值头折叠成 `"a, b"`）。`_client()` 的 `extra_hosts` 绕过**撤除** ⇒ 本条写明的代价「跳过 IP 解析校验」**已不成立**，恢复完整的「解析全部 IP 并校验在内网段内」。护栏**方向反转**：`test_base_host_passed_to_allowlist`（护着绕过本身）→ `test_base_host_not_special_cased`（**再塞白名单即变红**），Host 去重判据移入 `tests/test_security.py`。**真机三判据**（容器内）：`obs-backend` 解析 `172.23.0.4` / 新码 `pull_payloads()` **200** / 旧形状 `LocalProtocolError`（**与登记症状逐字吻合**）。**登记不修**：HTTPS 的 SNI（`copy_with(host=IP)` 后按 IP 校验证书，**同源不同层**，无已知故障面）。完整验收表见 `error-backflow-pending-phases.md` §5.5。

**阶段出口（G5）**：环 2 走查全绿 + 详设 §12.6 X-8~X-11（环 2 双端异常/**推送载荷对拍**用例）全绿（详设 §1.3 G5）。

## 阶段 G｜G6：M9 护栏并入 CI

> 护栏 = 防系统性误伤的**行为锁**（非一次性核对），随对应代码落地即写、并入 CI。

> ⏸ **O-G.1 / O-G.3 / O-G.4 三项暂停（2026-09-15 拍板）**：不撤回、不继续。
> 理由 = 三者都是**对着 online 源码/规格推断**契约（字段清单、分类表、矩阵登记），
> 而真实数据一条都没走过 —— 推断出的契约对错无法证伪。**改为先追通一条真实数据，
> 再按真实载荷倒推该写什么断言**。恢复时机 = 第一条数据打通后由用户拍板。
> 详见 `error-backflow-status.md` 顶部「全表暂停」注。

- **O-G.1 R-19 error_codes 一致性单测（护栏）**：`runner/error_codes.py` 字面量 + 分类表（§3.1 error_type 权威字面量域）**必须逐字符等于** executor.py 常量全集（详设 §2.4：executor 错误常量 L22-31 + `RETRYABLE_ERRORS` L38）；na 值域 ⊆ phase2 §6.4 na 分类矩阵值；单测钉死任一 key 漂移（拼写/长短名/大小写）→ 测试红（防 executor 加常量漏分类 → 未知 error_type 默认从严环境级 → 假 unclean_run 风暴复发）（详设 §3.1/§9.6 + 批 2 §6.4 R-19）。**验证目标**：§12.3 单测并入 CI，常量增改即红。
- **O-G.2 R-22 收尾回填 error_type 单测护栏**：scanner 回收 / orchestrator cancel / run 级超时收尾三路径回填 `scheduler_unexecuted` 非空 + **已终值行不改写** + 回填只落实跑集无结果 case 断言（详设 §3.4 + §12.4 R-22）。**验证目标**：§12.4 单测并入 CI（复用 O-E.7 断言，抽为独立护栏文件）。
  - ✅ **2026-09-15 落地**：`tests/integration/test_integration_scanner_error_run.py` 4 条
    （scanner 租约/硬超时两入口 + 计划数>加载数 + 已终值行不改写），真库、判别力经反向对照实测。
  - ✅ **收尾语义本身已有覆盖**：`tests/test_error_regression_run.py::TestFinishErrorRegression`
    有 **7 条无 DB 单测**钉死 `external_terminal=False` 整块（completed / ≥1 na→partial_failed /
    cancelled 压过 na / 空集→cancelled / `error_case` 与 `agent_score` / 外部终态不覆盖 /
    回填 na + `scheduler_unexecuted`）。
  - ❌ **缺口 = 「入口调用方」未被驱动，不是收尾逻辑**：`_run_error` 的两条早退均无测试 ——
    `:379-382` 前置校验不过 → `_mark_error_skipped`；`:384-386` 接管时已取消 → **直接 return、
    完全不调收尾**（run 停原状态、`fire_push` 不发 ⇒ link 挂到 scanner 回收）。
    R-22 另两条路径（orchestrator cancel / run 级超时）亦属同一形态：**被调函数已验、调它的入口未验**。
  - ✅ **2026-09-15 补（订正上行的 ❌ 判词）**：`dev` `5b01de5` **补的正是被点名的「两条早退路径」** ——
    `backend/tests/test_error_regression_run.py:443` `test_cancelled_at_takeover_skips_execution_and_finish`
    + `:457` `test_precheck_failure_marks_skipped_without_finish` + `:470` `class TestMarkErrorSkipped`（2 例）。
    ⇒ 上行「`_run_error` 的两条早退均无测试」与「**被调函数已验、调它的入口未验**」**均已不成立**。
    **核实命令**：`git show 5b01de5 --stat`。（同一失真亦登记于 `error-backflow-status.md` 顶部「已知失真 2」——
    该表按自身「全表暂停、保留原判词」惯例**不改判词**，此处按本文件惯例**就地订正**。）
  - ⚠️ **已撤除的工作（留痕，勿重复立项）**：2026-09-15 曾据「`if not external_terminal:` 整块
    零覆盖」新增 `tests/integration/test_integration_error_finish.py`（5 条真库用例，判别力对照
    4 条变异各只打红一条，实测通过）。**该前提为假** —— 立项时只扫 `tests/integration/`、
    未扫单测目录。该文件经验证后**整体撤除**（非因有错，而是它只把单测级覆盖换成真库级，
    答不上「不做会出什么具体故障」）。**教训：「某分支无覆盖」是可证伪断言，下结论前必须先定死
    扫过的测试面。**
- **O-G.3 §12.1 测试矩阵全绿核对**：详设 §12.1 测试矩阵逐条通过（#1-17 + KeyedLimiter #23 + R-8/R-12 用例 §12.2 等行）登记到集成报告（详设 §12.1/§12.2）。**验证目标**：矩阵全绿 = G6 出口。
- **O-G.4 R-25 出站载荷契约护栏（护栏）**：钉死推送载荷与 online 接收侧**逐字段一致** —— `schema_version`（必须 `'1.0'`）/ `run_status` Literal 四值（completed|partial_failed|timeout|cancelled）/ `cases[].pass_fail` 三值（pass|fail|na）/ 10 个字段名与必填性（**`prev_terminal_version` 必填但值可 null**）/ 字段长度上限（64/48）；**并锁两水位字段的取数口径** = agent 级**全部**终态 run（含 manual/held_out），**不得按 `trigger_type='error_regression'` 收窄**（误收窄 → 与 online「缺行中断」判据系统性错位 → 假中断）。**为什么必须常驻护栏**：v1.23 反转后 **offline 是发送方** —— online 增删改字段时 offline 不报错，只会**静默拒单或漏判**；与 R-19（executor 常量逐字符对齐）同类，但**跨仓、更脆、无编译期保护**。**验证目标**：字段名/类型/必填性/取值域逐条断言；online 侧字段增删改 → 本护栏红（字段清单以 online `api/backflow.py` 为唯一事实源，其变更须同步改本护栏）。

**阶段出口（G6）**：R-19/R-22/**R-25** 护栏单测并入 CI + §12.1 矩阵全绿（详设 §1.3 G6）。

---

## 横切实现/核对项登记（独立立项 + CI 护栏，来源 = online task.md 环 0 L49 登记 / 详设注记）

| R | 内容 | 性质 | 落点任务 | 门 | 验收锚 |
|---|---|---|---|---|---|
| R-3 | 版本差集对账（online 已发版无 error run 迟到补建） | offline 机制 | O-E.2 | G4 | §12.1 #5；环 2 场景 17；online E-27 输入侧 |
| R-4 | ❌ **v1.23 整条取消**：cap 溢出 `excluded_case_ids` 只读面透出 → 降级为 offline 本地诊断（列保留在 O-B.2 模型，不再对 online 透出） | ~~offline 只读面~~ | —（原 O-F.2） | G5 | 无（不落码） |
| R-5 | ❌ **v1.23 整条取消**：agent 已见版本只读面 → 改由载荷两水位字段 `agent_latest_version`/`prev_terminal_version` 承载 | ~~offline 只读面~~ | O-F.7（原 O-F.2） | G5 | 载荷字段 = claim 软校验/K 序列数据源 |
| R-8 | cap_gap 探测态节流（invalidated ack 只发一次 + 本地探测补齐） | offline ack 语义 | O-D.4 | G3 | 探测节流断言；§12.1 R-8 行 |
| R-10 | offline 零机制（`input_truncated` = online needs_review 侧 reason 4，EvalRun 无截断列） | 零机制核对锚 | O-B.2 | G1 | §4.4 L242 核对（EvalRun 不加截断列，仅核对不落码） |
| R-12 | text.py 空答 fail 修补 | offline code（G0 门禁先行） | O-A.1 → O-E.9 | G0→G4 | **✅ 2026-09-15 落地**（G0 当日判过 = 选项 A）。空答 FAIL 单测两形态 + 反事实对照；**白名单豁免项无对象**（pre-scan Q② 判空集）⇒ 该项以「空集落位」结清，非漏做。§12.2 |
| R-19 | error_type 字面量一致性单测护栏 | CI 护栏 | O-G.1 | G6 | §12.3 单测并入 CI，常量增改即红 |
| R-20 | pre-scan 报告门禁（三问三答 + 处置登记） | 实施门禁（owner = offline 实施） | O-A.1 | G0 | 报告产出 = G0 出口 |
| R-22 | 收尾回填 na 统一 `scheduler_unexecuted` 单测护栏 | CI 护栏 | O-E.7 + O-G.2 | G4+G6 | 收尾逻辑 = 7 条无 DB 单测 + 4 条真库集成用例；~~⚠️ **缺口在入口**（`_run_error` 两条早退无测试）~~ **⚠️ 该判已失效（2026-09-15）**：`5b01de5` 已补这两条早退 + `TestMarkErrorSkipped`，**入口缺口已闭合**（依据见 O-G.2 条目下的 2026-09-15 补） |
| R-25 | 出站载荷契约护栏（10 字段与取值域逐字符对齐 online `api/backflow.py` + 两水位字段取数口径不得按 trigger_type 收窄） | CI 护栏（**跨仓契约锁**） | O-G.4 | G6 | 字段名/类型/必填性/取值域断言；online 侧字段变更即红 |
| R-26 | 出站基址「容器名」不可达（`core/http.py` 双 Host 头 ⇒ `LocalProtocolError`；**根因在共享客户端，影响面 = 一切容器名出站**） | offline core（**A 级**） | O-F.9 | **✅ 已修（2026-09-15 批 4）**：根因已修 + 绕过已撤除 | 护栏**已反转**为 `test_base_host_not_special_cased`（再塞白名单即红）；Host 去重判据在 `tests/test_security.py`（3 条）；**真机三判据**已取（解析 172.23.0.4 / `pull_payloads()` 200 / 旧形状 `LocalProtocolError`）；反事实恰好 2 红 |

## 不做清单（归属明确，勿误入本 task 范围）

- **online 侧实现清单**：R-13 同簇刷新复制（detail §6.2）、~~R-16 excluded_case_ids 落 runs list/detail 字段位~~（**R-16 v1.23 已整条作废**）、R-21 `trace_judge_state.root_late_complement` + 消费 step4 root-late 补判、R-24 requeue guard 状态域——归 **online 仓**（online task.md 环 2 E-28/E-29 + L49 实现清单）；**offline 侧对应物 = 主动推送载荷（O-F.7），原 M7 只读面字段位（O-F.1/O-F.2）随 v1.23 整组取消**。
- **二期**：L3 quality/会话型回归、Kafka 传输（HTTP pull 保持）、error case/inbox 长期驻留归档策略、`inconclusive` 三值判定、error run 无信号自动重跑、跨端 deactivate/case 归档闭环、离线触发覆写（详设 §1.2 + 批 1 §1.3/批 2 §1.2）。
- **前端只读标注**（error suite 只读样式）：归前端仓（详设 §1.2）。
- 平台本体（manual/held_out 评测主链）非 error 语义的既有行为**不回归性改动**——凡 O-* 触及的改动都以「manual/held_out 行为等价」为护栏（详设 §8.3 步 2 / §11.1 三层隔离）。

## 测试与联调环归属

- **环 0（契约）**：已由批 1 契约定稿（pull/ack/回写 + **结果推送出站** + schema_version + case_type 白名单 + **入站 pull_token / 出站静态预共享 secret** + 游标/ack 语义 + R1~R3/R-4~R-24 修订；**结果回传方向 v1.23 反转为 offline 主动推送**）——本 task 的前置输入，实施以环 0 契约形状造 fixture/stub 样例（O-D.5）。
- **环 1（单端 stub）**：offline 侧 = 阶段 C/D（O-C.* 纯函数 + O-D.5 fake-online stub + O-D.2~O-D.4 收单/建 case/ack 闭环），仓内全绿无需真对端（详设 §12.5 + 批 1 §12.2）；集成异常/边界验收用例 = 详设 §12.6 X-1~X-7。
- **环 2（双端集成）**：阶段 F（O-F.1~O-F.8），同机双端 + 本地共享 infra（MySQL 分库），走 phase2 §11.2 场景 1-22 + online E-1~E-29 中 offline 侧依赖用例；集成异常/边界验收用例 = 详设 §12.6 X-8~X-11（X-9/X-10 对 online X-11 对端面）。与 online task.md 环 2 出口一致。
- **环 3（真实流量灰度）**：不在本 task（属放量阶段，online task.md 阶段 5；backflow_enabled 上线隐含 G0 已过/R-12 已合入，详设 §9.3）。

## 风险与关注（本 task 层面）

- **G0 先行**：R-12（O-E.9）未过 G0 不得开工；pre-scan 与现网结论冲突时按详设 §9.4/§13 行 12 处置（受影响面大 → 豁免清单先行、分批合入，不在详设写死豁免实现）。
- **与 online 接收侧时序**：M7 **推送载荷字段位（O-F.7/O-F.8）**须与 online 接收侧（`POST /backflow/regression-results` 的 10 字段与判定内核，R-13/R-21/R-24；**R-16 已作废**）在环 2 前对齐；offline 是**出站侧**，字段以 online `api/backflow.py` 为唯一事实源，机制在 online 仓。
- **现码行号漂移**：详设 §2/正文行号是 2026-09-07 核读值，实施以当时 HEAD 重对；任何漂移先改详设再动代码。
- **护栏回归**：KeyedLimiter 进程级层（O-E.4）、case_loader error 谓词（O-E.5）、共享 `_finish` 隔离（O-E.7）必须保住 manual/held_out 行为等价——回归断言是硬要求（详设 §8.3 步 2 / §12.1 #23）。
- **迁移与建表冲突**：O-B.4 迁移与 ORM `create_all` 双轨并存（详设 §4.5 验证行），先迁移后冒烟。
- **详设内部路径笔误提醒**：详设 §9.3 标题与 §12.2 标题的 `core/assertions/text.py` 与 §2.4/§6.4 class_path 的 `assertions/ops/text.py` 不一致——**真实文件 = `backend/app/assertions/ops/text.py`**（§2.4 L92 已实证；本 task 全篇以此为准；改码前先修详设这两处标题措辞防误导，属待办小修）。
