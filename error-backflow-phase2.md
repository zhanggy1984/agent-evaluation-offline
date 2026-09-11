# 线上观测平台 error 回流 —— offline 侧配套改造方案（批 2：error_regression run 执行语义 + 判定器复现与 verifier no_fallback）

> **定位**：Task #4 批 2 方案（= online `task.md` T-3.8 落点），**叠加于批 1 实施之后**（批 1 = `error-backflow-phase1.md` v0.2.2）。本批交付 error case 的**真实判定闭环**：error_regression run 执行语义、自动创建触发、executor 复现 + verifier no_fallback 判定、offline 推送回传（online 被动接收即判）。
>
> **契约**：error-only run 语义引用 online `solution_detail.md` **v1.23**（批 2 B 包按本稿落字于 v1.3、R5-R7 auto-fixed 判定语义落字于 v1.4、**R-1/R-2 修订包落字于 v1.5**、**R-3~R-10 修订包落字于 v1.6**、**R-18/R-19 修订包落字于 v1.7**、**R-13~R-17 修订包落字于 v1.8（v0.7.2 补录引用）**、**R-20~R-24 修订包落字于 v1.9（本批 v0.7.2；R-21/R-24 为 online 侧机制/语义、offline 侧零机制项随本批落 §2.3 v0.7.2 注/§7.2 R-20 注/§10）**，落点与回执见 §10）；offline 侧对等引用批 1（文中「批 1 §x.y」）。
>
> **⚠ 前置依赖（关键）**：截至本稿（2026-09-03）批 1 代码**尚未落地**——backend 全仓无 `case_type / is_error_suite / error_regression / no_fallback / pull_loop` 标识符，`EvalRun.trigger_type` 仍为 `("manual","held_out")`，`TestCase` 三列仍 `nullable=False`，无推送端点文件（原「平台只读面」随 v1.23 契约反转整组取消）。**批 1 必须先实施（含 alembic DDL、激活器 pull_loop、403 写守卫、error suite、`_load_run_cases` error 分支、5 行早退闸、rerun 拒绝守卫），批 2 才能落地**（原「平台只读面」一项随 v1.23 契约反转**整组取消**，结果改由 offline 主动推送 `POST /backflow/regression-results`）。本稿凡引用"批 1 承诺/依赖批 1 落地"处均以此为前提；实施顺序：批 1 代码 → 批 2 代码。
>
> **状态**：**v0.8.0**（v0.7.2 基础上 + **2026-09-11 v1.23 契约反转回填（T-3.8 批 2b，纯文档口径回填、offline 运行机制零改动）**：§9.3 由「平台只读面」整节改写为「**推送载荷与出站行为**」（端点 / 触发时机 / 鉴权 / 失败语义 / 载荷 schema / 响应字段 / 拒单规则 / 幂等键，八项新增定义）+ §6.3 收尾表新增「推送」行 + §9.4 载体同步 + R-4/R-5 只读面取消，详见 §13 v0.8.0 行）。**v0.7.2**（v0.7.1 基础上 + **2026-09-07 修订包 R-20/R-22/R-23 落字**：R-22 收尾回填 na 统一钉死 `scheduler_unexecuted`（scanner 回收 / orchestrator cancel / run 级超时收尾三路径同源，§6.3 v0.7.2 注 + §6.4 源行补注，timeout 不作收尾回填原因）/ R-23 timeout 三层语义分界（case 级 na 源 / run 终态 timeout = 半途中断不直接触发 needs_review、claim TTL 兜底 / run 级超时收尾回填 na = 环境级 scheduler_unexecuted → 关联 pass case 入 unclean_run 批，§2.3 v0.7.2 注③ + §6.4）/ R-20 复现 run 空答 fail 语义定性 = leakage 空话术复发证据 + **pre-scan 显式立项 Phase B code_detail 实施门禁**（§7.2 R-20 注 + §11.1 补注 + §10 B 项登记；R-12 core 修补不动、本批 offline 运行机制零改动）——online 已落字 `solution_detail.md` v1.9 + `solution.md` v3.5.9，详见 §2.3 v0.7.2 注 + §13 v0.7.2 行）。**v0.7.1**（v0.7 基础上 + **2026-09-07 高危 2 条 R-18/R-19 落字**：R-19 error_type 判据字面量对齐 executor 真值 + 补漏项 `no_usage`/`contract_error`（§6.4 矩阵行 1/2 + 影响域语义块 + §11.1 镜像，权威标注修正、offline 零机制改动）/ R-18 input 明文载体 offline 零改动确认（§2.3 v0.7.1 注②，D19 evidence.input ≤8K 截断快照边界不变）——online 已落字 `solution_detail.md` v1.7 + `solution.md` v3.5.7，详见 §2.3 v0.7.1 注 + §13 v0.7.1 行）。**v0.7**（v0.6.2 基础上 + **2026-09-07 修订包 R-3~R-10 落字**：R-3 对账版本差集补齐（§5.3）/ R-4 cap 溢出 case 只读面透出（§9.3）/ R-5「agent 已见版本」只读面（§9.3）/ R-8 cap_gap 探测态节流（offline 现稿行为修正、代码单独立项）/ R-10 input 截断复现边界注记 / R-11 环 2 场景并入 §11.2——详见 §2.3 v0.7 注；online 已落字 `solution_detail.md` v1.6 + `solution.md` v3.5.6，offline 机制改动（R-3 对账差集 / R-8 节流 / R-4/5 只读面）代码变更单独立项，phase1 稿保持基线）。**v0.6.2**（v0.6.1 基础上 + **2026-09-07 修订包 R-1/R-2/R-12 落字**：auto-fixed K 改 claim 级固化 `claim_k` / 纯净判据下钻 claimed case 行 + na error_type 影响域分流 / verifier 空串/纯空白应答保守 fail 修补决策——详见 §2.3 v0.6.2 注 + §6.4 影响域标注列 + §7.2/§11.1 R-12；online 已落字 `solution_detail.md` v1.5 + `solution.md` v3.5.5，offline 运行机制零改动、R-12 代码变更单独立项）。**v0.6.1**（v0.6 基础上 + **2026-09-07 终审通过，3 处消费语义补定义**：聚合与 reason 值域 / 纯净可判判据 / K-TTL 收敛终点（见 §2.3 v0.6 注 ①②③ + §10 B-10~B-12）——R5-R7 由方向性登记精化为 **online 落字规格，已落字 `solution_detail.md` v1.4 + `solution.md` v3.5.4（2026-09-07）**，offline 侧运行机制零改动。历史：v0.6 在 v0.5.2 基础上 + **2026-09-03 数据流三案裁决 R5-R7（用户确认后落稿，方向性契约修订）**：auto-fixed 判据重构 = 纯净 run 前提 + 跨版本稳定序列 K / reentry 同版本复验禁 auto-fixed / 老 case 窗口中断 K = 接受退化诚实欠测——全文 §2.3 v0.6 注 + §10 B-10~B-12 登记，**待落字 online `solution_detail.md` v1.4**，offline 侧运行机制零改动）。v0.5.2 全部内容（落字回执 P0 + 措辞收口 P1 + 终审再修 1P1+4P2）保留于文中。待用户终审。 + **2026-09-03 落字回执并入（P0）**：online B 包已按本稿落 `solution_detail.md` v1.3（A1-A7 + 三处排查补正：needs_review_batch 载体定稿/批量端点/整批原子/reentry 同日 count+1 可见性提示）+ `solution.md` v3.5.3 + `task.md` 环 0 B-7，全文「现文 v1.2/仅 offline 主张/需确认后落」框架回正、风险 13 解除；**P1 措辞收口**：na 源计数统一五源矩阵、洪峰收敛创建侧落点、终态写守卫收尾同事务顺序）；**终审再修（2026-09-03，1 P1 + 4 P2）**：P1 心跳续租粒度钉死（固定时间片 ≪ lease TTL 覆盖单 case 执行窗，禁仅逐 case）、在途判定丢失显式声明（§6.3）、§11.1 na 矩阵镜像五源、§10 小结块快照注、offline 触发侧断链对称（风险 17/场景 13）。待用户终审 —— **v0.6.1（2026-09-07）已终审通过，3 处消费语义定义补毕（§2.3 v0.6 注 ①②③），已落字 online `solution_detail.md` v1.4 + `solution.md` v3.5.4 + `task.md` 环 0 登记（2026-09-07 收口；commit 待用户明示）**。
>
> **裁决记录**：2Q1 复用断言引擎 / 2Q2 复现失败 na / 2Q3 版本变更自动触发 / 2Q4 单 fix 版回归（v0.1 并入）；**R1 两平台同用 semver**（v0.2 并入；**v0.4 修订：收缩为跨端「版本字面量域」**，见 §2.4）/ **R2 执行期 agent 并发闸**（v0.2 并入；**v0.4 修订：共享 per-agent 执行并发槽池，废弃 run 级排他窗**，见 §2.2/D7/§6.2）/ **R3 分母治理 v1 裁决 = 守卫驻留 + 诚实欠测**（v0.5 并入，§6.1 注 1/2，v1 不引入跨端 deactivate）/ **R4 放弃离线触发覆写**（v0.5 并入，§2.3，false-fixed 收敛 reentry/下轮 claim）；**R5 auto-fixed 只接受纯净 run**（run 级 0 技术性 na，含 na 的 run 内 pass 不置 verify passed → needs_review，v0.6 并入）/ **R6 auto-fixed = claim fix_version 起连续 K 个纯净可判版本 run 无 fail**（K=2 默认可配；老 case 窗口截断中断 K → 接受退化诚实欠测，v0.6 并入）/ **R7 reentry 同版本复验禁 auto-fixed**（generation>1 cluster claim 同已完成版本 → 旧 run pass 转 needs_review，不放开 completed 不重建守卫，v0.6 并入）——详见 §2.3 v0.6 注 + B-10~B-12。

---

## 0. 定位与切刀背景（读前须知）

批 1 实施后，offline 侧 error 回流只走到「error case 激活 + ack 闭环 + ~~只读面可查~~（**v1.23 取消**：只读面整组取消，结果改由 offline 主动推送）」，无任何真实判定：批 1 §7.1 两处安全闸（create_run trigger 白名单只放 manual/held_out；orchestrator._run 对 error_regression 早退）保证 error_regression run「模型可建、不可执行」。本批放开这两处闸，让 error_regression run 真正跑起来。

**现码事实修正（v0.1 评审实测，相对 v0.1/批 1 表述的校准）**：

1. 批 1 §7.1 称「error case 三列 None 触发 `_enabled_semantic_dims` 异常」——现码（`scorer.py` `_enabled_dims`，metrics 空 → `return set(ACCURACY_DIMENSIONS)`）**不抛异常**，而是返回全 accuracy 维度启用、会误建 LLM-judge。真实风险是「全维启用的错误行为」→ error run **整体绕过 scorer/judge** 是硬要求（§6.2），不依赖"某处会抛"兜底。
2. **agent_mutex 不包执行期**：`core/lock.py` agent_mutex = `SELECT Agent FOR UPDATE`，现码只在 create_run/rerun_run 建单互斥与 reset 消费；`start_run/_run` 全程不持锁。同 agent 串行唯一现机制 = 建单时 `_MUTEX_STATUS(pending/running)` 计数 409（`api/runs.py`）。→ v0.1 D7「执行期 agent_mutex 串行」为事实错误，改「执行期 agent 并发闸」（裁决 R2，§6.2）。
3. **scanner 回收会经 salvage 拉 error run 进 scorer**：`scanner.py` 对 lease/hard_deadline 超时 run 无条件 `score_run_salvage`（→ `_score_executed_results` 重跑断言 + 写 agent_score），error run 一旦超时被回收即被污染 → 需短路（§6.6）。
4. 模型已有**独立 `na_case` 列**（`models/run.py`，与 `error_case` 并存）——na 落 `na_case`，`error_case` 语义保持「pass_fail=='error' 计数」（error_regression run 内恒 0），两列口径见 §2.3/§6.3。
5. keyword_not_contains **内存注册即可执行**（`assertions/ops/__init__.py` 已导入、单测已过），无需 DB 行即可 run → v0.1 §7.4「登记遗漏运行期才炸」过强，降格为「发布一致性/读面登记」（§7.4）；真正要防的是批 1 激活器若做 DB `assertion_op_def` 校验则须先补行（§7.4 核验点）。
6. **普通 run 断言在 scorer 阶段执行**（`scorer.py:306 run_assertions`，由 `_finish`→score_run 触发），不在执行器执行路径 → error 分支的断言执行是 **run_assertions 的新调用点**（非"复用既有 per-case 判定封装"），见 §7.2。

---

## 1. 背景、范围与职责边界

### 1.1 本批范围（交付清单）

| 交付 | 说明 | 主要落点 |
|---|---|---|
| A. error_regression run 自动创建触发 | 被测 agent「发版」登记 → 幂等自动建 run（§5） | api/runs.py 信号点 + 内部创建器 |
| B. error_regression run 执行语义 | 放开早退闸；执行期 agent 并发闸；不经 SCORING；独立收尾（§6） | orchestrator / case_loader / scanner 短路 |
| C. 判定器 executor 复现 + verifier no_fallback | 复现打被测 agent；no_fallback 断言判 pass/fail 落终值（§7） | executor 复用 + run_assertions 新调用点 |
| D. error case 激活补写 no_fallback 断言 + 存量 backfill | 激活器写断言（含词级净化）；存量 active error case 补写（特权直写、幂等，并入补偿对账）；verifier 空断言→na（§7.3/§7.5） | pull_loop 激活路径 + backfill（§5.3 对账兼任） |
| E. keyword_not_contains 发布一致性登记 | 白名单/seed/上线核验（§7.4） | core/constants + seed |
| F. 统计/清理/占位隔离收口 | dashboard 排除 + 活跃计数排除 error_regression + pinned 豁免（§9） | dashboard.py / runs.py / cleanup_rules.py |
| G. CaseVersion 惰性接通 | error run 落 result 走既有 `_ensure_case_version`，不预建（§8） | orchestrator |
| H. 测试与环 2 联调 | 判定矩阵 + fixture 桩 + 双端闭环（§11） | backend/tests + fixtures |

### 1.2 本批不做（边界，二期/后续）

- `inconclusive` 三值判定（v1 无，契约 fail-closed，二期）。
- 传输通道 Kafka（批 1 风险 7，平台间 HTTP 保持）。
- error case / inbox 长期驻留运营策略（批 1 风险 4，本批只保证 run 级清理豁免与终态可查）。
- **error run 的无信号自动重跑**（无新发版信号时对 partial_failed/timeout/cancelled 自动补跑）——不做。online 侧兜底修正（v0.4 A3 契约校准）：online **不存在**「超时→needs_review」的既设兜底——needs_review 仅由「回归 infra-error 无法判定」触发（error run 的 na 载体）；online 对「无推送入站（online 侧无对应 `verify_run_record`）」的既有兜底 = **claim TTL（14d）超窗 → 回退 open**（solution_detail §10.1/§7.6），本稿全部相关措辞以此为准。同版本**新信号**到达时对坏终态（timeout/cancelled）重建、对可判终态（completed/partial_failed）不重建，且**重建以「该信号为新（未被消费）」为前提**（§5.2/§5.3 consumed 锚，防补偿对账周期循环，v0.4 K1/B1）；显式重建入口随 §5.4 兜底入口一并留二期（§12 开放项）。
- **离线触发覆写 v1 不做（v0.5 R4）**：迟到 error run 不覆写 online 已判定的 claim（online §7.6 终态只读，无「离线迟到 run 触发 reopen」机制，v0.4 A7「复用既有 supersede+reopen 覆写」不成立）。v1 false-fixed 收敛路径 = 错误线上再现触发 reentry（§7.5 版本门控，新 cluster/新 case）或 admin 手动 reopen → 下轮 claim（§2.3/风险 11）。
- **跨端 error case 归档 v1 不做（v0.5 R3）**：error case 守卫驻留、分母单调不减，跨端「online cluster 终态 → offline case 出 active」闭环列二期（§6.1 注 2/B 包 B-9）。v1 以 run 级窗口 + 诚实欠测管理回归成本，不引入跨端新消息。
- mock/sandbox：本批不引入（C3 同精神，桩只用于测试基建 §11.2）。
- online 侧 claim→推送回传→fixed 判定状态机属环 2/online 侧；offline 只如实落库并经推送回传（对接批 1 §8.2；该节只读面随 v1.23 **整组取消**）。

### 1.3 决策来源

现码调研 + 批 1 全文锚点 + 批 2 裁决 2Q1-2Q4（v0.1）+ 双独立评审（逻辑/契约 + 现码事实）+ 裁决 R1/R2（v0.2）+ 第二轮四方向独立评审（性能/安全/容错/逻辑复核，v0.3 全量修入）+ 第三轮四方向独立评审（契约对接/逻辑自洽/双端容错/全链路负载，v0.4 全量修入）+ 第四轮四方向独立评审（契约双向对表/逻辑自洽/双端容错时间线/全链路负载，v0.5 全量修入，含裁决 R3/R4）。

---

## 2. 契约锚点与字面量对表修订

### 2.1 error-only run 判定语义（契约 §7.3 #4）

> error-only run 的 `pass_fail` = **executor 技术判定 ∧ verifier no_fallback**，verifier 阶段落终值，不经 SCORING、不产 `agent_score`。

本批定义三态：

- **pass**（修复生效）：复现拿到可判输出 ∧ 输出不含固化词表任一词（不再 fallback）。
- **fail**（error 仍在）：复现拿到可判输出 ∧ 输出命中固化词表任一词（仍 fallback）。
- **na**（无法判定，无终值）：**未取得可判终值**。统一不变量（v0.3 逻辑复核 W1 修入；源计数 v0.5.2 统一为 5 源矩阵，见 §6.4）：na = 未取得可判终值，run 判定内可达源 = executor 技术失败（可重试/非可重试）∨ 调度层未执行 ∨ 调度回收 scheduler_unexecuted；判定素材缺失源为防御兜底（加载过滤前置，见 §6.2/§7.5）。各源记固定 `error_type`。na 是**运行期缺判定态**，不是判定值；它是 offline「未判成」的唯一跨端信号载体（online needs_review 触发）。**error_type 进推送载荷 per-case（na case 必带，v0.4：仅 machine-to-machine 载荷，人类读面仍隔离，见 §9.3/§9.4）**——online 以 error_type 分组聚合 needs_review、转 reason 展示（v0.4 I3/S2 修入），不混入终值语义。素材缺失（空/坏断言）的 case **不进 run**（加载过滤 + backfill 对账驱动），其 na 触发被前置消除（v0.4 逻辑 I2 修入，见 §6.2/§7.5）。

error run 的 `EvalResult.pass_fail` 取值域 = **{pass, fail, na}，绝不落 'error' 值**（§6.4 调度层未执行归 na 即为防此，v0.3 容错 B1）。

### 2.2 回归 run 资源语义（契约 §7.3 #3）

> 回归 run 不占互斥槽 / 不参与 max_active_runs / 独立保留档。

**裁决 R1/R2 落地**：

- **活跃计数隔离（评审 B1）**：现互斥是**建单时 DB 扫描同 agent 全部 pending/running 行**计数 409（`api/runs.py:251-253/:378`，`_MUTEX_STATUS=pending/running`，**无 trigger 过滤**）。若不隔离，error run pending 会被计入 → N=1 时堵死后续 manual 建 run。→ 两处活跃计数扫描加 `trigger_type != 'error_regression'` 排除（§9.2），error run **不占该名额、也不堵 manual**。
- **执行期 agent 并发闸（裁决 R2，v0.4 语义：共享 per-agent 执行并发槽池）**：现码同 agent 建单已被 `_MUTEX_STATUS` 计数 409 串行（manual/held_out 之间本不会并发 running）；error run 被计数排除后是**唯一可与同 agent 的 manual/held_out 叠加的 run 类型**。v0.3 曾引入 run 级排他窗（error 整 run 持窗、manual 见窗让位）——该粒度**双向饿死**：error run 长时持窗会整段阻塞同 agent 全部 manual（`per_agent_concurrency=3` 三路全让）；manual 常跑又无让位承诺、error 排队无上界。**v0.4 修正：不设 run 级窗，error run 逐 case 复现执行与 manual/held_out 共享同 agent 的执行并发槽池**（= 既有 per-agent 执行并发限制，实施对表现码 limiter 作用域）——同 agent 的 manual/held_out 与 error 复现**同时执行并发总数 ≤ 槽池上限**，error 复现不叠加出超限路数，互斥由槽池自动保证；manual 侧零改动。不把 manual run 内部既有并发形态压成 1，也不要求「同 agent 全局 ≤1 路」（manual 多路 + error ≤1 路并存于槽池，见 §6.2）。error run 不占 `max_active_runs` 名额（创建不经公开 create_run）。
- **独立保留档**：`pinned=True` → cleanup 现豁免（`cleanup_rules.py` pinned 排除）。对契约 #3「独立保留档」的处置 = pinned 保留终态 + 不新增归档机制（评审 A4 注记：替换批 1 §7.3「清理先归档 pass_fail 终态」措辞，环 2 对表知悉）。

### 2.3 字面量对表补 na_case（对齐批 1 §8.3，本稿修订声明）

批 1 §8.3 约定「批 2 verifier 对 error case 只落 pass/fail（不落 error/na）」——**本稿显式修订**：该约定指**判定成功域**（verifier 产出仅 pass/fail，不产 'error'）；na 是「未取得可判终值」的**运行期缺判定态**（五源矩阵，§2.1/§6.4——executor 技术失败[可重试/非可重试]/调度层未执行/调度回收 scheduler_unexecuted 为 run 判定内可达，素材缺失源为防御兜底），属 error_regression run 的合法取值。此修订的契约依据 = online 侧「回归 infra-error 无法判定 → needs_review」触发源依赖 per-case 无终值信号（solution_detail §7.6 语义），na 正是该信号载体。

- `EvalResult.pass_fail` 四值域，error_regression run 取值域 = `{pass, fail}` + `na`（复现失败），**不用 'error' 值**。
- **列口径（评审 B4/I1 修入）**：现模型已有独立 `error_case` 与 `na_case` 列（`models/run.py`）。error_regression run 收尾：`na_case` = pass_fail=='na' 计数；`error_case` 保持原义（== 'error' 计数，error run 内恒 0）。推送载荷（原「平台只读面」响应，批 1 §8.2：total/pass/fail/error_case；只读面随 v1.23 **整组取消**）**补 `na_case` 字段**（向后兼容加字段）——online 对 na 的 needs_review 判定以 `na_case`/per-case na 为准。批 1 §8.3 对表输出随本稿更新。

**error run 终态 → online 推送入站/判定映射（v0.3 W3 + v0.4 修订：na/缺行拆解、needs_review 触发域、聚合注；批 1 §8.3 对表同步修订项）**：

| error run 终态 | 含义 | online 消费（推送入站判定格） |
|---|---|---|
| `completed` | 全 case pass/fail，0 na | 逐 case：pass→fixed / fail→error 仍在 |
| `partial_failed` | ≥1 na（executor 技术失败/调度未执行，v0.4：素材缺失经加载过滤已前置消除） | **可判终态**：pass/fail 行逐 case 判；**na 行（有 EvalResult、pass_fail='na'）→ per-case needs_review**（仅 claimed case 为 na 才触发该 cluster needs_review，同 run 他 case na 不置该 claim） |
| `timeout` / `cancelled` | 半途中断，已完成 case 保留终值、未完成回填 na | 同上（按 per-case 终值判 + na 行→needs_review），**不因 run 级中断整单丢弃** |

- **缺行 ≠ na（v0.4 B2 修入，统一 §6.3 实跑口径）**：case 在 run 内**无任何 EvalResult 行**（实跑集 < case_ids：加载过滤剔除的素材缺失 case、作废 case、晚到 active case）→ online **按无终值处理**（不触发 needs_review；该版观察中断由载荷水位字段 `prev_terminal_version` 探测，不再靠轮询）。needs_review 的唯一 case 级触发源 = **存在 `pass_fail='na'` 的结果行**。§2.3 表 v0.3 行文「na/缺行→needs_review」中的缺行措辞作废，与 §6.3「无结果行 → 按无终值处理」一致。
- **聚合注（v0.4 I3 承接）**：同 run 内**批量同根因 na**（如被测 agent 整体宕机 → 同一 `error_type` 成片）→ online 对该 run 该 error_type **聚合成单条 needs_review**（携带 run_id + error_type + case 引用列表），**不逐 case 生成**，防 needs_review 风暴（对表注，B 包落字）。
- **needs_review 触发域（v0.4 A3/A5 校准）**：本表全部 needs_review 语义均指「error run na 载体」这一**源**；online 侧 needs_review = 人工复核队列、多源可触发（判分 na 列二期为另一源），两源不互斥、reason 区分——**本稿主张的消费契约已落字生效**（B 包落字 online `solution_detail` v1.3：§7.6 回归判定语义 blockquote + §8.7 回查 status 值集（该端点随 v1.23 只读面**整组取消**）+ §7.5 reentry 门控 + 聚合载体 needs_review_batch；R5-R7 判定语义落字 v1.4：reason 值域扩 {na, unclean_run, reentry_same_version}、needs_review_batch 同 (run,error_type) 至多单条、推送载荷补 `na_case`/per-case `error_type`，§10 落字回执）。
- **同 `(agent, version)` 多 run 选取序**（崩溃重跑/坏 run 重建可产生两个）：**优先最近 `completed`；无 completed 取最近可判终态（status ∉ {pending, running}）**。online 推送入站锚定该 run（v0.5：选取序并入推送入站判定规则 = B-1(b)，claim 未 closed 期间每次推送入站取「当前最近可判 run」判定，有推进即更新——「未定前用新 run」，不是覆写已 closed）。
- **离线触发覆写 v1 放弃（v0.5 R4，取代 v0.4 A7）**：v0.4 声称「已按旧 run 判定的 claim 由 online supersede+reopen 机制覆写」**不成立**——online 现文 supersede+reopen 仅两路径（§7.6 L908 admin 人工废弃 active 行、§6.2 同键复发 reentry 旧 link 让位），均 online 侧驱动；§7.6 L911 终态只读明写「已定 passed/failed/superseded 不被迟到 run 改写，只追加时间线」。v1 **不做离线迟到 run 触发覆写**：若旧坏 run 判 pass 已 closed、后到好 run 判 fail → 该 cluster 停在 false-fixed，收敛 = 错误线上再现触发 reentry（新 cluster/新 case）或 admin 手动 reopen → 下轮 claim（风险 11）。B-2 从 B 包撤销（§10）。
- **截断诚实标记（v0.5 R3 承接）**：run 因 cap 截断（§6.1 注 1）时记 `case_truncated` 标记（含溢出计数），经推送载荷暴露——online/reviewer 能区分「该版全判」与「窗口截断欠测」。溢出 case 不在该 run `case_ids` 内（JSON_CONTAINS 反查不到）→ online 侧对溢出 case 走「缺行≠na → 按无终值处理（观察中断由载荷水位字段探测）」，诚实标记只服务诊断、不改变 online 消费语义。**（v0.7 R-4：溢出从计数升级为具体 case 列表透出 —— **v1.23 取消**，理由 = 推送为全量对账，不存在「缺行→轮询」，亦不存在只读面；见 §9.3）**

> **auto-fixed 判据重构（v0.6 数据流三案，R5-R7，2026-09-03 用户确认）——offline 侧零机制改动、纯 online 消费语义修订主张，已落字 detail v1.4（B-10~B-12，2026-09-07）**：上表 v1.3 落字语义「completed → 逐 case：pass→fixed」把 **fix_version 单 run、单 case、单次 pass** 当 auto-fixed 依据，三案裁决重构为「**纯净 run + 跨版本稳定序列**」：
> - **R5 纯净 run 前提（挑战 1）**：auto-fixed 只接受**纯净 run**（run 级 0 技术性 na；技术性 = error_type ∈ {executor 技术失败·可重试/非可重试、调度层未执行、调度回收}，§6.4 矩阵行 1/2/3/5；素材缺失源加载过滤前置不可达）。含技术性 na 的 run = 复现环境不可靠（慢 agent/接口抖/breaker）→ 该 run 内 **pass 行不触发 verify passed** → 转 needs_review（reason 带 run_id + `unclean_run`）；fail 行不受影响（强证据即时 reopen）；不纯净 run 不参与 K 计数也不清零（中断观察非否定）。
> - **R6 跨版本稳定序列（挑战 2）**：auto-fixed = 该 case 自 claim fix_version 起、**连续 K 个纯净可判版本 run 中均无 fail**（K=2 默认、可配；fix_version 自身为第 1 版确认；K=1 即兼容现行为）。fail 任何版本 → 即时 reopen；缺行≠na 不计数不终止（观察中断，等下一次推送；claim TTL 超窗回退 open 兜底）。**载体 = online `verify_run_record` 逐版本时间线（detail §5.1 ⑧ 已存在）**——消费侧只改判定逻辑，无新表、无新 offline 机制。失真假 fixed（单点 input 打不出）随 K 个**独立版本样本**累积而指数稀释（R4「reentry 收敛」在该稀释下才有保证）。
> - **R6 边界声明（老 case 窗口耦合，用户拍板）**：error case 被 newest-active-first 窗口（§6.1 注 1）截断挤出后续版本 run → K 序列中断 → 该 case 缺行≠na → claim TTL 回退 open。**接受退化、诚实欠测**——不引入跨端机制给 claim case 保窗口优先级（与 R3 守卫驻留同精神）；`case_truncated` 使 online 能区分「窗口溢出缺行」，诚实标记不伪装。
> - **R7 reentry 同版本禁 auto-fixed（挑战 3）**：reentry 产物 cluster（generation>1）claim 填的 fix_version = 该 (agent,version) 已有 completed run 的版本 → **该 cluster 不得用旧 run 的 pass auto-fixed**（旧 run 是修复前证据，不代表复发后状态）→ 推送回传 pass → 转 needs_review（reason 带 run_id + `reentry_same_version`，提示升级验证需人工或升版）；改填新版本 → 新 run 自然进入 R5/R6 正常判据。**§5.2「completed 不重建」守卫不放开**（重建通道会引入同版复验振荡，且动 consumed 锚/幂等机制成本高收益低）。

> **v0.6.1 消费语义补定义（终审 ①②③，2026-09-07——把 R5-R7 精化为 online 落字规格，纯消费侧定义、offline 零机制改动保持）**：
> - ① **聚合与 reason 值域（R5/R7 × B-4 needs_review_batch 载体）**：na 行沿用 batch（聚合键 run_id+error_type）；`unclean_run`（含技术 na run 内的 pass 行）→ 同 run 成片 pass 行聚合为**单条 batch**（reason=`unclean_run`，附该 run 技术 na 的 error_type，link_refs 引 pass case 列表），resolve 沿用 {reopen_cluster, escalated} 整批单事务；`reentry_same_version` → **claim 级单条、不产 batch**，走 cluster 级既有 `POST /backflow/clusters/{id}/needs-review-resolve`（reason=`reentry_same_version`）；三类 reason 值域随 v1.4 登记 online detail §1.4/§7.6/§12.1。**needs_review v1 源口径 = claim 回归 error run 结果行的判定产物**（na 结果行 → reason `na`；不纯净 run 内 pass 行 → reason `unclean_run`；reentry 同版本 claim 旧 run pass 行 → reason `reentry_same_version`；均属 error run 结果行、run 超时非源保持）。**撞键裁定（v1.4 落字细化 ①，2026-09-07）**：na 批与 unclean_run 批共用聚合键 `(run_id, error_type)`，不纯净 run 同时含 na 行 + pass 行时分建会撞 needs_review_batch 唯一键 → **同 (run,error_type) 至多一条 batch**：na+pass 混合 run 的 na+pass 行**并入单条 `unclean_run` 批**（不另建 na 批）；仅含 na 行（无 pass 行）的 run 才建 reason=`na` 批。
> - ② **纯净可判判据（R5 × verify_run_record 消费）**：逐版本判定「纯净可判」= 该 (agent,version) error run 达终态（status ∈ {completed, partial_failed, timeout, cancelled}）且 `na_case==0`——纯净性由 online 消费侧从关联 run 的 `na_case` **派生**，verify_run_record/needs_review_batch 均**不加纯净性列、offline 不加字段**（R6 载体承诺保持）；纯净可判且无 fail → K 步进；fail → 即时 reopen 并清零；含 na run 内 pass → needs_review(`unclean_run`)，不计 K 不清零；缺行 → 中断观察不计数不终止。
> - ③ **K-TTL 收敛终点（R6 × claim TTL 竞态）**：verify passed 仅在 K 满时置；收敛 = **两时钟先到者**——K 满 → cluster fixed（TTL 停钟）/ claim TTL(14d) 超窗 → 回退 open（K 观察中止、下轮 claim 重计）；TTL 自认领起算，K 观察/推送入站推进**不重置、不延长** TTL；缺行/不纯净中断观察延续至下一次推送（K 满）或 TTL 超窗，无无限期挂起。

> **v0.6.2 修订注记（R-1/R-2/R-12，2026-09-07 逐问评审拍板、已落字 online detail v1.5 + solution v3.5.5；offline 运行机制零改动保持）**——上 v0.6 注 R5-R7 / v0.6.1 注 ①②③ 的消费语义随修订包精化，正文 v1.4 语义保留为历史基线：
> - ① **K 取值 = claim 级固化 claim_k（R-1/A1）**：v0.6 注 R6「K=2 默认、可配」的配置载体从 run/全局下沉为 **cluster 级固化值**——online error_cluster 增列 `claim_k`（TINYINT UNSIGNED，值域 {1,2}，DEFAULT 2；claim CAS 同批写；缺省取全局 `dict_config.auto_fixed_k_default`=2 并**写死固化值**，不实时读全局键）；claim 生命周期内 **零推进可降（2→1）、推进后锁死**；K 只读固化值。claim_k=1 = fix_version 纯净可判 pass → 推送入站即 verify passed（K=1 兼容现行）；TTL 回退 open 后重 claim 可改值、TTL 自新认领起算（重 claim 同 fix_version 表单动态提示依赖 R-5 另列 —— **v1.23 取消**：R-5「agent 已见版本」只读面整条取消，其守卫语义改由载荷水位字段 `agent_latest_version` 承载）。offline 零机制：载体 = 既有 verify_run_record 逐版本时间线，判定纯 online 消费侧。
> - ② **纯净判据下钻 claimed case + na 影响域分流（R-2/B1）**：v0.6.1 注 ②「纯净可判 = 终态 run 且 `na_case==0`」粒度过粗（打包 run 内无关 case 技术 na 误伤他 cluster 真实 pass）——判据锚点下钻 **claimed cluster 对应 case 在 run R 的行 + run 内 na 的 error_type 影响域**：**环境级 na**（circuit_open/interface_disabled/scheduler_unexecuted/http_client_error/pool_error）→ 整 run 复现会话不可靠 → 该 run 内 pass 行不置 verify passed → 触发 unclean_run；**case 级 na**（v0.6.2 本注初稿 = `timeout/connect/sse_parse/http/no_done/body_too_large/missing_assertion/assertion_shape`，**v0.7.1 R-19 追改：字面量对齐 executor 真值并补漏项 = `timeout/connect_error/sse_parse_error/http_error/no_done/body_too_large/no_usage/contract_error/missing_assertion/assertion_shape`**，见 §6.4 权威标注列 + §2.3 v0.7.1 注①）→ 只说明该 case 无证据、不牵连他 cluster 已跑通 pass（claimed case 自身 na 才单点 needs_review）。影响域权威标注列已加 **§6.4**（本稿）；online 按标注归类，**无标注的新 error_type 默认从严 = 按环境级 → unclean_run + 告警**（从严仅兜底非常态）。unclean_run 批纯化：needs_review_batch 仅承载「环境级 na 污染下 pass 行 cluster」，纯 na cluster 退批走 cluster 级单点、撞键裁定收窄。
> - ③ **R-12 verifier 空串保守 fail（offline 机制微改例外）**：`KeywordNotContainsOp` 对 **strip 后空串/纯空白应答 → 保守 fail** 的决策已拍板，落 §7.2 注 + §11.1 单测矩阵行（同 path 缺失/非文本 fail 层）；共享算子微改影响普通 run 断言 → **不在本批 offline 零机制范围、代码变更单独立项**。

> **v0.7 修订注记（R-3~R-10，2026-09-07 逐问评审拍板、online 已落字 detail v1.6 + solution v3.5.6）**——本批修订含 **offline 机制改动**（R-3 对账差集补齐 / R-8 cap_gap 探测态节流 / ~~R-4/5 只读面新增~~ **v1.23 取消**），突破「offline 零机制改动」承诺，与 R-12 同类：**本稿仅做决策 + 规格登记（§5.3/§6.1 注/§9.3 字段/§11.2 用例），机制代码变更单独立项、phase1 稿保持基线**；R-4/R-5 随 v1.23 **取消**（推送为全量对账，不存在「缺行→轮询」，亦不存在只读面）；R-7/R-9/R-10 的 online 消费语义已由 detail v1.6 承载，offline 侧仅边界注记：
> - ① **R-3 对账版本差集补齐（offline 机制改动）**：§5.3 低频补偿对账由「单最新锚」升级为「**版本差集逐版补建**」——中间版本不再因活跃闸拒建而永久丢（详见 §5.3 v0.7 落字块）。online 语义零改动（推送入站 B-1(b) 多 run 选取序已覆盖多 run）。
> - ② ~~**R-4 cap 溢出 case 只读面透出（offline 只读面新增）**：cap newest-active-first 截断的溢出从「计数（case_truncated）」升级为「**具体 case 列表透出**」——run 只读面补溢出 case 列表字段（新增只读、不改 run/判定语义）；online 回查据此判「该版本窗口外欠测」非修复失败（§9.3 v0.7 注 + §11.2 场景 18）。~~ **v1.23 取消**——理由 = 推送为全量对账，不存在「缺行→轮询」，亦不存在只读面。
> - ③ ~~**R-5「agent 已见版本」只读面（offline 只读面新增）**：新增 agent 已见版本列表读面（manual/held_out runs distinct version + 最近终态时间，只读不改 run 语义）——online claim 表单软校验/版本活跃度动态 K 提示的数据源（§9.3 v0.7 注 + §11.2 场景 19）。~~ **v1.23 取消**——只读面整组取消，其守卫语义改由载荷水位字段 `agent_latest_version` 承载（manual_invalidate 语义不变，§6.5 仍可重发）。
> - ④ **R-8 cap_gap 探测态节流（offline 现稿行为修正，代码单独立项）**：phase1 §6.5 cap_gap 每小时自愈对**已 acked 闭环行**的重扫在映射持续缺期间产生无效重复 invalidated ack——语义修正 = invalidated ack **只发一次**（首次 reject 闭环），其后进「等待接入」探测态：每小时仅本地重跑自检+映射，补齐（通过）→ 建 case + 发 ack active（复用 payload_id，契约 R2 `invalidated(offline_cap_gap)→active`），仍缺 → 静默等轮（**不复位 ack_status、不重发 invalidated**）；§6.5 L359「status='rejected' → 重处理」判据收窄至 ack_status ∈ {none, pending}（首次/对账未闭环需重发）或收到 requeue/内容刷新（online_content_gap 重拉覆盖 envelope）的行。online 契约零改动（幂等已兜底）。phase1 §6.4/§6.5 注记随代码单独立项一并落。
> - ⑤ **R-10 input 截断复现边界（offline 零机制、仅注记 + 用例）**：evidence.input ≤8K 为 envelope 约定，截断由 **online 源头执行并带 `input_truncated` 标记**（error run 复现输入 = 截断后实文）——offline **不截断不改写**；error run 对截断输入复现 **pass = verifier 如实判 pass**（na 语义不符——na 是 infra 判不了，此处判定能且如实）；「截断 → 假 auto-fixed」防护在 online 消费侧（detail v1.6：input_truncated 关联 run 不计 K → needs_review reason `input_truncated`，reason 值域 {na, unclean_run, reentry_same_version, **input_truncated**} 3→4）。phase1 §2.1 增注随代码单独立项。ring 2 断言用例见 §11.2 场景 22。
> - ⑥ **R-11 环 2 用例并入**：R-3~R-10 拍板的环 2 双端闭环用例随 v0.7 并入 §11.2（场景 17-22，含 R-3 洪峰 3 版连发差集补齐 / ~~R-4 溢出透出核对~~ / ~~R-5 已见版本读面~~（**v1.23 取消**）/ R-7 批量 requeue 重拉 / R-8 cap_gap 无重复 ack / R-10 截断如实判）；R-6/R-9 online-only 场景随 detail v1.6 归 online §14，不占 §11.2。
> **v0.7.1 修订注记（R-18/R-19，2026-09-07 高危 2 条评审拍板落字、online 已落字 detail v1.7 + solution v3.5.7；offline 零机制改动、仅权威标注字面量修正 + 载体确认）**：
> - ① **R-19（error_type 判据字面量错位 + 漏项修正，本稿落点）**：§6.4 矩阵行 1 字面量由短名 `connect/sse_parse/http` 修正为 executor 真值 `connect_error/sse_parse_error/http_error`（对齐 executor.py L38 RETRYABLE_ERRORS）；矩阵行 2 + §11.1 镜像 + **§2.3 v0.6.2 注② 内嵌 case 级清单**（2026-09-07 全表清点发现遗漏）**补入漏项 `no_usage`/`contract_error`**（executor.py L23/L31 常量，非 RETRYABLE → 直接 na、不重试）——短名错位会让 online 消费侧把既有 case 级错误当「未知 error_type → 默认从严环境级」系统性误触发 unclean_run；修正后 case 级清单与 executor 真值同域，从严兜底回归「仅兜底非常态」（detail §7.6 影响域矩阵 + solution §10.5 判据块同步字面量）。offline 侧仅本文档标注修正、运行机制零改动。
> - ② **R-18（input 明文载体，offline 零改动确认）**：detail v1.7 修「trace_judge_state 只持久 root_input_hash、明文无载体」组装断点——判定态增列 `input_snapshot_clean`（脱敏截断 ≤8K）+ `input_truncated`，消费 step4 root 到达落库、error_cluster 开 cluster/回填自判定态列复制。offline 侧**零新字段**：D19 evidence.input 收 online 组装后的 ≤8K 脱敏截断实文 + input_truncated 标记（§2.3 v0.7 注⑤ 既定边界不变），error run 复现输入 = 截断后实文、pass 如实判不因截断改判（§5.2 对账不受载体调整影响）。

> **v0.7.2 修订注记（R-20/R-22/R-23，2026-09-07 逐问评审拍板落字、online 已落字 detail v1.9 + solution v3.5.9；offline 运行机制零改动，仅收尾语义钉死 + 护栏登记）**：
> - ① **R-22 收尾回填 na 统一钉死 `scheduler_unexecuted`（本稿收尾语义落点）**：收尾回填 na 的 error_type 三路径**同源** = scanner 回收（§6.6）/ orchestrator cancel / run 级超时收尾（§6.3 表行 3）一律 `scheduler_unexecuted`——v0.5 只明确 scanner 路径、cancel/run 级超时收尾路径未显式钉 error_type，存在「同是未跑完回填 na、error_type 因路径而异」的分裂 →「na case 必带 error_type」不变量（§9.3）需**全覆盖收尾路径**。**timeout 不作收尾回填原因**（只作 case 级 na 源 + run 终态，下③）；「在途判定丢失」语义（§6.3）保持：回收瞬间丢失的 case 以 na 呈现、宁 na 不半终态，error_type 同 `scheduler_unexecuted`（环境级）。实现期单测护栏断言 = 收尾回填 na 的 error_type 非空且 = `scheduler_unexecuted`（§11.1 v0.7.2 行）。
> - ② **R-20 复现 run 空答 fail 语义定性 + pre-scan 立项（本稿 verifier 语义落点）**：R-12 空串/纯空白应答 → 保守 fail 修补**保持不动**（core 不改）——复现 run 里 agent 空答（无有效产出）→ fail 的**语义定性 = leakage 空话术复发证据**（agent 对回归输入只回空话术/无内容，与词表命中 fail **同属「未修复」、证据形态不同**——词表命中 = 复发话术撞词，空答 = 复发话术 strip 后无词可撞；两者都说明修复未生效）；**「空答不落 na、落 verifier fail」保持**（na = infra 判不了；空答 = 判定能且判 fail，D2/§7.1 成功语义不因空答改判）。**pre-scan 显式立项（R-12 core 修补实施门禁）**：R-12 修补上线前置 = pre-scan 报告——扫现网 `keyword_*` 断言对**空字段应答的依赖**（确认纯收紧不误伤：是否有靠空串 pass 的正向用例）+ 产「合法空答」清单（业务上允许空答的接口 → 收紧时带豁免白名单上线）；owner = offline 实施，产物入 task 环 0 门禁 + Phase B code_detail。
> - ③ **R-23 timeout 三层语义分界（对表 L99 timeout/cancelled 行消费语义精确化）**：`timeout` 一字多义需拆——**(a) case 级 timeout** = executor 技术失败·可重试源（§6.4 矩阵行 1，na 行 → reason=na、case 级不牵连他 cluster）；**(b) run 终态 timeout** = 半途中断（scanner/run 级超时收尾置），**不直接触发 needs_review**——推送入站按 per-case 终值判 + na 行→needs_review（B-4），「run 中断本身」不产生 review，真正兜底 = claim TTL 超窗回退 open（B-3）；**(c) run 级 timeout 收尾回填的 na 行**（error_type=`scheduler_unexecuted`，环境级）→ 该 run 复现会话被中断污染 → 非纯净 → 关联 pass case 入 unclean_run 批（R-2 环境级牵连）。「run 超时非源」（B-3 落字口径）排除的是 **timeout 事件直接驱动 cluster 状态**（run 挂起 ≠ 该错误已修复/已复发），**不排除**环境级回填污染的**间接**作用 (c)。

### 2.4 版本字面量域（v0.4 新增：R1 收缩为跨端字符串同域）

> 契约锚点：online claim `fix_version` VARCHAR(64) **无格式校验**（solution_detail §8.4 L558/L965）；offline `create_run`/`rerun_run` 现码对 version **强校验 strict semver** `^\d+\.\d+\.\d+$`（§10 明确不改清单曾据此）。两域不重合，且真实被测 agent 版本域含日期标签（solution_detail L830 示例 `agent_version=2026.08.31-r47`）——**两平台各持一套互不兼容的校验 = 确定性静默断链**（v0.4 容错 Z1 / 契约 A2 独立复现）。

**v0.4 裁决（R1 修订，取代 v0.2「两平台同用 semver」）**：跨端版本锚定 = **同一「版本字面量域」内的字符串相等**，不做语法 parse、不要求 semver。

- **共享域定义**：`version` 字面量 = 被测 agent 自报版本（agent_version 上报域），约束仅「非空、≤64、无空白/控制字符」。
- **offline 侧配套**：manual/held_out 的 version 强校验（现码 semver）随本稿**放宽为共享域校验**（`api/runs.py` create_run/rerun_run 校验点，纳入 §10 新增与「明确不改」修订）——否则 agent 真实版本（如 r47 日期标签）在 offline 侧无法登记为信号 → error run 永不建。质量防线改由「同 agent 版本单调」（新增版本低于该 agent 已见最大版本 → 仅告警不拒绝，实施可选）承担，不靠格式。
- **锚定链**：error run.version = 信号 manual run 的 version（域校验值）= online claim fix_version——三者**同一字符串**，推送入站按字符串相等命中；auto-trigger（§5）不做 parse、不比较序。
- **online 侧配套（B 包）**：`fix_version` VARCHAR(64) 存储不动，字段注释改为「与 offline error run.version 共享同一版本字面量域（error 回流场景），不强行 semver」。**（v0.5 扩 + v0.5.1 裁决）§7.5 reentry 版本门控 `agent_version ≥ cluster.fix_version` 的序语义同步处理（B-5）**——字面量域下日期前缀 + `r47`/`r100` 标签串在 `≥` 序比较下会乱序（`r100` < `r47` 字典序）→ reentry 假阴/假阳。**落地语义（v0.5.1 对接核查 S4 裁决）= 等值+日期前缀门控**：先截版本前 10 位（`YYYY.MM.DD`，非该形态则整体作不透明字面量回退等值比较）——日期前缀相等 → 仅**完全等值**放行 reentry、同日不等值保守按「上线中」只 `count+1` 不开新 cluster；日期前缀不等 → 按日期前缀字符串序比较（跨日天然可靠，r 标签不参与跨日比较）。不解析 tag 数值、字面量域裁决保持；代价 = 同日多版本 hotfix 复发漏开（等次日信号或 admin reopen），显式接受。
- **claim fix_version 人工输入风险（v0.5，容错 #7）**：字面量域把断链风险全部押在「人手输入 == agent 自报」——任何偏差（大小写/`v` 前缀/尾随空格/`r47` vs `47`）在字面量域内合法但不等值 → offline 永不建该版本 run → 无推送入站 → 静默 14d TTL reopen。缓解属 online 交互层（风险 17）：claim 表单从 offline 已见该 agent 版本候选提示 + trim/归一化；至少环 2 补 mismatch 用例。

```
被测 agent 发版（版本字面量域 v，§2.4）→ offline 收到携带 (agent, v) 的 manual/held_out run 并到达终态（发版登记信号）
  → maybe_auto_schedule(agent, v)（§5）：有 active error case 且 §5.2 skip 规则放行？
       → 建 error_regression run（§6.1 参数，pinned=true, version=v, status=pending）
  → orchestrator 消费 error 分支（依赖批 1 放开早退闸）：
       前置校验（pinned + case_ids 非空，脏数据防线）
       跳过 _probe_before_run（§6.2）
       _load_run_cases(error 分支) → error suite active error case（断言非空过滤，§6.2 step2）
  → 逐 case：
       （每个 case 复现前 acquire 同 agent 执行并发槽池，§6.2 step4）
       executor.execute_case 复现（error case input 打被测 agent）
         技术失败（error_type）→ 按 RETRYABLE 重试 → 重试尽 → pass_fail='na' + error_type（§7.1）
         成功 → run_assertions 跑该 case 断言（keyword_not_contains(words)）
                命中任一词 → 'fail'（仍 fallback）；不命中 → 'pass'（不再 fallback）
       EvalResult 经 _ensure_case_version 惰性快照 → pass_fail 一次写终值
  → 独立收尾（§6.3，不动共享 _finish 对账）：全 case 有 pass/fail → completed；有任一 na → partial_failed
  → scanner 对 error run 超时回收：短路 score_run_salvage → 替代收尾（timeout + 未完成回填 na，§6.6），不产出 agent_score
  → offline 推送 POST /backflow/regression-results（online 收单同事务内判定）
       case 级 pass_fail：pass→fixed / fail→error 仍在 / na→needs_review（无法判定）
```

---

## 4. 架构决策（批 2 域 D1~D8）

### D1 verifier = 复用断言引擎（裁决 2Q1）

error case 激活时把 `no_fallback_config.words` 固化为一条 `keyword_not_contains` 结构化断言写入 `TestCase.assertions`；error run 对复现输出执行该断言 → pass/fail。理由：`KeywordNotContainsOp`（输出含词表任一词即 fail）与 no_fallback 语义完全同构且已在 gq 金丝雀场景验证；词表随 payload 固化 → 断言即固化快照，天然满足「改词表不 retroactive」。
**语义收窄**：批 1 §5.1「error case 三列允许空」收窄为——`expected`/`metrics` 恒 NULL；`assertions` 由激活器写入恰一条 no_fallback 断言（激活成功后恒非空）。nullable 保留作 fail-safe；403 写守卫防激活后篡改。见 §7.3。

### D2 复现失败 = 无终值 na（裁决 2Q2，v0.3 na 定义统一）

executor 技术失败（未拿到可判输出）→ case 不落 pass/fail，`pass_fail='na'` + error_type；run 计 `na_case`；online 对 na case 走「回归 infra-error 无法判定 → needs_review」。
**na 定义（v0.3 逻辑复核 W1 统一；源集合 v0.5.2 对齐 §6.4 五源矩阵）**：na = **未取得可判终值**——run 判定内可达源 = executor 技术失败（可重试/非可重试，error_type 非空、重试尽后）∨ 调度层未执行（circuit_open/interface_disabled）∨ 调度回收 scheduler_unexecuted；判定素材缺失（空断言/断言形态被篡改，§7.5）源经加载过滤前置、v1 仅防御兜底。各源固定 error_type 值，终值语义单一（「未判成」），根因走 error_type 区分。
**分层守卫**：断言层异常保守判 fail——`KeywordNotContainsOp` 对 path 缺失/非文本返回 False（不静默 pass）：输出结构都异常了，谈不上修复生效。

### D3 版本变更自动触发（裁决 2Q3 + R1 v0.4 修订为版本字面量域）

- 触发信号 = 被测 agent 发版经 offline 评测登记：出现携带新 `(agent, version)` 的 manual/held_out run。凡该 agent 有 active error case 且 `(agent, version)` 尚无 error run → 幂等自动补建。
- **版本断链解除（R1 v0.4 修订，取代 v0.2「两平台同用 semver」）**：被测 agent 发版/部署版本与 online claim fix_version 共享**同一版本字面量域**（§2.4）——error_regression run.version = 信号 manual run 的 version = 被测 agent 声称修复版本 = online claim fix_version，字符串相等命中推送入站。v0.2 曾以 R1 撤销批 1 §7.3 登记项 D-14（「version 非 semver 放开」，基于示例 `2026.08.31-r47`）：该撤销的前提「manual semver 强校验值即版本锚」不成立——真实 agent 版本域（如 r47 日期标签）不在 semver 内，manual 强校验把承载域外版本**挡在信号面外 → error run 永不建 → 静默断链**（v0.4 Z1/A2）。v0.4 修正：manual/held_out version 校验放宽为**共享域校验**（§2.4/§10），域内原样承载；D-14 以「共享域」形态**恢复**（批 1 撤销记录随之修正，§10 跨文档同步项 ③）。
- 诚实边界：依赖被测 agent 发版经 offline 评测；若完全不经过，该版本不自动回归 → online 无推送入站（无对应 `verify_run_record`）→ **claim TTL 超窗回退 open（online 既有兜底，非 needs_review，v0.4 A3 校准）**；显式补建入口留二期（§5.4）。

### D4 单 fix 版回归（裁决 2Q4）

每 error case 一次复现打在 agent 当前（fix）版本：命中固化词表 → fail，不命中 → pass。不做 trigger/fix 双版本对照（契约以 fix_version run 为锚、经推送回传判定；error 历史由 payload 快照保证）。

### D5 error run 不经 SCORING / scoring 态

error run 状态机 `pending → running → {completed, partial_failed}`，不出现 scoring/scoring_failed 态，不产 `agent_score`。三条防线：① orchestrator error 分支不触发 score_run（§6.2）；② **独立收尾**不动共享 `_finish` 的 SCORING/对账逻辑（§6.3，评审 B4）；③ **scanner/salvage 短路**（§6.6，评审 B3）。

### D6 CaseVersion 维持惰性，不预建

error case 激活不预建 CaseVersion；error run 落 result 走既有 `_ensure_case_version` 惰性快照即满足不变式（批 1 §5.1：content 不可变 403 写守卫 + 运行期惰性快照 = 信封原文）。预建零增益、且避免 `_content_hash` 口径一致风险。与批 1 裁定 Q3 衔接：延批 2 的结论 = **维持惰性**，helper 抽取不需要。

### D7 error run 免占位 + 执行期并发闸（裁决 R2，v0.2 D7 → v0.3 排他窗 → v0.4 共享槽池）

- **免占位**：创建不经公开 create_run，不占 `max_active_runs`；**活跃计数扫描排除 error_regression**（防 error run 堵 manual，评审 B1，§9.2）。
- **执行期并发闸（v0.4 语义：共享 per-agent 执行并发槽池，废弃 v0.3 run 级排他窗）**：现码同 agent 建单已被 `_MUTEX_STATUS` 计数 409 串行；error run 被计数排除后是**唯一可与同 agent 的 manual/held_out 叠加的 run 类型**。为防 error 复现叠加放大对被测 agent 的并发负载：error run 的**逐 case 复现执行与 manual/held_out 共享同 agent 的执行并发槽池**（= 既有 per-agent 执行并发限制所在信号量，如 `per_agent_concurrency=3` 同一池；实施对表现码 limiter 作用域）——error 复现至多占 ≤1 路，不叠加出超限路数，互斥由槽池自动保证，**manual/held_out 侧零改动**。v0.3 run 级排他窗（error 整 run 持窗、manual 见窗让位）废弃：该粒度让 error 长 run 整段阻塞同 agent 全部 manual（3 路全让），manual 常跑又无让位承诺 → 双向饿死与优先级纠缠（v0.4 I4/Z2 修入）。不再宣称"agent_mutex 包执行期"（事实错误）；也**不宣称「同 agent 全局 ≤1 路」**（manual 多路 + error ≤1 路并存于槽池，见 §6.2）。
- 理由：契约"免互斥槽"是**名额维度**豁免（回归不挤占正常评测额度、不被其占位卡死）；执行期并发由**共享槽池**保障（error 复现不叠加放大负载），两个独立机制落地。

### D8 error run 执行前置校验（评审 A3 脏数据防线）

error 分支放开早退闸后，非内部创建器产物（DB 直插、pinned=false、case_ids=null）的 error_regression run 也可能被消费。执行前置校验：`pinned=true AND case_ids 显式非空`，否则跳过并告警（§6.2）。

---

## 5. run 创建触发（自动，D3）

### 5.1 信号与登记模型

信号 = 到达**终态**（非 pending/running）的 `trigger_type ∈ {manual, held_out}` run（携带 `agent_id + version`，version 取**版本字面量域** §2.4 校验值，不要求 semver）= 被测 agent 完成一次该版本评测 = 发版登记（终态前置见 §5.2；「新落库」仅指 run 出现，触发只在终态后挂接，防建即 cancel 的空信号）。held_out 复测同版本、rerun 同版本均命中幂等不重复建单；error_regression run 自身不作信号（防递归）。判定量 `(agent, version)` 映射唯一 error run。

### 5.2 触发条件与幂等（v0.3 修入：信号终态前置 + skip 规则完备化）

`maybe_auto_schedule(agent_id, version)` 在**信号 run 到达终态后**调用（见 §5.3 挂接点）：

```
if 该 agent 无 error suite / 无 active error case → return
if runnable_case_count(该 error suite) == 0 → return   # v0.5 C1 创建前门禁：无「断言非空 active」case（存量 backfill 未清零）→ 不建空 run、latest 保持 None；补偿对账每周期重试，backfill 清零后自然建出。防「全 case 被加载过滤剔空 → vacuous completed」占死 (agent,version) 可判终态位（v0.5 容错 #6/逻辑 #3）
latest = 该 (agent, version) 最近一条 error_regression run（按创建序，实现在取 agent 行锁后读取，见下「并发防重」）
signal = 本次调用携带的信号 manual/held_out run（§5.3）
if latest is not None and latest.trigger_signal_id == signal.id → return  # 该信号已消费：曾据它建过 error run；补偿对账复用同锚不重复动作（v0.4 K1/B1 吸收态，见 §5.3）
if latest is None → 创建（§6.1，记 trigger_signal_id = signal.id）
elif latest.status ∈ {pending, running} → return            # 已建未跑完，防重
elif latest.status ∈ {completed, partial_failed} → return    # 已有可判终态，不重复回归（技术 na 重跑 = 二期 §12 开放项）
elif latest.status ∈ {timeout, cancelled} → 创建              # 坏终态（上次没判成）：仅当本次为「新信号」（未被 latest 消费）时给一次重建机会；重建后 trigger_signal_id = signal.id → 后续同锚不再触发
```

- **信号前置（v0.3 安全方向）**：只有**到达终态**（非 pending/running）的 manual/held_out run 才作发版信号——建即 cancel 的空信号不算发版登记，收敛触发面。
- **创建前门禁（v0.5 C1）**：runnable case = error suite 下 `status='active' ∧ assertions 非空且形态为 keyword_not_contains`（与 §6.2 step2 执行加载**同一谓词**）。该数为 0 → 不建 run、latest 保持 None——批 1→批 2 间隙存量 `assertions=None` 未 backfill 清零时，首个发版信号不会建出「实跑集为空」的空 run，杜绝 vacuous `completed`（0 判 0 na）占死 (agent,version) 可判终态位 → online 推送入站命中空 run 零结果行 → 缺行≠na 观察无法推进 → TTL reopen 死锁（v0.5 容错 #6 时序洞 + 逻辑 #3）。清零后由 §5.3 补偿对账（同锚每周期重试）自然建出含存量 case 的 run——存量 case 在该 fix_version 即可被判定，不必等下一新版本。防御兜底：门禁通过后执行期实跑集仍为空（极窄：runnable case 建后被作废）→ 不落 completed，落 cancelled 并告警（§6.3）。
- **幂等规则变更（v0.3 W4 → v0.4 consumed 锚补全）**：v0.2 skip 集 {pending,running,completed} 缺 partial_failed/timeout/cancelled——partial_failed 会落回「全过→创建」产生重复可判 run；timeout/cancelled 又永不补。v0.3 改「坏终态后同版本新信号允许重建」，但**未约束「同一信号」与「不同信号」**：§5.3 补偿对账周期复用同一最新 manual 锚 → 每次对账都对坏终态重建 → **无吸收态自动重跑循环**（v0.4 K1 容错 / B1 逻辑 双重独立复现）。v0.4 补 `trigger_signal_id`（error run 建单记下所据信号，§6.1/§10 新增字段）：**每个信号最多产生一次动作**——已消费信号（latest.trigger_signal_id == 本次信号 id）直接 return；重建只可能由「比上次消费更新」的信号触发，补偿对账用同一锚反复调用被吸收态拦截 → 循环收敛。可判终态（completed/partial_failed）后新信号仍不重复回归（与 §1.2 一致）。
- **partial_failed 的素材缺失钉死解除（v0.4 I2 修入）**：v0.3 的 partial_failed 恒 return 会把「仅素材缺失 na」的 run 判为终态、永不重建 → assertion 补齐后该 case 仍永久无判。v0.4 将素材缺失 case **移出 run 判定**（加载过滤 §6.2/§7.5）：partial_failed 内 na 只剩 executor 技术失败/调度未执行（真故障），不再有「等素材」的假终态；素材缺失 case 未落 na、不进 needs_review，assertion 补齐后由 backfill 对账清零 + **C1 门禁放行、补偿对账同锚即建**（§5.2/§5.3，不依赖下次发版信号）。
- **晚到 active case 覆盖（W4 边界声明）**：error run 创建时 case_ids = 当时 active 全集；此后新激活的 error case 不在该 run 内 → online 对该 case 按无终值处理（观察中断，claim TTL 超窗回退 open，非 needs_review），到该 agent **下次发版信号自然纳入**。v1 不为「晚到单 case」单独触发（防建单风暴），此边界显式声明。
- **洪峰收敛（v0.3 安全方向）**：同一 agent 同时最多 1 条活跃（pending/running）error run（含跨 version）——超出则等先建 run 终态后由后续信号触发；配额值实施定，防「一次多版本发版瞬间建 N 个复现 run」放大对被测 agent 的负载。**创建侧落点（v0.5.2 补全）**：该收敛**不在**本 skip 逻辑内（其按 `(agent, version)` 键、看不到其它 version 的活跃 run）——校验须放内部创建器 `_create_error_regression_run`：**取 agent 行锁后、插建前**查「该 agent 活跃（pending/running）error_regression run 数 ≥ 配额 → 不建、返回」，待先建 run 终态后由后续信号/补偿对账再试；执行并发仍由 §6.2 共享槽池兜底，两机制独立。
- **并发防重（v0.5 锁序修正）**：两路信号并发首次触发同 `(agent, version)`——**读 latest/查重必须发生在取 agent 行锁之后**（`SELECT ... FOR UPDATE` 后重读；伪码第一行「读 latest」仅为意图描述，实现时后移至锁内）——否则两路并发都读到 latest=None 再各自插建，双建（v0.5 逻辑 a/d：伪码字面顺序「先读后锁」与并发防重段「锁内查重」不自洽，以取锁后重读为准）。不加 runs 唯一索引（MySQL 无法表达"仅 error_regression 的 (agent,version) 唯一"）。写入路径极低频，行锁足够。**锁序约定**：任何 error-run 相关路径**先取 agent 行锁，且持 agent 行锁期间不得再取同 agent 的 eval_run 行锁做更新**；`maybe_auto_schedule` 从信号收尾触发须在 `_finish` 事务**提交后**调用（fire-and-forget），不持 eval_run 行锁时取 agent 行锁（§5.3），规避两锁反向交叉。

### 5.3 代码落点

`maybe_auto_schedule` 放 `runner/`（执行侧纯服务函数）。挂接点 = **信号 run 到达终态、`_finish` 事务提交后**（fire-and-forget，失败仅记日志）——**不在持 eval_run 行锁的事务内调用**：maybe_auto_schedule 建 run 需取 agent 行锁，与 `_finish` 持 eval_run 行锁构成反向锁序（§5.2 并发防重锁序约定），提交后调用规避死锁（v0.5 逻辑 a/d）。执行期并发由 §6.2 闸保证，触发即建不构成并发风险（评审 B M2 时序洞由 R2 闸兜底）。

**低频补偿对账（v0.3→v0.4 语义 = 历史基线——单最新锚逻辑已被下方 v0.7 R-3 块「版本差集逐版补建」升级取代，实施以 R-3 块为准，H7-7 双规范消除）**：原语义为低频任务（并入 scanner 周期，量级小）——对每个有 active error case 的 agent，以**最新到达终态的 manual/held_out run** 为锚调用一次 `maybe_auto_schedule`（锚取法保留 = R-3 差集每缺版取其最近终态 manual/held_out run）。兜底场景：① 触发事件进程崩溃丢失；② 兼任存量 backfill 未清零对账（§7.3，直到断言全非空）；③ 幂等键重复评估无副作用。

**v0.4 边界（K1/B1 双重确认修入）**：对账**只负责补「从未建过 run」的首建缺口**；重建坏终态（timeout/cancelled）run 只可能由「新信号」（比 latest.trigger_signal_id 更新）触发——对账周期若锚仍是已被 latest 消费的信号，§5.2 吸收态（`trigger_signal_id == signal.id → return`）直接拦截，**同一坏 run 不可能被对账反复重建**（无信号时永远吸收；新信号重建一次后 trigger_signal_id 前移，再次吸收）。对账与「timeout→重建」不再复合成无退避自动重跑。触发缺口从「静默且永久」降为「至多一个补偿周期」。对账漏触发/不一致须有日志/告警 key（v0.3 逻辑 S2）。

**v0.5 C1 门禁与对账衔接**：对账锚未消费 + 该 agent runnable=0（backfill 未清零）→ 不建 run 且**不被吸收态拦截**（无 run 即无 trigger_signal_id，每次对账周期重试）——backfill 清零后同一锚建出 run，即门禁把「等待清零」转化为对账周期内的自然重试，不产生错误 run、不占可判终态位。清零窗口内每周期一次空查（runnable 计数）成本可忽略。

**v0.7 R-3 对账版本差集补齐（2026-09-07 评审拍板，offline 机制改动）**：上「低频补偿对账」以**最新**终态 manual/held_out run 为单锚 → 只补该最新版本的「从未建 run」缺口；**洪峰丢版本洞**：活跃闸=1（§5.2 创建器校验）期间连发的中间版本（v2 于 v1 running 时触发被拒建、v3 后 latest 前移）**其版本号从未建过 run、对账差集无此维度 → 永久丢**——claim fix_version 落在该版本 → 无推送入站 → 静默 14d TTL；且 R-1 claim_k 跨版本序列依赖「每个 fix_version 版本都有 run」，丢版本使判据整版空转。**修订 = 对账从「单最新锚」升级为「版本差集逐版补建」**：

- **差集定义（按 agent 计算）**：`该 agent 已到终态的 manual/held_out run 的 distinct version`（**信号全集**，按 run.trigger_type ∈ {manual, held_out} 取，显式排除 error_regression run 自身——error run 不作信号、防自指循环）`− 该 agent 已有 error run 的 version`（含 timeout/cancelled 等坏终态版本号：**视为有 run**，重建只走 §5.2 新信号逻辑，防差集补建与坏终态重建双路径冲突）。
- **补建规则（落字前提，实施核对）**：对差集内每个缺失版本**逐个补建**——每个缺版以「该版最近到达终态的 manual/held_out run」为锚记 `trigger_signal_id` 调 `maybe_auto_schedule`；**遵守活跃闸（§5.2）**：一周期内该 agent 至多补 1 个（补建后该版本即进入「已有 run」，下一周期差集自然前移，执行仍串行，总耗时与「解除闸排队」等价）；§5.2 吸收态（`trigger_signal_id == signal.id → return`）继续拦截已消费版本，差集与吸收态正交：吸收态防「同一版本反复建」，差集管「从未建过的版本」。
- **保留语义**：仍只负责补「从未建过 run」的首建缺口；backfill 清零对账（§7.3）继续由差集内版本 run 自然覆盖存量 active case；§5.2 skip 规则 / §6.2 共享槽池 / 并发防重锁序 / cap 截断**全不动**（改动面 = §5.3 对账逻辑 + §5.2 措辞：创建器拒建的「待先建 run 终态后由后续信号/补偿对账再试」落点从此 = 差集逐版补齐）。
- **环 2**：洪峰 3 版连发 → 3 run 最终全建出、中间版 claim 可经推送入站判定（§11.2 场景 17）。online 语义零改动（推送入站 B-1(b) 多 run 选取序已覆盖多 run）。



自动信号漏触发时缺人工兜底。二期在 admin 面加「对该 agent 强制发版回归」（复用内部创建器）+ error run 终态重跑通道。v1 靠无推送入站（online 侧无对应 `verify_run_record`）→ **claim TTL 超窗回退 open** 兜底（solution_detail §10.1，非 needs_review，v0.4 A3），不扩 UI。

---

## 6. error_regression run 执行语义（D5/D7/D8）

### 6.1 创建参数规格（内部创建器 `_create_error_regression_run`）

| 字段 | 值 | 说明 |
|---|---|---|
| `agent_id` | 信号 run 的 agent | — |
| `suite_id` | 该 agent 的 error suite（`agent_id + is_error_suite=true`，批 1 依赖） | error case 全集中此 suite，run.suite_id 无二义 |
| `case_ids` | 该 error suite 下 `status='active' AND case_type IS NOT NULL` 的 case **newest-active-first 取至 cap**（溢出最老侧，§6.1 注 1；**显式非空**） | 批 1 §8.2 依赖：`[]`/`null` 一并钉死，反查 JSON_CONTAINS。§5.2 门禁查「该集 ≥1 runnable」（批 2 激活 case 恒非空 → 必在 newest 侧窗口内，故门禁基于全集即可，见 §5.2 C1 bullet） |
| `version` | 信号 run 的 version（版本字面量域 §2.4，= fix_version） | manual/held_out 的 version 校验随本稿放宽为共享域校验（§2.4/§10）；error run 原样承载 agent 版本字符串，推送入站按相等命中（v0.4 Z1/A2） |
| `trigger_signal_id` | 触发本 run 的信号 manual/held_out run 的 id（v0.4 新增 consumed 锚，§5.2/§5.3） | 补偿对账吸收态判定依据；重建 run 覆盖为最新信号 id |
| `trigger_type` | `'error_regression'` | 公开 create_run 白名单保持 manual/held_out 不变 |
| `pinned` | `True` | cleanup 豁免 + D8 前置校验标识 |
| `status` | `'pending'` | — |
| `lease_until` | **非空初始租约**（v0.3 安全方向） | 内部创建器写入初始 lease；pending 卡死可被 scanner 租约回收（现码回收前提 lease_until not null，§6.6） |
| 执行预算 | **error-run 专用 run_config**（v0.3 容错 4/性能 1） | 不落缺省 `case_timeout=120/max_retries=1`——error case 原始故障常是慢场景，修复版复现需更大预算（按 agent adapter 能力上限冻结，如 case_timeout=600/max_retries=2，实施依 adapter 实际上限定）；error run 的 hard_deadline = 总预算含闸等待估值（§6.2） |
| 名额 | 不检查、不计数 `_max_active_runs` | §9.2 另在计数扫描排除 error_regression |

表后注（**v0.4 容量双锁 → v0.5 R3 裁决重写：分母锁降格为守卫驻留声明，run-cap 锁保留并补排序/反推/诚实标记**——全集无界 × 全量复现 = 确定性容量炸弹，90d 累积 1800-2700+ case/agent 必撞 hard_deadline → 批量 na；第 4 轮四方向独立互证「分母治理跨端闭环不存在」，详见注 2）：
1. **run 级 case 上限（配置化，从执行预算反推）**：单 error run case 数上限由 error-run 专用执行预算反推——`cap ≈ H_run / (case 平均复现时长 × 尾因子)`（示例：H_run=2h、avg 10s/case、尾因子 ×2 → cap≈360；实施按 agent adapter 实际上限与历史 case 时长定；v0.4「默认 1000」为拍脑袋值，需按上式核，负载 D-1）。
   - **截断排序 = newest-active 优先（v0.5 定）**：cap 截断按 case 激活序**倒序**（最新激活必进 run）。最新激活 = 最新错误 = fix claim 对象——倒序截断保证 claim 盯着的 case 必进，**消除「最新错误排 1000 名外 → 该版全部新 claim 集体 TTL」的洞**（v0.4 负载 #1/逻辑 BLOCKING：截断顺序未定义）。溢出 = 最老的一批。
   - **溢出 = 窗口外守卫欠测（诚实声明）**：溢出 case 不进 run、保持 active、**不产生结果行/不落 na/不触发重建**。老哨兵**无 claim 盯着**（online 只在错误再现/reentry 时 claim，而 reentry 产新 payload → 新 case，不复活老 case）→ 溢出欠测**不产生 TTL 链**，代价 = 窗口外老错误的回归验证在该版本放弃（该错误再现时以 reentry 新 case 进 run，见风险 15）。同 fix_version 溢出 case online 无结果行（观察中断）→ claim TTL 超窗回退 open → 下轮 claim 重试（接受，宁欠不炸）。
   - **截断诚实标记**：run 记 `case_truncated`（截断发生）+ 溢出计数，经推送载荷暴露（§2.3）——「该版全判」与「窗口截断欠测」可区分，不伪装。**（v0.7 R-4：溢出计数 → 溢出 case 列表字段透出 —— **v1.23 取消**，推送为全量对账、不存在「缺行→轮询」与只读面，§2.3/§9.3）**
2. **分母治理 v1 裁决（R3 = 守卫驻留 + 诚实欠测）**：v0.4「分母随 cluster 终态回写闭环收敛（fixed/invalidate 出 active）」在 v1 契约下**不成立**——online invalidate 仅限 assembled/draft（active ack 后不可能，批 1 403 写守卫）；v1 无 online→offline deactivate 通道（只有 offline 拉 ack + offline 主动推送结果，容错 #2/契约 #2/负载 #2/逻辑 #4 四方向独立互证）；error case active 集**只增不减**。v1 裁决：**不引入跨端 deactivate**，接受分母单调增长为**守卫驻留语义**——error suite = 该 agent 历史错误簇的回归守卫队列，每版 run 以 newest-active-first 窗口覆盖（注 1），回归成本 = 窗口固定而非分母全量。**代价诚实入风险 15**：窗口外老错误不逐版验证，回归检测依赖错误再现触发的 reentry（新 case 必进 run 窗口）。跨端 case 归档/去重（online cluster 终态 → offline case 出 active）列**二期**（B 包 B-9，v1 不落跨端新消息）。
3. **DB 增长声明（v0.5）**：守卫驻留下 error case/run/result 均单调（result 行受注 1 窗口封顶，case/run 行 = 历史错误簇 × 发版，90d 量级 1800-2700+ case/agent 见风险 7）。v1 保证运行不受影响（case_ids 窗口化、JSON_CONTAINS 按 run 反查），归档/截断策略列二期（§12 开放项，含 run_case 索引表建议——JSON_CONTAINS 不可索引，结果行膨胀后按 run 检索退化，v0.4 负载 #5）。

### 6.2 orchestrator error 分支（依赖批 1 放开早退闸；D8 前置校验 + R2 并发闸）

`runner/orchestrator.py` `_run`（依赖批 1 落地后含 trigger 分发与早退闸）对 `trigger_type='error_regression'` 放开为执行分支：

1. **D8 前置校验**：`pinned=true AND case_ids 显式非空 AND suite_id 为该 agent 的 error suite`（**agent 归属一致**，v0.3 安全方向；防 DB 直插脏行真打被测 agent），不满足 → 跳过并告警。校验失败路径纳入 §11 单测。
2. `_load_run_cases` 走批 1 §7.2 error 分支条件（suite_id + active + case_type IS NOT NULL + is_held_out=False，case_ids 子集叠加）——**依赖批 1 落地**；**叠加断言非空过滤（v0.4 I2 修入）**：`assertions IS NOT NULL 且形态为 keyword_not_contains` 的 case 才进本 run——素材缺失（批 1→批 2 间隙存量未 backfill）case **不进 run**、不落 na、由 §5.3/§7.3 backfill 对账清零；清零后 §5.2 C1 门禁放行、补偿对账同锚即建（不依赖下次发版信号）。空断言 case 不再可能成为 run 内 na 源（na 源收敛为 executor 技术失败/调度未执行）。
3. **跳过 `_probe_before_run`**：error case 复现自带错误处理与重试；探活会额外对被测 agent 多一次真实调用（成本/副作用），对 error 判定零增益（评审 B M3）。
4. **执行期 agent 并发闸（R2，v0.4 语义：共享 per-agent 执行并发槽池，取代 v0.3 run 级排他窗）**：
   - **闸语义（v0.4 修正）**：error run 逐 case 的复现执行与 manual/held_out **共享同 agent 的执行并发槽池**——error case 执行前 acquire 同 agent 执行并发名额（与 manual 相同的 per-agent 并发限制，实施对表 executor limiter 作用域与计数口径）；manual run 之间仍由既有 `_MUTEX_STATUS` 建单计数串行（现状不变）；同 agent 的 manual/held_out 与 error 复现**同时执行并发总数 ≤ 槽池上限**，error 复现至多占 ≤1 路、不叠加出超限路数。v0.3 run 级排他窗废弃（error 整 run 持窗会整段阻塞 manual 3 路，manual 常跑又无让位承诺 → 双向饿死，v0.4 I4/Z2）。
   - **manual/held_out 侧零改动（v0.4 简化）**：互斥由槽池自动保证，manual 执行入口**无需**检查 error 排他窗——撤销 v0.3「manual/held_out 同接入排他窗」要求（§10 清单同步撤）。manual run 内部 `per_agent_concurrency=3` 既有并发形态不变。
   - **排队与饿死（v0.3 容错 4 承接 + v0.4 定界 + v0.5 心跳判据）**：槽位被 manual 占满时 error case 入队（与 manual 同队列 FIFO，槽释放即进）；error case 自带 per-case timeout；error run 的 **hard_deadline 估算含排队等待估值**，且「已启动且推进心跳未停」的 running error run 豁免回收——**v0.5 统一判据 = 推进心跳/lease 刷新**（§6.6：逐 case 续租 + 排队期心跳，替代 v0.4「豁免 hard_deadline」二选一）——running 排队期间 lease 持续刷新即视为活，防合法排队活 run 被误收成 timeout+na；同时 lease 过期的僵尸 running（进程崩溃）被回收，不永久占 §5.2 skip 集与洪峰配额（逻辑 #2 判别物缺失补齐）。error 独占 ≤1 路 → 不 starve manual；error 排队等待无上界的风险由 hard_deadline 估算/心跳豁免吸收（v0.4 I4：等待期不产生「饿死 pending」状态机，见 §6.6 pending 回收）。
   - **前提标注**：槽池以进程内共享信号量实现即可；若多进程 worker，跨进程并发需在 agent 行做轻量执行标记/乐观校验（实施前核对 worker 模型；默认单进程）。
5. 逐 case 判定（§7）；落 `EvalResult` 走 `_ensure_case_version` 惰性快照（§8）+ **pass_fail 一次写终值**（复用普通 run 的 `_save_result` 机制但**去掉"先写 pass 占位"**——普通 `_save_result` 先写 pass 占位再由 scorer 覆盖，error 分支不走 scorer，直接一次写终值）。**error 分支落库不得走 `_save_result` 的默认 `pass_fail='error'` 路径**（v0.3 容错 B1，见 §6.4 调度层未执行类）。
6. **绕过 scorer/judge 一切入口**：不触发 SCORING、不建 judge 任务、不进 `_enabled_semantic_dims` 全维误判路径（§0 修正 1）。

### 6.3 独立收尾（D5，不动共享 _finish；评审 B4/M4）

error 分支**不复用共享 `_finish`**（其 L541-545 对账回填 `pass_fail='error'`、全 na 落 completed、按 'error' 计 error_case 等逻辑均与 error 语义冲突）。新增独立收尾 `_finish_error_regression`：

| 条件 | run.status | 落库 |
|---|---|---|
| 全部 case 有 pass/fail 终值（0 na） | `completed` | — |
| 存在 ≥1 na case | `partial_failed` | — |
| 未跑完被取消/超时（cancel/scanner，§6.6） | `timeout`/`cancelled`（沿用） | 已完成 case 保留 pass/fail 终值；**未完成 case 回填 `pass_fail='na'`**（不写 'error'，M4） |
| **终态 commit 后**（上三种终态共同） | — | **异步推送** `POST /backflow/regression-results`（fire-and-forget：超时 5s、重试 3 次、全败记 error **不阻塞收尾**；载荷与拒单规则见 §9.3，v1.23 契约反转） |

- **推送时机（v1.23）**：**三种终态均推送**——终态 commit 之后（**不在收尾事务内**）异步发起；online 侧按 `run_status` + per-case 终值判定（§2.3 对表），run 级中断不整单丢弃。
- 计数：`na_case` = pass_fail=='na' 计数；`error_case` = =='error' 计数（error run 恒 0）；`agent_score` 恒 NULL。
- `completed` 仅当 0 na（评审 B4：全 na 不得落 completed——那会误导 online 认为可判）。
- **实跑口径（评审 A2 → v0.4 扩展）**：run 汇总（total/pass/fail/na_case）以**实跑集**计——`case_ids` 在创建时固化，若个别 case 致实跑集 < case_ids：① run pending/running 期间被作废（批 1 invalidate 路径，v1 不变式下极窄窗）；② **空断言被加载过滤**（v0.4 I2，批 1→批 2 间隙存量未 backfill 的 case）——推送载荷对该 case 无结果行（而非伪值），online 按无终值处理（§2.3 缺行≠na），避免静默失配。过滤出的空断言 case 由 backfill 对账清零，下轮信号自然纳入。
- **实跑集为空防御（v0.5 C1 兜底，容错 #6/逻辑 #3）**：§5.2 门禁保证建 run 时 ≥1 runnable；若执行时实跑集仍为空（极窄：runnable case 建后被作废/全部致实跑集 < case_ids 至 0）→ **不落 `completed`**（空集对「全 case 有 pass/fail」恒真，vacuously completed 会伪装可判终态占死 (agent,version)），落 `cancelled`（坏终态，§5.2 允许新信号重建）+ 告警。此路径理论上被门禁消除，仅防御竞态。
- **事务约束（v0.3 容错 5）**：na 回填（含未完成回填、§7.5 素材缺失 na）与 run 终态推进**同事务、同 db session 直插 `EvalResult`**——沿用共享 `_finish` L536-546 注释既定模式：持 eval_run 行 FOR UPDATE 锁时经 `_save_result`（自开 session）插 eval_result 会因 FK S/X 锁互锁 1205，必须同事务直插；`_ensure_case_version` 若自开 session，收尾路径不得经其绕回开新事务（先取 case_version 再同事务插）。`uk_result` 唯一约束作幂等兜底。
- **终态写守卫（v0.5，容错 #5）**：run 已入终态（completed/partial_failed/timeout/cancelled）后，EvalResult 写入一律**拒绝**（uk_result 撞键 = 丢弃迟到行 + 记日志）——error 分支长跑与 scanner 回收（§6.6）竞态下，防「scanner 置 timeout 后 orchestrator 仍逐 case 写迟到 pass/fail 行」把 run 弄成半终态（timeout + 事后 pass 行并存 → online 两次读取不一致）。保证终态后结果集冻结：推送载荷在 run 终态后一次性产出，online 侧该 run 数据集稳定（原「list runs → results」两次拉取的中间态不复存在）。**收尾事务自洽（v0.5.2 补全）**：独立收尾（§6.3 表）/scanner 替代收尾（§6.6）本身要置终态并回填 na——收尾须**同事务内先插 na 行、后置 run 终态**（§6.3 事务约束同事务直插模式）；本守卫只拦「已提交终态之后」的迟到写（如 orchestrator 对已置 timeout run 的迟到 pass/fail 行），实现按已提交终态判定，不得拦同事务自身回填。**在途判定丢失（v0.5.2 显式声明）**：回收时点恰在「executor 已判出终值、EvalResult 未落库」窗内的 case，scanner 已将其回填 na → orchestrator 迟到真实行被本守卫丢弃 → 该 case 以 na 呈现（可能实为 pass/fail）。取舍 = 宁 na 不半终态（保 online 读一致性），代价 = 个案假 needs_review（下版重测收敛）；`scheduler_unexecuted` 语义相应放宽为「该版该 case 未获可判终值（含回收瞬间丢失、非必从未执行）」，诚实标记口径同步（§6.4/§6.6）。
- **依赖不变式（v0.3 容错 7）**：**v1 active error case 不可 invalidate**——批 1 对 error case 全写端点 403 + online 人工 invalidate 仅限 assembled/draft（§7.6），「run 执行中 case 被作废」只在 ack-400 case_created 极窄窗可发生。此不变式为实跑口径前提，显式声明；未来放开 error case invalidate 时须重审视（实跑集语义 + 补偿触发）。

> **v0.7.2 R-22 收尾回填统一（2026-09-07 落字）**：上表行 3「未完成 case 回填 na」的 error_type **钉死 = `scheduler_unexecuted`**——收尾三路径（scanner 回收 §6.6 / orchestrator cancel / 本表 run 级超时收尾）回填 na **同源同 error_type**，杜绝「同是未跑完回填、error_type 因路径而异」使 online 分组聚合分裂；「na case 必带 error_type」不变量（§9.3）**全覆盖收尾路径**。**timeout 不作收尾回填原因**——timeout 只作 case 级 na 源（§6.4 行 1）+ run 终态（表行 3 置 timeout），不作为回填 na 的 error_type（R-23 三层分界见 §2.3 v0.7.2 注③）；本段「在途判定丢失」语义不变（回收丢失 case 落 scheduler_unexecuted、宁 na 不半终态）。实现期单测护栏断言收尾回填 na error_type 非空且 = `scheduler_unexecuted`（§11.1 v0.7.2 行）。offline 运行机制零改动（本稿为待实施规格的语义化钉死，实现按此落 error_type）。

### 6.4 na 分类矩阵（v0.3 容错 B1 + 逻辑 W1 修入：na 五源矩阵，终值语义单一 + error_type 固定值）

error run 内 `pass_fail='na'` 的**五源矩阵**（v0.3 根因三层[executor 技术失败/判定素材缺失/调度层未执行] → v0.4 executor 拆可重试/非可重试 → **v0.5 新增调度回收源 scheduler_unexecuted**，逻辑 #5/容错 #5——scanner 回收回填 na 必须落入固定 error_type，否则违「na case 必带 error_type」（§9.3）、online 分组 key 不了；行 4 素材缺失为防御兜底、行 1/2/3/5 为 v1 run 判定内可达源），每类固定 `error_type`。**error_type 跨端口径（v0.4 I3/S2 修入）**：error_type 进**推送载荷**（machine-to-machine，na case per-case 必带，§9.3），online 以 error_type 分组聚合 needs_review 并转 reason 展示——**不是人类读面泄漏**（error_type 是分类枚举非实文；实文隔离仍按 §9.4）；「未判成」终值语义不分裂。

| 源 | 触发（`error_type`） | 处置 | 影响域（v0.6.2 R-2 权威标注，online 消费归类） |
|---|---|---|
| **executor 技术失败·可重试** | `RETRYABLE_ERRORS`：timeout/connect_error/sse_parse_error/http_error/no_done（**v0.7.1 R-19：字面量对齐 executor.py L38 真值**，connect/sse_parse/http 补 `_error` 后缀） | orchestrator 既有重试 + 熔断累计 → 重试尽 → na | **case 级**——仅该 case 与 agent 交互失败（无证据），不牵连他 cluster 已跑通 pass |
| **executor 技术失败·非可重试** | `pool_error` / `http_client_error` / `body_too_large` / `no_usage` / `contract_error`（**v0.7.1 R-19：补入 executor 全值域非 RETRYABLE 漏项**——no_usage=未收 usage 契约违反、contract_error=契约/解析异常，executor.py L23/L31） | 不重试、不累计熔断 → **直接 na**（本地容量/契约级失败，重试放大负载无益） | **pool_error**/**http_client_error** = **环境级**（从严：出站连接/执行池失败，疑 agent/环境整体）→ 他 case pass 存疑；**body_too_large** / **no_usage** / **contract_error** = **case 级** |
| **调度层未执行（v0.3 新增，容错 B1）** | `circuit_open`（breaker 打开）/ `interface_disabled` | **显式归 na**——现码 `_run_one` L270-273/L340-343 对这两类走 `_save_result(..., error_type=...)`，其默认 `pass_fail='error'` 会把 error run 击穿出 {pass,fail,na} 值域（§2.1/批 1 §8.3）。error 分支必须拦截：不落默认 error 行，记 `pass_fail='na'` + error_type 原值 | **环境级**——整 run 复现会话不可靠 → 他 case pass 存疑，触发 unclean_run |
| **判定素材缺失（v0.4 语义修订，I2）** | `missing_assertion` / `assertion_shape` | **加载过滤前置（主路径，§6.2 step2/§7.5）**：素材缺失 case 不进 run、不落 na → 该行不再由 run 判定产出；运行期遇到（理论不可达，403 守卫防篡改 + 激活即非空）仅作**防御兜底 na** 并告警触发 backfill 对账 | **case 级**——主路径加载过滤前置、运行期仅防御兜底（同左） |
| **调度回收·未执行（v0.5 新增，容错 #5/逻辑 #5；**v0.7.2 R-22 扩为三路径同源**）** | `scheduler_unexecuted`（lease/pending 超时被 scanner 回收前未启动执行的 case；**v0.7.2 R-22：orchestrator cancel / run 级超时收尾（§6.3 表行 3）的未完成回填同落此 error_type**） | scanner 替代收尾（§6.6）回填 na 时固定此 error_type——v0.4 矩阵只覆盖 executor/素材/调度禁用源，漏了「回收前从未执行」这一类；无此值则回填 na 带 NULL error_type → online 按 error_type 分组聚合失败 | **环境级**——调度未执行/回收（run 级会话不可靠） |

**影响域标注语义（v0.6.2 R-2 修订，2026-09-07 拍板；本列 = offline 侧权威标注源，online 消费判定按此归类）**：claimed cluster 对应 case 在 run R 的行 + run 内 na 的 error_type 影响域决定污染力——**环境级 na**（上表 circuit_open/interface_disabled/scheduler_unexecuted/http_client_error/pool_error）→ 整 run 复现会话不可靠 → 该 run 内 pass 行不置 verify passed → 触发 unclean_run（批量）；**case 级 na**（timeout/connect_error/sse_parse_error/http_error/no_done/body_too_large/no_usage/contract_error/missing_assertion/assertion_shape；**v0.7.1 R-19：case 级清单字面量对齐 executor 真值 + 补入 no_usage/contract_error**）→ 只说明该 case 无证据，**不牵连**他 cluster 已跑通 pass（claimed case 自身 na 才单点 needs_review）。**无标注的新 error_type 默认从严 = 按环境级 → unclean_run + 告警**（从严仅兜底非常态）。offline 侧仅标注、**零机制改动**；判定语义落字 online detail §7.6 判定语义 v1.5 + solution v3.5.5（2026-09-07，见 §2.3 v0.6.2 注 ②）。

**scanner 回填对象界定（v0.5，逻辑 #5）**：scanner 替代收尾（§6.6）回填 na 的对象 = **实跑集内未终值 case**（该 run 已加载且尚无 EvalResult 的 case），**不含**被加载过滤（§6.2 step2）剔除的素材缺失 case——那些 case 不在实跑集、本就该「不进 run 不落 na」，回收不得把它们硬回填成 na（与 §6.2 过滤语义矛盾）；也不得因回收时点加载列表未持久化而落「run=timeout 但零行」（§6.3「未完成回填 na」落空 → online 按缺行（无终值）处理而非 na → needs_review 触发丢）。实施：run 创建时即把实跑 case 名单持久化（或回收时按加载谓词重算），使回填边界确定。

**熔断隔离（v0.3 性能 3 + 安全 + 容错 B1 合流）**：error 复现**不与 manual/held_out 共用 agent breaker**——共用会使「error 复现连败」熔断后波及同 agent 正常评测（反之 manual 熔断也误伤修复验证）。取独立 breaker key（`agent_id + ':error_regression'`）或 error 复现失败不累计共享熔断（二选一，默认**独立 key + 独立阈值**，实施定）；breaker 打开 → 上表 `circuit_open` → na → partial_failed，不污染 manual 判定。

na 不参与 agent 判定统计，只计 `na_case`。

**批量同根因 na 的聚合出口（v0.4 I-3 承接）**：被测 agent 整体宕机/接口禁用 → 同 run 同 `error_type` 成片 na（几十~上百 case）。offline 侧如实逐 case 落 na 行 + 同 error_type（如实落库，不提前合并）；**needs_review 的聚合在 online 消费侧做**（§2.3 聚合注：同 run 同 error_type 聚合成单条 needs_review，不逐 case 生成）——run 级 infra 故障不扇出 per-case 人工 review 风暴（对表注，B 包落字）。

### 6.5 免清理核对

`cleanup_rules.py` pinned 排除现成——error run 置 pinned 后不落入 run 级清理。批 1 §7.3「清理先归档 pass_fail 终态」由 pinned 保留满足，不新增归档（评审 A4 注记，见 §2.2）。

### 6.6 scanner 回收 error run：短路 salvage + 替代收尾动作（评审 B3 + v0.3 容错 1 修入）

`runner/scanner.py` 对超时 run 无条件 `score_run_salvage`（会把 error run 拉进 scorer 重跑断言、写 agent_score）。**scanner 回收 error_regression run 时短路 salvage，并执行替代收尾动作**：置 run `timeout` 终态、已完成 case 保留终值、未完成 case 回填 `pass_fail='na'`（§6.3）——**执行主体 = scanner 自身**（进程崩溃后唯一存活的回收者；`_finish_error_regression` 在 orchestrator 执行流里，崩溃后不保证执行）。职责仿 `score_run_salvage`：scanner 调 error 收尾函数，幂等（已终态 run 跳过）。若短路后什么都不做，超时 run 的未完成 case 无 EvalResult 行 → online 按无终值处理而非 per-case na → needs_review。§10/§11 补 scanner 收尾动作。

**pending 卡死回收（v0.3 安全方向）**：内部创建器已写初始 lease_until（§6.1）；scanner 对 `status='pending'` 且 lease 过期/缺失的行补回收（error_regression 特判），防 pending 永久卡死毒化 §5.2 幂等 skip 集。

**running 回收判据 = 推进心跳（v0.5 补全，逻辑 #2/容错 #5，替代 v0.4 单条「排队豁免」）**：error run 长跑（跨小时），不得用普通 hard_deadline 一刀切；scanner 无法从 status 区分「活着在 FIFO 排队」与「崩溃后停 running」的僵尸——判别物 = **lease 心跳**：
- **error 分支固定间隔续租（v0.5.2 钉死粒度）**：orchestrator error 分支执行循环以**固定时间片**（间隔 ≪ lease TTL，覆盖单 case 执行窗口）持续刷新 `lease_until`——**不得仅以「每 case 完成」为续租粒度**：error-run per-case 预算（§6.1 `case_timeout` 可至 600s）可长于 lease TTL，逐 case 粒度会让 executor 阻塞期间的活 run 心跳过期 → 被 scanner 按僵尸 running 误收为 timeout + 回填 na（复现风险 10「慢 agent 被误杀」，v0.5.2 终审 P1）。**排队等待期间亦刷新心跳**（case acquire 前等待 loop 内周期续租）——排队中的 running run lease 不因等待而过期。
- **scanner 对 `running` 且 lease 过期（心跳停）的行回收**（置 timeout + 实跑集内未完成 case 回填 na，§6.3/§6.4 回填对象界定）；lease 未过期的 running（活执行/活排队）**豁免**。僵尸 running 心跳停 → lease 过期 → 被回收，不永久占 §5.2 skip 集与洪峰配额（同 agent 全部 error 回归静默停摆的洞消除）。
- 普通 hard_deadline 仅作运行总预算兜底（防止活 run 无限跑），不作为回收主判据。

### 6.7 rerun/start 拒绝（评审 B M5，依赖批 1 守卫）

批 1 §7.1 承诺 create_run/rerun_run trigger 白名单只放 manual/held_out 防 DB 直插 error run 被 rerun——**依赖批 1 落地**。批 1 落地清单须包含：`rerun_run` 对 `trigger_type='error_regression'` 显式拒绝（若批 1 只拦 create_run 而漏 rerun，批 2 实施时补）；error run 不暴露 start/rerun 公开执行通道（内部重建走 §5.4 二期入口）。

---

## 7. 判定器（D1/D2 落地）

### 7.1 executor 复现（技术判定）

以 error case 的 `input`（激活时装载 envelope `evidence.input`）对被测 agent 执行一次真实调用，复用 `executor.execute_case`：

- 成功（`CaseOutcome.error_type is None`）→ 技术判定通过 → verifier。成功语义含输出归一 unified 容器（含最终文本 `answer`）。
- 失败（`error_type` 非空）→ 按 §6.4 分类 → 重试/直接 → `EvalResult.pass_fail='na'` + error_type 如实记录。

error case 不产 LLM-judge、不产中间评测指标——复现输出只喂给断言。

### 7.2 verifier no_fallback（断言执行新调用点；评审 B M1 修入）

普通 run 的断言在 scorer 阶段由 `score_run` 触发（`scorer.py:306 run_assertions`），error 分支不经 scorer → 断言执行是 **error 分支对 `run_assertions` 的新调用点**：

- 输入：复现 unified 输出 + 该 case 的 `assertions`（激活时固化的唯一一条 keyword_not_contains）。
- 判定：`run_assertions` 结果（该断言 bool）→ `True` → 'pass'（不再 fallback）；`False` → 'fail'（命中任一词 或 path 缺失/非文本保守 fail，D2；v0.6.2 扩：strip 后空串/纯空白应答亦 fail，见下注）。
- 产出：单 case pass/fail 终值，直接落 `EvalResult.pass_fail`（不经 scorer 聚合、无 agent_score）。

词表 = case 内固化断言关键词（激活时自 `no_fallback_config.words` 转录 + 词级净化，§7.3）；判定以固化为准，改词表不 retroactive。**命中一致性（v0.3 安全 4）**：净化规则（strip/拒空串/拒纯空白/拒超长词/大小写归一）须与执行端 `KeywordNotContainsOp` 子串匹配语义对齐（大小写是否敏感实施时核对并归一），防「边界词致恒 fail」或「大小写不一致致漏判恒 pass」。

**R-12 修补决策（v0.6.2，2026-09-07 拍板落字；代码变更单独立项）**：核对 offline 实现 `KeywordNotContainsOp.run`（`backend/app/assertions/ops/text.py`）发现——answer 键缺失/None/非 str 已全保守 fail（path 缺失/非文本层，D2），但 **answer 为 str 且空串/纯空白 → `hits` 空 → 判 pass**，且与 run 有无 na 正交（executor 跑通 + agent 空话术即中招）。决策 = **strip 后空串/纯空白应答 → 保守 fail**（并入 D2 分层守卫「无有效内容谈不上修复生效」，与 path 缺失/非文本 fail 同层），不新增 error_type、不改 executor 成功语义（§7.1：空应答仍落 verifier 判）。该修补作用于**共享算子**（普通 run keyword_contains/keyword_not_contains 断言亦走此 op）→ **属 offline 机制微改、突破本稿「offline 零机制改动」承诺 → 代码变更单独立项**（不在本批消费语义落字范围）；实施前置 = 先扫现网断言对空字段的依赖（确认纯收紧不误伤）+ 单测矩阵补「空串/纯空白应答 → fail」行（§11.1）+ 回归 keyword 金丝雀。

> **R-20 空答 fail 语义定性 + pre-scan 实施门禁（v0.7.2，2026-09-07 拍板落字）**：上 R-12 修补（strip 后空串/纯空白应答 → 保守 fail）**core 保持不动**；其上线前置 = **pre-scan 显式立项为实施门禁**——owner = offline 实施，产物 = pre-scan 报告：① 扫现网 `keyword_*` 断言对**空字段应答的依赖**（确认纯收紧不误伤——是否有靠空串 pass 的正向用例）；② 产「合法空答」清单（业务上允许空答的接口）→ 收紧**带豁免白名单上线**。**复现 run 空答 fail 语义定性**：error run 中 agent 空答（无有效产出）→ fail = **leakage 空话术复发证据**——agent 对回归输入只回空话术，与词表命中 fail（复发话术撞词）**同属「未修复」、证据形态不同**（词表命中 = 词可撞；空答 = 复发话术 strip 后无词可撞）；两者都说明修复未生效。**「空答不落 na、落 verifier fail」保持**（na = infra 判不了；空答 = 判定能且判 fail，§7.1 成功语义不因空答改判）。门禁登记 task 环 0 + §10 B 项 + Phase B code_detail。

### 7.3 error case 激活补写断言 + 存量 backfill（评审 I2 + v0.3 逻辑 W5/容错 6/安全 4 修入）

- **激活（依赖批 1 §6.3 步骤 4）追加**：建 error case 事务内写 `assertions = [{"op": "keyword_not_contains", "args": {"path": "answer", "keywords": <净化后 words>}}]`；`expected/metrics` 恒 None（D1 收窄）。结构自检已保证 words 非空（批 1 fail-closed）。**转录前词级净化（v0.3 安全 4）**：strip + 拒空串/纯空白/超长词 + 大小写归一（与执行端命中规则一致，§7.2）；净化后若词表为空（全被剔除）→ **报错拒激活**（fail-closed，不落空断言）。
- **守卫函数修订声明**：批 1 §5.1 `assert_normal_case_golden_fields` error case 分支「只允许 null」需随 D1 修订为——error case 分支锁 `expected/metrics` 恒 NULL、放行 `assertions` 由激活器写入（该修订属批 1 已承诺内容在本稿的显式化，实施批 1 时按此落）。
- **存量 backfill（v0.3 逻辑 W5 修入写路径）**：批 1→批 2 实施间隙产生的 active error case（assertions=None）在批 2 部署后补写断言。**写路径**：批 1 的 `update_case` 对 `case_type IS NOT NULL` 一律 403——backfill 是激活后内容变更，必须走**特权内部直写**（不经 update_case），且**仅当 `assertions IS NULL` 哨兵**下执行（空值幂等，不覆盖激活器已写内容）。补写前该类 case 不进入 error run 判定（见 §7.5）。
- **backfill 幂等与对账（v0.3 容错 6）**：backfill 写成**可重跑幂等**（仅补 `assertions IS NULL`），并**并入 §5.3 低频补偿对账**持续对账直到清零，防一次性任务漏跑/半途失败把「修复有效 case」永久误标 na。
- 读面：case 断言对读面等同普通断言结构，不含 envelope 敏感实文（批 1 §5.3 读面隐藏已覆盖断言载体之外的 envelope 列）。

### 7.4 keyword_not_contains 发布一致性登记（评审 B M1/S1 降格）

`KeywordNotContainsOp` class 已实现、内存注册、单测通过，**无需 DB 行即可执行**。登记目的 = 发布一致性 + config/读面列表正确 + create_op 白名单一致：

1. `core/constants.py` `_ASSERTION_OPS` 增补 `"assertions.ops.text.KeywordNotContainsOp"`（发布级常量变更，frozenset 注释"禁止运行时增删"→ 随本批上线）。
2. `ASSERTION_OP_CLASS` 增补 `"keyword_not_contains"` 键 → `seed.py:51 _seed_assertion_ops` 幂等插 DB 行（现 DB 若已有行则跳过）。
3. **同批上线核验**：`ALLOWED_CLASS_PATHS` 含其 class_path + `AssertionOpDef` 有行（若批 1 激活器做 DB op 校验）——白名单与 DB 行必须同批上线，防 `registry.load_from_db` 接上后启动抛错。
4. 激活器若在写断言时校验 DB `assertion_op_def`，须先确保行已存在（同批部署顺序）。

### 7.5 verifier 空断言防御（评审 I2 + v0.3 逻辑 W1 固定 error_type 值）

**素材缺失 case 的判定守卫（v0.4 语义修订：加载过滤前置，na 仅防御兜底）**：

1. **主路径 = 加载过滤（v0.4 I2）**：error run 加载 case 时过滤 `assertions` 非空且形态为 keyword_not_contains（§6.2 step2）——空断言/坏形态 case **不进 run、不产生 na 行**，其「缺素材」状态由 §5.3 补偿对账的 backfill 驱动清零；清零后门禁放行、补偿对账**同锚即建**（§5.2/§5.3 C1：run 未建则 latest 保持 None、锚未被消费，不依赖下次发版信号）。此路径下 partial_failed 内不存在「素材缺失 na」，杜绝「素材补齐后单 case 永久钉死」的假终态。
2. **运行期防御兜底（理论不可达，保留 fail-closed）**：若极端竞态仍遇空断言/被 403 写守卫篡改的坏形态 → **判 na，`error_type='missing_assertion'/'assertion_shape'`**（不静默 pass/fail），告警触发 backfill 对账（§7.3/§5.3）。两类均入 §6.4 矩阵「判定素材缺失」源。

**过渡性说明**：激活（§7.3）保证新 case 断言非空；backfill + 补偿对账后存量清零 → 加载过滤长期零命中、防御兜底零触发（v0.3 容错 6 → v0.4 由「判 na 守卫」升级为「过滤前置」）。

---

## 8. CaseVersion 惰性快照接通（D6）

error run 逐 case 落 `EvalResult` 时走 `orchestrator._ensure_case_version`（落 result 路径既有调用）→ 首次 error run 惰性建快照 = 信封原文 content（激活后不可变，403 写守卫）。**不预建、不抽 `_content_hash` helper**。核对点：error 分支落 result 必须走 `_ensure_case_version` 同路径，不得旁路（防 `EvalResult.case_version_id` 非空 FK 断裂）。

---

## 9. 统计/占位/读面隔离收口

### 9.1 dashboard 全聚合排除（批 1 §7.3 登记项落地，评审 A1 口径统一）

`api/dashboard.py` 现 gate/trend/compare/perf/cost/baseline 六处聚合只排除 `held_out`（现码行号：43/60/85-87/174/196/254，`_agent_dim_series` 138-141 是其中部分的上游序列 helper），`dashboard_rules.py` stale 依赖 gate。本批统一加 `AND trigger_type != 'error_regression'`：

- **口径**：**六处独立业务查询各加条件**（`_agent_dim_series` helper 是序列化辅助、覆盖不了六处各自 SQL 的 trigger 过滤），过滤谓词收口为一个公共常量（仿批 1 §5.4 统一过滤面），避免六处裸写漂移。stale 随 gate 排除自动修正。

### 9.2 活跃计数隔离（评审 B1）

`api/runs.py` create_run/rerun_run 的互斥计数扫描（`_MUTEX_STATUS=pending/running` 同 agent 行数 → 409）**排除 `trigger_type='error_regression'` 的行**——error run 不占互斥槽名额、不堵 manual 建单。v0.1 §9.2「不另改该函数」撤销。改两处扫描（:251-253/:378）。

### 9.3 推送载荷与出站行为（**v1.23 契约反转：原「平台只读面」整组取消，本节改写**）

> **方向反转（v1.23）**：offline 不再提供「供 online 回查」的只读面——改为 **offline 在 error_regression run 终态后主动推送结果**，online 侧零 outbound。原 §9.3 的只读面内容（批 1 §8 `/api/v1/runs` 上的 `na_case` / per-case `error_type` 消费）与 v0.7 只读面扩展（R-4 `excluded_case_ids` 溢出列表 / R-5「agent 已见版本」列表）**整组取消**；其中 `na_case` 与 per-case `error_type` **非废弃——载体改为推送载荷 `cases[]`**（`pass_fail='na'` + `error_type`），online 消费语义（na 聚合 needs_review）不变。

**端点**：`POST /backflow/regression-results`（online 收单，**同事务内完成判定**——判定已从轮询 job 搬进该端点）。

**触发时机**：run **终态 commit 之后异步**触发（§6.3 收尾表「推送」行）。终态写守卫（§6.3）保证终态后结果集冻结，故推送载荷**一次性产出**、数据集稳定。

**鉴权**：`Authorization: Bearer <secret>` —— **三出站端点共用静态预共享 secret**；**无 scope 分置、无 JWT**（原 §9.2 JWT 设计不落地；`BACKFLOW_INBOUND_SECRET` 整条作废）。

**失败语义（fire-and-forget）**：超时 **5s**、重试 **3 次**（退避 1s/2s/4s）；三次全败 → **记 error 日志后放弃、不阻塞 run 收尾**（online 作为接收方不重试）。**丢失代价**：该版本观察中断——online 侧「缺行中断」守卫（`prev_terminal_version` 本地无记录）使 K 序列不推进，不误判为 pass。

**载荷 schema**（`schema_version` 固定 `"1.0"`）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema_version` | str | 固定 `"1.0"`；不匹配 → 拒单 |
| `agent` | str | 被测 agent |
| `agent_version` | str | = 本 run 绑定版本（原 `bound_version`） |
| `run_id` | str | |
| `run_status` | enum | ∈ `completed` / `partial_failed` / `timeout` / `cancelled` |
| `agent_latest_version` | str | **水位 1**：该 agent 全部终态 run 的最大版本 |
| `prev_terminal_version` | str \| null | **水位 2**：本 run 之前该 agent 最近一个**已到终态** run 的版本；**必填但值可为 `null`**（= 此前无任何终态 run，即首次） |
| `trigger_signal_id` | str \| null | 可空（§5.3 consumed 锚） |
| `finished_ts` | str | ISO8601 UTC；**非法格式 → 拒单** |
| `cases[]` | array | `{case_id, case_type, pass_fail, error_type?, error_detail?}`；`pass_fail ∈ {pass, fail, na}`；na 行必带 `error_type`；**空数组合法** |

- **两水位字段口径硬约束**：均为 **agent 级事实**——统计范围 = 该 agent **全部终态 run**，**不得**按 `trigger_type='error_regression'` 收窄（online `_agent_versions(session, agent)` 签名内无 trigger_type 过滤）；manual/held_out run 虽**不触发推送**，其版本**计入**水位。误收窄 → 与 online「缺行中断」判据系统性错位 → **假中断**。
- **两水位必须同一次查询产出**：`prev_terminal_version` 漏带 → online 的「缺行中断」守卫失效 → 中间版本推送 fire-and-forget 丢失时被判「连续 pass」→ **簇被静默误判 fixed**。
- **v1 硬约束**：一次回归 run 只对应一个 cluster（`trigger_signal_id` 单值）；跨 cluster 批量回归不在 v1。

**响应字段**（online 回执；offline 仅记日志，**不据此做业务分支**）：

| 字段 | 语义 |
|---|---|
| `accepted` | 是否收单 |
| `duplicated` | 是否幂等重放 |
| `run_record_id` | 落库的 `verify_run_record` id |
| `links_advanced` | **真正发生终态迁移**的 link（passed/failed/superseded）；重复推送 / orphan 为空 |
| `cases_dropped` | 丢弃的 case 数；**幂等重放恒返与首次相同的值、不归零** |

**拒单规则**（整单拒；offline 记 error 日志后放弃，**不重试**）：

- `case_id` 在 `cases[]` 内**重复** → `ERR_PULL_0002`（同 case 两条 = 载荷自相矛盾）→ **offline 出站前须保证 `case_id` 唯一**
- `finished_ts` 非法 ISO8601 → `ERR_PULL_0002`
- `schema_version` 不匹配 → 拒单

**幂等键**：`uk_verify_run(link_id, run_id)` —— 同一 `(link, run)` 重复推送 → 200 + `duplicated:true`。

批 1 §8.3 对表输出随本稿更新；`excluded_case_ids` / 「agent 已见版本」两处只读面**不再提供**。

### 9.4 人类读面隔离 + 排障路径（v0.3 安全 3 + 逻辑 S2 修入，新增）

- **error run 的普通读面隔离**：error_regression run（含其 case_ids、error_detail、复现输出）与 error suite 同属敏感面——**不出现在普通 viewer 的 `list_runs`/`get_run`/`run_results` 人类读面**（与 §9.1 dashboard 排除同精神；批 1 只排除 dashboard/普通 suite，未覆盖 run 列表，本稿补）。error run 仅 admin/evaluator 角色可见（其结果经**推送载荷**进 online，§9.3；原「平台只读面」随 v1.23 **整组取消**）。
- **`case_version.snapshot` 继承 error case 读面限制**：error run 落 result 惰性建快照（§8），snapshot 含 `input` 实文 = 从信封 evidence 装载的复现输入——**人类读面对 error run 的 case 级 read 不随 results 暴露 snapshot 的 input 实文**（按批 1 §8.2 响应字段白名单裁剪），防批 1 §5.3「只隐藏 `backflow_envelope`」被快照旁路；**推送载荷（§9.3）亦不含 input 实文**——载荷 `cases[]` 仅 `case_id/case_type/pass_fail/error_type/error_detail`，无 `input` 字段。
- **排障路径（逻辑 S2）**：error run 需要可排查但不可全暴露——admin/evaluator 内部 API + 日志透出 error_type 明细（EvalResult.error_detail）、na 分类源、收尾终态；`partial_failed`/needs_review 根因（复现失败 vs 调度未执行；素材缺失经加载过滤已不构成 run 判定源，v0.4 I2）有日志/告警 key 可查（§5.3 补偿对账漏触发同纳入告警）。**error_type 的双面归属（v0.4 I3/S2 收口）**：普通人类 viewer 无 error_type/error_detail（隔离如上）；**推送载荷（online 消费，§9.3）na case 带 error_type 分类值**；admin/evaluator 全量明细——**两面分级**，互不越界（原「三面」中的平台只读面随 v1.23 取消）。

---

## 10. 复用与明确不改清单

**复用**：`executor.execute_case`（复现）、`_ensure_case_version`（惰性快照）、`RETRYABLE_ERRORS` + orchestrator 重试熔断、`run_assertions`（断言执行函数本体，新调用点）、`agent_mutex`（建单防重串行，非执行期）、cleanup pinned 豁免、批 1 §7.2 `_load_run_cases` error 分支（依赖批 1）、共享 `_finish` L536-546 的同事务直插模式（§6.3 事务约束沿用）。

**新增**：内部创建器 `_create_error_regression_run`（§6.1，含 error-run 专用预算 run_config + 初始 lease_until + **`trigger_signal_id` consumed 锚**）、`maybe_auto_schedule`（§5.3，信号**终态**挂接）+ 低频补偿对账（§5.3，含 backfill 清零对账）、orchestrator error 执行分支（§6.2，含 suite-agent 归属校验、调度层未执行归 na、error 复现独立熔断 key、error 分支不经默认 error 落库路径、**加载断言非空过滤**）、**执行期并发闸 = 共享 per-agent 执行槽池**（§6.2 step4，取代 v0.3 run 级排他窗；manual 侧零改动，取消 v0.3 W2「manual/held_out 同接入排他窗」）、**manual/held_out version 校验放宽为共享域**（api/runs.py create_run/rerun_run，§2.4）、独立收尾 `_finish_error_regression`（§6.3，同事务直插约束 + uk_result 幂等兜底）、scanner error 收尾替代动作 + pending 租约回收 + salvage 短路（§6.6）、激活断言装载（含词级净化）+ backfill 特权直写（§7.3）、keyword_not_contains 白名单/seed 登记（§7.4）、活跃计数扫描排除（§9.2）、推送载荷字段 na_case + **per-case `error_type`（na case，§9.3）**、人类读面隔离（§9.4）、**创建前门禁**（§5.2，runnable 空不建）、**cap 截断 newest-active-first + `case_truncated` 标记**（§6.1 注 1）、**error 分支逐 case/排队心跳续租 + running 回收心跳判据**（§6.6）、**终态写守卫**（§6.3）、**na 第五源 `scheduler_unexecuted` + scanner 回填对象界定**（§6.4）。

**明确不改**：`runner/scorer.py` 主链与 `_enabled_dims`（error run 不经它）、`judge/` 全模块、共享 `_finish` 对账逻辑（error 用独立收尾）、`create_run`/`rerun_run` 公开 **trigger 白名单**（保持 manual/held_out 不变；v0.4 仅放宽 version 校验、白名单不动）、普通 case（`case_type IS NULL`）一切语义、dashboard 业务聚合逻辑（只加过滤条件）、platform_runs.py 查询结构（**v1.23 取消**——平台只读面整组取消，字段改由推送载荷承载，§9.3）。

**跨文档同步项（v0.3 逻辑 S1 → v0.4 修订，随本稿需同步修订已 commit 的批 1 文档）**：① 批 1 §8.2 只读面响应字段补 `na_case` + **per-case `error_type`（na case，v0.4）**——**v1.23 取消**（只读面整组取消，两字段改由推送载荷承载）；② 批 1 §8.3 对表补 na 值域 + error run 终态→online 映射（§2.3 表，v0.4 版含缺行≠na 拆分）；③ 批 1 §7.3 登记项 D-14：v0.2 的「撤销确认（R1）」随 v0.4 **修正为「以共享域形态恢复」**——manual/held_out version 校验从 strict semver 放宽为版本字面量域（§2.4）；④ 批 1 §5.1 `assert_normal_case_golden_fields` error case 分支措辞修订（D1/§7.3）。实现者按批 1 实施时须以此为准，防文档冲突。

**对 online 侧契约修订清单（B 包，v0.4 第三轮契约方向 A1-A9 归并 + v0.5 第四轮契约方向逐条核对现文后重构；2026-09-03 已落 online `solution_detail.md` v1.3 + `solution.md` v3.5.3 + `task.md` 环 0 B-7，落点与回执见下）**——本稿 §2.3 对表 = offline 主张的消费契约，已随下列各项落字生效：

> **v0.7 修订包（R-3~R-10，2026-09-07 逐问评审拍板，online 已落字 `solution_detail.md` v1.6 + `solution.md` v3.5.6）**：本批为**方向性契约修订 + offline 机制改动混合包**（突破「offline 零机制」承诺的项 = R-3 对账差集 / R-8 探测态节流 / R-4/5 只读面，与 R-12 同类：**本稿决策 + 规格登记，机制代码变更单独立项、phase1 稿保持基线**）。逐项归属与落点：**R-3** 对账版本差集补齐（offline 机制改动，本稿 §5.3 v0.7 落字块 + §2.3 v0.7 注①，online 语义零改动）；**R-4** cap 溢出 case 只读面透出（offline 只读面新增 §9.3 v0.7 注 + online 回查欠测判定落 detail v1.6 §7.6/§8.7/§9.3）；**R-5**「agent 已见版本」只读面（offline 只读面新增 §9.3 v0.7 注 + online claim 软校验落 detail v1.6 §8.4/§9.3）；**R-6** err_summary_json error_type 原值 schema 钉死（online 实现前钉死点，detail v1.6 §4.3/§5.1⑩ DDL 注释，offline 零机制）；**R-7** online_content_gap 批量 requeue + 可愈性标注（online 工具，detail v1.6 §7.4/§8.4/§9.3 + solution v3.5.6；offline 零机制——§6.5 重处理逐 payload 天然支持批量重拉）；**R-8** cap_gap 探测态节流（offline 现稿行为修正、phase1 §6.5 判据收窄，本稿 §2.3 v0.7 注④，online 契约零改动）；**R-9** needs_review_batch resolve 语义化跳过非 claim 引用 cluster（online 并发语义，detail v1.6 §7.6/§8.4 + solution v3.5.6，offline 零机制）；**R-10** input 截断标记 + reason 值域 3→4 增 `input_truncated` + hash 归一上限 4096→8192（online 语义 + detail v1.6 §5.1 DDL/§7.6，offline 零机制仅边界注记 §2.3 v0.7 注⑤ + phase1 §2.1 增注随代码单独立项）。**R-11** 环 2 用例集并入 §11.2 场景 17-22（R-6/R-9 online-only 场景归 detail v1.6 §14）。全文「待落字 online detail v1.5」等旧框架随 v0.6.2 已回正、本批无需改动。
>
> **v0.6 B 包扩展（R5-R7，2026-09-03 方向性修订 → v0.6.1 终审通过，2026-09-07 已落字 online `solution_detail.md` v1.4 + `solution.md` v3.5.4）**：auto-fixed 判据重构（详见 §2.3 v0.6 注）——**B-10 = recheck 纯净 run 前提** / **B-11 = 跨版本稳定序列 K（载体 = 既有 verify_run_record，消费侧改判定）** / **B-12 = reentry 同版本禁 auto-fixed**。三案均**消费语义**，offline 侧零机制改动（na/error_type/verify_run_record 均已透出或已存在），改 online 判定逻辑即可。判定现文改动以 v1.4 落字为准，正文 v1.3 语义保留为历史基线。
>
> **v0.6.1 精化（2026-09-07 终审通过）**：B-10~B-12 的三处消费语义已补定义（聚合与 reason 值域 / 纯净可判判据 / K-TTL 收敛终点，见 §2.3 v0.6 注 ①②③ + 修订记录 v0.6.1 行）——**v1.4 已按精化规格执行（2026-09-07）**：detail §1.4/§7.6/§12.1 登记 unclean_run/reentry_same_version reason 值域与聚合归属、§7.6 纯净可判判据 + K 序列重写判定 blockquote、§5.1⑧ verify_run_record 补消费注、§7.5/§8.4/§10.1 联动 reentry_same_version 提示与 K-TTL 收敛终点。
>
> **v0.6.2 修订（2026-09-07 拍板落字，detail v1.5 / solution v3.5.5）**：B-10~B-12 的 online 消费判定语义随修订包再修——auto-fixed 判据的 K 由「全局默认 K=2 可配（v1.4）」改 **claim 级固化 claim_k**（B-11 载体 = verify_run_record 逐版本时间线不变）；纯净判据由「run 级 `na_case==0`（v1.4）」下钻「**claimed case 行 + na error_type 影响域分流**」——本稿 **§6.4 矩阵已加「影响域（环境/case）」权威标注列**（R-2 落点），online 按标注归类、无标注新 error_type 默认从严，unclean_run 批纯化 + 撞键裁定收窄（B-10/B-11 语义精化，纯 na cluster 退批 cluster 级单点）。offline 运行机制零改动保持；**R-12 例外** = `KeywordNotContainsOp` 空串/纯空白应答保守 fail 修补（§7.2 决策注 + §11.1 单测行，共享算子微改、代码变更单独立项）。
>
> **v0.7.2 修订（2026-09-07 拍板落字，online 已落字 `solution_detail.md` v1.9 + `solution.md` v3.5.9；offline 运行机制零改动、仅收尾语义钉死 + 护栏登记）**：本批修订 R-22/R-23/R-20 均为 offline 侧语义钉死 + online 消费侧同步——**R-22** 收尾回填 na 统一 `scheduler_unexecuted`（§6.3 v0.7.2 注 + §6.4 源行：scanner 回收 / orchestrator cancel / run 级超时收尾三路径同源，timeout 不作回填原因，「na case 必带 error_type」不变量全覆盖收尾路径 + §11.1 护栏断言）；**R-23** timeout 三层语义分界（§2.3 v0.7.2 注③：case 级 na 源 / run 终态 timeout 半途中断不直接触发 needs_review、claim TTL 兜底 / run 级超时收尾回填 na = 环境级 → 关联 pass case 入 unclean_run 批；「run 超时非源」只排除 timeout 事件直接驱动 cluster 状态、不排除环境级回填污染的间接作用）；**R-20** 空答 fail 语义定性 = leakage 空话术复发证据 + **pre-scan 显式立项 Phase B code_detail 实施门禁**（owner = offline 实施，产物 = pre-scan 报告；R-12 core 修补不动，§7.2 R-20 注 + §11.1 + task 环 0 门禁登记）。online 消费语义落点 = detail v1.9（§4.3④/§4.4/§5.1⑩/§6.1/§6.2/§7.4/§7.6/§9.4/§14 E-28~E-29 等）+ solution v3.5.9（§6.1/§7.2/§10.2 等）。
>
> **v0.5 B 包重构说明（契约方向逐条核对现文）**：B-1 拆 a/b/c 三条（判定源值域映射 / 多 run 选取序并入 recheck / §8.7 status 值集与轮询终止）；**B-2 撤销**（v1 放弃离线触发覆写，见 §2.3，不再需 online 落字）；**B-8 移出 B 包**（na_case/error_type 进平台只读面 = 批 1 §8.2 offline 字段白名单，已由 §10 跨文档同步项①覆盖，online 侧残留并入 B-1）；**B-3 落字需同步改 §1.4/§12.1 二期标注**；**B-4 需定义聚合载体/触发**（现文 needs_review 是单 cluster 状态，无跨 cluster 批量实体）；**B-5 扩到 §7.5 reentry 版本门控序语义**；**B-6 顺带修 §5.1⑥ run_status 注释值域**；**新增 B-9 = 跨端 case 归档（二期预留，v1 不落）**。
>
> **⚠ 快照注（v0.5.2）**：以下「对接核查小结」为 v0.5.1 时点的**落字前核查快照**，其中「S1 未落 online / 风险 13 维持 / offline 主张」等均为快照时刻的历史状态——**均已由紧随其后的 v0.5.2 落字回执解决**（online 已按本稿落字 v1.3）。读本块请以落字回执为终态，小结仅作核查过程留存。
>
> **对接核查小结（v0.5.1，2026-09-03；非评审修订——online solution_detail v1.2 / 批 1 v0.2.1 / 本稿三卡对表后逐缝核清，S1-S6 见上一条消息）**：**S1 判定消费语义未落 online = 本 B 包本体**——recheck 骨架 online v1.2 已有（recheck_job §7.6 + verify_run_record + `closed_by='auto_regression'` 单链 + L1075「无 run 提示 pending」），缺的是 error run **终态字面量 / na+error_type 消费 / 缺行≠na 轮询 / 聚合载体**（风险 13 维持，落字前 §2.3 表仍为 offline 主张）；**S2 澄清（无真实缝）**：`auto_regression` 是 cluster 置 fixed 时的 `closed_by` 标签（online E-7/L899/L480），判定是**单链**（claim→offline 回归 run→recheck 单错级回查），不存在「online 自有回归 vs offline 回流」两源歧义——B 包落字是数据形态精确化，非判定源替换；**S3 澄清（无空洞）**：error case 断言构造已由本稿 D1/§7.3 定义（激活器把 `no_fallback_config.words` 固化 `keyword_not_contains` 写入 TestCase.assertions）+ 存量 backfill 特权直写并入补偿对账（§5.3/§7.3），批 1「三列可空」由 D1 收窄为激活后 assertions 恒非空，实施顺序门内无冲突；**S4 裁决**：reentry 版本门控序语义 = 等值+日期前缀门控（§2.4 + B-5 细化，v1.2 L886 现文序比较需随 B-5 修订）；**S5**：发版=manual/held_out run 硬前提并入 B-7 环 2 用例；**S6**：run 级超时批量 na 呈现并入 B-4 设计注。
>
> **落字回执（2026-09-03，P0）**：上述 B 包已按本稿落 online `solution_detail.md` v1.3（A1-A7 + 三处排查补正）+ `solution.md` v3.5.3 + `task.md` 环 0 B-7 登记——**B-1(a/b/c)** → §7.6 回归 run 判定语义 blockquote（只消费终态 run：pass→fixed / fail→error 仍在 / na 行→needs_review / 缺行≠na 轮询至 claim TTL）+ §8.7 回查 status 值集 {completed, partial_failed, timeout, cancelled} + §5.1⑥ run_status 注释；**B-3** → §1.4/§12.1/§7.6 needs_review 口径（v1 源 = error run `na` 结果行、判分 na/会话快照不足/cc 二期或 D18、run 超时非源）；**B-4** → **载体定稿 = `needs_review_batch` 实体**（detail §5.1 ⑦b DDL：run_id/agent/bound_version/error_type/status(open|resolved)/link_refs JSON/reason/resolve_action/resolved_by/created_ts/resolved_ts；处置 = 整批单动作单事务 {reopen_cluster, escalated}、引用 clusters 处置前保持 claim、conversion_record action=needs_review_resolve、端点 `POST /backflow/needs-review-batches/{id}/resolve`；单 case na 无批 → 既有 cluster 级 `POST /backflow/clusters/{id}/needs-review-resolve`）；**B-5** → §8.4 fix_version 字段注释 + §7.5 版本门控（等值+日期前缀）+ claim 表单候选提示/归一化 + §7.5 同日不等值 count+1 **可见性提示**（fixed/claim 详情显「同键新版本复发 N 次」，前端实时派生 ES 同键最新版本、不新增列）；**B-6** → §5.1⑥/§7.3 注释；**B-7** → task.md 环 0 登记；**B-9** → task.md 二期预留。

| # | online 修订点 | 落点（solution_detail / task.md） |
|---|---|---|
| B-1(a) | **推送入站判定源限定终态 run + per-case 值域映射**：只消费 `status ∈ {completed, partial_failed, timeout, cancelled}` 的 run（**绝不含 running/pending**——含 running 会把先落库的部分 pass 行当终值 → premature fixed）；per-case pass→fixed / fail→error 仍在 / **na 行（pass_fail='na'）→ needs_review** / **无结果行（缺行）≠ na → 按无终值处理**（观察中断由载荷水位字段 `prev_terminal_version` 探测，不再轮询）（A1/A7/K2） | §7.6 推送入站判定 + §8.7 回查端点（**v1.23 取消**） |
| B-1(b) | **同 (agent,version) 多 run 选取序并入推送入站判定**：优先最近 `completed`、无则最近可判终态（§2.3）；claim 未 closed 期间每次推送入站取「当前最近可判 run」判定、有推进即更新（未定前用新 run） | §7.6 推送入站判定 |
| B-1(c) | ~~**§8.7 回查 `status=` 查询值集 = {completed, partial_failed, timeout, cancelled}** + 轮询终止边界（recheck_job 绝不传 running/pending）~~ **v1.23 取消**（§8.7 回查端点随三只读面整组取消、`recheck_job` 已删；原字面量对表随推送载荷复用，见批 1 §8.3 保留项）；**`verify_run_record.run_status` 注释对齐 error run 字面量域**（现文 §5.1⑥ 写 passed/failed 与 verify_status 混用是错的，v0.5 容错 #4） | §8.7 端点（**v1.23 取消**）+ §5.1⑥ 注释 |
| B-2 | **（v0.5 撤销）** v1 放弃「离线迟到 run 覆写已 fixed」——false-fixed 收敛 reentry（§7.5 版本门控）/ admin reopen / 下轮 claim；online 终态只读 L911 保持不改。B 包无此落字（§2.3，A7 作废） | — |
| B-3 | **needs_review 语义写清 = 多源人工复核队列**：v1 源 = error run 的 `na` 结果行（reason 区分，error_type 分类值入 reason 展示；na 源矩阵见 §6.4——run 判定内可达 = executor 技术失败[可重试/非可重试]/调度层未执行/调度回收 scheduler_unexecuted，素材缺失源加载过滤前置 v1 不可达）；判分 na 仅二期追加源（**落字时同步改 §1.4/§12.1 现文「判分 na 属二期/不回流」标注，限定「error-run 产 na 为 v1 源、其它判分 na 仍二期」**）；超时**不是** needs_review 源（真正兜底 = claim TTL 超窗回退 open）（A3/A5） | §1.4/§12.1 needs_review 触发源条目 |
| B-4 | **批量同 error_type na 聚合触发规则 + 载体**：推送入站判定对同 run 同 error_type 的 na case 只置**单条** review（run_id + error_type + case 引用列表），不逐 case 生成——**聚合载体已定（2026-09-03 落字）= `needs_review_batch`**（detail v1.3 §5.1 ⑦b DDL + §7.6 处置 blockquote：run_id/agent/bound_version/error_type/status open|resolved/link_refs JSON/reason/resolve_action {reopen_cluster, escalated}/整批单动作单事务/引用 clusters 处置前保持 claim；端点 `POST /backflow/needs-review-batches/{id}/resolve`；单 case na 无批 → cluster 级 `POST /backflow/clusters/{id}/needs-review-resolve`；原「可选形态」句作废）（I3；v0.5.1 对接核查 S6：聚合载体须容纳「整 run 超时/中断 → 同 error_type 批量 na」的呈现——run 级故障非 case 级问题，review 文案区分「该版本回归未判全（超时）」与「个案 infra 无法判定」） | §7.6 推送入站聚合规则 + 载体定义 |
| B-5 | claim **fix_version 字段注释**：与 offline error run.version **共享版本字面量域**、不强行 semver（存储 VARCHAR(64) 不变）；**§7.5 reentry 版本门控 `agent_version ≥ cluster.fix_version` 序语义同步处理——落地语义（v0.5.1 对接核查 S4 裁决）= 等值+日期前缀门控**（同日期前缀仅等值放行 reentry、同日不等值按上线中 count+1、跨日按日期前缀串序；详 §2.4）；**claim 表单 fix_version 候选提示/归一化**（防人手输入 ≠ agent 自报，风险 17）（A2/Z1） | §8.4 字段注释 + §7.5 版本门控 + claim 交互 |
| B-6 | §7.3 期望 #3「清理先归档」措辞、§5.1⑥ `run_status` 注释值域（passed/failed 与 verify_status 混用更正 + 补 error run 终态集合）同步（A4/A9） | §7.3/§5.1⑥ 注释 |
| B-7 | **task.md 环登记**：环 0 契约冻结补本稿字面量对表（终态字面量 + 值域 + version 域 + needs_review reason 字段 + 响应补 na_case/error_type + **per-case pass_fail 值域含 na** + 回查 status 值集 —— **v1.23 取消**）；环 2 登记批 2 双端闭环场景 + fix_version mismatch 用例（A6；v0.5.1 对接核查 S5：环 2 把「被测 agent 发版 = offline manual/held_out run 达终态」立为**硬前提用例**——发版未触发 offline 评测则信号缺失 → 该版本 error run 永不建 → 无推送入站、TTL 兜底，属流程前提非契约缺陷） | task.md 联调闭环小节 |
| B-9 | **（二期待办，v1 不落）跨端 error case 归档/去重闭环**：online cluster 终态（fixed/superseded/invalidated）→ offline error case 出 active，分母从守卫驻留改有界——需设计通道（offline 拉 cluster 终态 / online 通知，二选一）；v1 的守卫驻留语义（§6.1 注 2）到期后以本项收敛（R3） | task.md 二期预留 |
| B-10 | **（v0.6.1 已落字 detail v1.4，R5）推送入站判定加「纯净 run 前提」**：仅 **0 技术性 na** 的 run 内 pass 行可触发 verify passed；含技术性 na 的 run 内 pass 行 → 该 case needs_review（reason 带 run_id + `unclean_run`），fail 行照旧即时 reopen，不纯净 run 不计 K 不清零——改 detail §7.6 回归判定语义 blockquote（现 v1.3「pass→fixed」单判），补推送载荷判定无需 offline 新字段（na_case/error_type 已透出） | §7.6 推送入站判定 |
| B-11 | **（v0.6.1 已落字 detail v1.4，R6）auto-fixed 改「跨版本稳定序列」**：case 自 claim fix_version 起连续 K（默认 2、可配）个纯净可判版本 run 均无 fail 才置 verify passed；fail 任何版本即时 reopen；缺行≠na 中断观察不计数；载体 = 既有 `verify_run_record` 逐版本时间线，消费侧改判定逻辑（聚合已存在的事实行），**不加表/不加 offline 字段**；K=1 兼容现行为；老 case 窗口截断中断序列 → 接受退化、缺行语义不变 | §7.6 推送入站判定 + §5.1 ⑧ verify_run_record 消费注 |
| B-12 | **（v0.6.1 已落字 detail v1.4，R7）reentry 同版本禁 auto-fixed**：generation>1 cluster（reentry 产物）claim 填的 fix_version = 该 (agent,version) 已有 completed run 的版本 → 该 cluster 不用旧 run pass auto-fixed（旧 run = 修复前证据），推送回传 pass → 转 needs_review（reason `reentry_same_version`，提示人工/升版）；改填新版本走 R5/R6 正常判据；「completed 不重建」守卫（§5.2）不放开——与 §7.5 reentry 门控 + §8.4 claim 字段校验联动 | §7.6 + §7.5 + §8.4 claim 交互 |

**依赖批 1 落地清单（实施顺序门）**：alembic DDL（trigger ENUM + case_type + is_error_suite + 三列 nullable）；激活器 pull_loop 与结构自检；403 写守卫（含 `assert_normal_case_golden_fields` 的 D1 修订）；error suite upsert；`_load_run_cases` error 分支；orchestrator trigger 分发与早退闸；create_run/rerun_run trigger 白名单（rerun 对 error_regression 显式拒绝，§6.7）；平台只读面（**v1.23 取消**——只读面整组取消，结果改由 offline 主动推送）。

---

## 11. 测试与联调

### 11.1 单元测试（核心逻辑覆盖）

- **verifier 判定矩阵**：pass（无命中）/ fail（任一命中）/ 多词表子串边界（前缀/超串/全角半角）/ 断言层保守 fail（path 缺失、非文本）/ **strip 后空串/纯空白应答 → fail（v0.6.2 R-12 修补，2026-09-07 拍板落字）**——executor 跑通 + agent 空话术不得判 pass，与 path 缺失/非文本 fail 同层（决策注见 §7.2；代码变更单独立项）。
- **na 判定矩阵（镜像 §6.4 五源矩阵逐源一例；v0.3 三层 → v0.4 executor 拆可重试/非可重试 → v0.5 增调度回收源）**：① executor 技术失败·可重试（timeout/connect_error/sse_parse_error/no_done/http_error，**v0.7.1 R-19 字面量对齐 executor.py L38**）→ 重试 → 重试尽 na；② executor 技术失败·非可重试（pool_error/http_client_error/body_too_large/no_usage/contract_error，**v0.7.1 R-19 补入漏项**）→ 直接 na 不重试；③ 调度层未执行（circuit_open/interface_disabled）→ na、不落 'error'；④ 判定素材缺失（missing_assertion/assertion_shape）→ **加载过滤前置（case 不进 run）**，运行期仅防御兜底 na + 固定 error_type（理论不可达，v0.4 I2）；⑤ 调度回收·未执行（scheduler_unexecuted）→ scanner 收尾回填 na + 固定 error_type，回填对象 = 实跑集内未终值 case（§6.4/§6.6，见「scanner 回填对象」用例）。**非源边界（不属 na 源）**：词表净化（空串/纯空白/超长词/大小写归一，净化后空词表拒激活）——防边界词致恒 fail / 大小写不一致致漏判恒 pass。
- **触发幂等**：同 (agent,version) 首建/已建（pending/running/completed）跳过；无 error case/error suite 不建；并发双触发行锁串行只建一个。
- **对账差集（v0.7 R-3）**：洪峰 v1 running 期间 v2/v3 信号被活跃闸拒建 → 对账差集（manual distinct version {v1,v2,v3} − 已有 error run {v1}）逐周期补建 v2 → v3（每周期至多 1 个、遵守活跃闸）；差集排除 error_regression run（不作信号）；timeout/cancelled 坏终态版本号视为有 run（不计入差集，重建走 §5.2 新信号逻辑）；对账多周期模拟无重复建、吸收态继续拦截已消费版本。
- **run 收尾**：全 pass/fail → completed / 含 na → partial_failed / 未跑完回填 na 不写 error / na_case 计数。
- **并发闸/共享槽池（v0.4）**：manual 占满槽位时 error case 排队（FIFO），error 复现不叠加出超限并发；manual 释放槽后 error case 推进；manual 侧无感知改动（取代 v0.3 排他窗用例）。
- **活跃计数隔离**：error run pending 不影响同 agent manual 建单（N=1 可建）。
- **scanner 短路**：error run 超时回收不触发 salvage、不产 agent_score。
- **加载分支组合**（批 1 §7.2 续）：manual × held_out × error_regression × case_ids 真 error case 隔离。
- **backfill（v0.3 → v0.4 → v0.5 C1）**：仅 `assertions IS NULL` 哨兵可写、绕 403 特权直写、幂等可重跑；空断言 case 不进 error run（加载过滤）；清零前门禁不建空 run、清零后同锚对账即建（§5.2/§5.3）。
- **D8 前置校验（v0.3）**：pinned=false / case_ids 空 / suite-agent 不归属 → skip + 告警。
- **闸与回收交互（v0.3 → v0.4 → v0.5）**：槽池排队（error case 不叠加超限并发）；排队/执行中 running error run 心跳续租（lease 未过期不回收），僵尸 running 心跳停被 scanner 回收（§6.6）；pending 卡死被 scanner 租约回收；**坏终态（timeout/cancelled）后仅「新信号」（未被 latest.trigger_signal_id 消费）重建、completed/partial_failed 不重建；补偿对账复用同锚被吸收态拦截——同一坏 run 不被周期重建（v0.4 K1/B1）**。
- **熔断隔离（v0.3）**：error 复现连败不污染同 agent manual breaker；breaker 打开 → na → partial_failed。
- **容量上限（v0.4 D-1 → v0.5 R3）**：case 数超 cap → **newest-active-first 截断**（最老溢出，不进 run、无结果行、不落 na）+ run 记 `case_truncated`（§6.1 注 1/§2.3）；claim 对象（最新激活 case）必进 run。
- **版本字面量域（v0.4，Z1/A2）**：非 semver agent 版本（如 `2026.08.31-r47`）经 manual 登记 → 发版信号 → error run.version 原样承载 → 推送入站字符串命中；同 agent 乱序版本仅告警不拒。
- **创建前门禁（v0.5 C1）**：error suite 全 case 断言空（backfill 未清零）+ 发版信号 → **不建 run**（latest 保持 None）；backfill 清零后补偿对账同锚建出含存量 case 的 run——存量 case 在该 fix_version 即被判定，不必等下一新版本。清一色 `assertions=None` + 一次发版信号为第 4 轮容错 #6 时序洞回归用例。
- **截断排序与诚实标记（v0.5 R3）**：case 数超 cap → newest-active 优先截断、最老溢出（不进 run、无结果行、不落 na）、run 记 `case_truncated`；claim 对象（最新激活 case）必进 run。
- **终态写守卫（v0.5）**：run 入终态后 EvalResult 写入被拒（uk_result 撞键 = 丢弃迟到行 + 日志）；scanner 置 timeout 后迟到 pass 行不落库（容错 #5）。
- **running 心跳回收（v0.5）**：error 分支**固定间隔续租**（间隔 ≪ lease TTL，覆盖单 case 执行窗口，不得仅逐 case 完成，v0.5.2）+ 排队期心跳；排队中/单 case 长跑（时长 > lease TTL）的 running run lease 未过期不回收；僵尸 running（进程崩溃、心跳停）lease 过期被回收置 timeout（逻辑 #2）。**用例：单 case 时长 > lease TTL 的 error run 不被误回收**。
- **scanner 回填对象（v0.5）**：回收回填 na 只覆盖实跑集内未终值 case、error_type=`scheduler_unexecuted`；被加载过滤剔除的素材缺失 case 不落 na。
- **收尾回填 error_type 护栏（v0.7.2 R-22）**：收尾三路径（scanner 回收 §6.6 / orchestrator cancel / run 级超时收尾 §6.3）回填的 na 行 **error_type 断言非空且 = `scheduler_unexecuted`**——「na case 必带 error_type」不变量（§9.3）全覆盖收尾路径；**timeout 不作收尾回填 error_type**（run 终态 timeout 下未完成 case 回填 na + `scheduler_unexecuted`，不落 'timeout' 当 error_type）。
- **verifier 空答 fail 定性（v0.7.2 R-20，承接 §7.2 R-20 注）**：agent 空答/纯空白应答 → fail 的语义 = leakage 空话术复发证据（非 na、非词表命中独立形态），单测与词表命中 fail 用例并列——空话术 strip 后无词可撞、落 fail 不落 na。

### 11.2 桩与环 2 双端集成

- **error 复现桩（测试基建）**：offline 无 mock/sandbox。环 2 需可控 fallback 输出的被测 agent 桩——返回受控响应，可切"含词表词/不含/断连"，覆盖 verifier 两分支与复现失败。
- **环 2 双端闭环用例**（对齐 task.md 联调环 2，offline 侧真跑）：
  1. online fake 组 payload → pull → 激活 error case（断言固化）→ ack active；
  2. 桩切"仍 fallback" → manual run（新 version）触发自动建 error run → 复现命中 → case fail → completed；
  3. 桩切"已修复" → 再发版 → (agent, 新 version) 建新 error run → 不命中 → pass；
  4. 桩断连 → 复现失败重试尽 → na → partial_failed（na_case=1）；
  5. 桩断连触发 scanner 超时回收 → 短路 salvage、无 agent_score，未完成 case 回填 na（scanner 收尾主体验证）；
  6. error run pending 期间同 agent manual 可建（计数隔离）；manual 占满槽位时 error case 排队、不叠加超限并发（共享槽池，§6.2）；
  7. online fake 接收推送（`POST /backflow/regression-results`）：载荷含 run 终态、case pass/fail/na + na_case + per-case error_type 齐 → needs_review 素材齐；
  8.（v0.3）复现触发被测 agent 桩 5xx/崩溃 → 归 na（技术失败源）+ 独立熔断 key 不污染同 agent manual 判定；批量同 error_type na → online 聚合单条 review；
  9.（v0.3 → v0.4）坏终态（timeout）后同 version **新** manual 信号（未被 trigger_signal_id 消费）→ 重建；completed/partial_failed 后 → 不重建；晚到 active case 随下次发版信号纳入；
  10.（v0.4 K1/B1 吸收态）坏终态后**补偿对账复用同一锚**（最新 manual 已被 latest 消费）→ 不重建；模拟对账多周期 → 同一坏 run 只建一次，无自动重跑循环。
  11.（v0.5 C1 门禁）批 1→批 2 间隙存量 active error case 全断言空（backfill 未清零）+ 桩发首个新版本 manual → **不建空 run**（latest 保持 None）；backfill 清零后补偿对账周期建出含存量 case 的 run → 存量 case 在该 fix_version 判出终值；
  12.（v0.5）case 数超 cap → newest-active 截断、最老溢出 + run 记 case_truncated；claim 对象（最新 case）必进 run；manual 同 version 复测不重复建（§5.2 completed→return）；
  13.（v0.5 风险 17）**双向 mismatch**：a）online fake claim fix_version 填「人手 ≠ agent 自报」字面量（如尾随空格/`v` 前缀）→ offline 侧该版本无 run（期望行为确认，非 bug）；正确字面量经推送入站闭环。b）（v0.5.2 对称）offline manual run version 填「≠ agent 自报」字面量 → error run 建在错版本 → online 正确 fix_version 无推送入站 → 走 claim TTL 兜底（流程前提非契约缺陷）。
  14.（v0.6 R5 纯净 run）桩环境注入一次技术性 na（复现调用断连、RETRYABLE 重试尽 → 该 case 落 na，na_case>0）→ 该 run 混有干净 pass 行 + na 行 → **run 内 pass 行不置 verify passed** → online 该 case needs_review（reason `unclean_run`，带 run_id + error_type）；fail 行照旧即时 reopen；不纯净 run 不计 K 也不清零已确认序列——验证"环境污染不传导 fixed"；
  15.（v0.6 R6 跨版本稳定序列 K=2）桩"已修复" → claim fix_version 起连续 2 个纯净版本 run 均 pass → **第 2 版推送入站才置 verify passed（fixed），单 pass 不 fixed**；序列中途任一版本 fail → 即时 reopen；老 case 在后续版本被 newest-active-first 截断（case_truncated）→ 该版本缺行中断 K 观察 → 接受退化诚实欠测（不 auto-fixed，claim TTL 兜底）；
  16.（v0.6 R7 reentry 同版本禁 auto-fixed）已 fixed 的 (agent, version) 线上错误再现 → reentry 生成 generation>1 cluster → 用户 claim 填**同已完成版本** fix_version → 该 cluster 不用旧 run pass auto-fixed → 推送回传 pass 转 needs_review（reason `reentry_same_version`）；改 claim 新版本（修复真生效）→ 新版本走 R5/R6 正常判据 → fixed。
  17.（v0.7 R-3 洪峰 3 版连发差集补齐）桩连续发 v1/v2/v3 三版（v1 run pending 期间 v2/v3 各自到终态触发，被 §5.2 活跃闸=1 拒建）→ **补偿对账差集**（manual distinct version {v1,v2,v3} − 已有 error run version {v1}）逐周期补建 v2 → v3 → 最终 3 run 全建出；中间版 v2 claim fix_version=v2 → 推送入站命中 v2 run 判出终值（不再静默 14d TTL）；对账周期内观察每周期至多补 1 个（活跃闸）。
  ~~18.（v0.7 R-4 cap 溢出透出核对）高错误率 agent 激活数超 cap → newest-active-first 截断最老 case 溢出 → run 只读面返回**溢出 case 列表**（excluded_case_ids）→ online 回查该老 case 在 fix_version 版缺行且 ∈ 溢出列表 → claim 详情显「该版本窗口外欠测（cap 挤出）」非修复失败 → admin 人工 fixed 或 agent 错误量治理提示；不引入跨端保窗口（补建后老 case 是否重进后续 run 随 newest-active 序自然判定）。~~ **v1.23 取消**——理由 = 推送为全量对账，不存在「缺行→轮询」，亦不存在只读面（该用例不再执行）。
  ~~19.（v0.7 R-5 agent 已见版本读面）桩跑 manual v2.1 / v2.1.1 两版到终态 → 「agent 已见版本」只读面返回 distinct version 清单 + 最近终态时间；online claim 表单填 v2.1（已见）→ 无告警；填 `v2.10`（字面量未发布）→ **软校验告警**（展示已见版本，确认后才提交，非硬拦）；填 v3.0（正在跑首版尚未终态）→ 放行 + 「待 <v3.0> 回归 run」持续显示。~~ **v1.23 取消**——只读面整组取消，其守卫语义改由载荷水位字段 `agent_latest_version` 承载（该用例不再执行）。
  20.（v0.7 R-7 批量 requeue 重拉）多 link 因 online_content_gap（字段缺/空词表）invalidated → online admin 修正现场后**批量 requeue** → offline §6.5 逐 payload 重拉重处理（覆盖 envelope，manual_invalidate 语义不变）→ 补齐项重新建 case + ack active；「版本不识别」行被可愈性标注禁勾（需强确认），不误重推。
  21.（v0.7 R-8 cap_gap 探测态无重复 ack）cap_gap rejected 行首次 reject → invalidated ack 到 online（幂等 200）闭环后进入「等待接入」探测态 → 映射持续缺期间模拟多轮每小时自愈 → **无重复 invalidated 出站**（ack_status 不复位）；补 mapping 后下一轮本地重跑通过 → 建 case + **单次** ack active（复用 payload_id，契约 R2 `invalidated(offline_cap_gap)→active`）。
  22.（v0.7 R-10 截断复现如实判）error_cluster input_snapshot 超 8K 截断带 `input_truncated` 标记 → 复现 run 用截断输入打桩 → 复现 pass → verifier **如实判 pass**（na 语义不符——判定能且如实）；该 run 结果供 online 消费侧识别截断 → 不计 K → needs_review(reason=`input_truncated`)；后续小输入纯净版本正常攒 K auto-fixed（offline 侧零改动，本用例验证「如实判 + 标记透传」）。
- 批 1 环 1 fixture 复用作扩展；T-4.13/4.14 场景（词表边界、复现失败注入、并发、时间窗口）落地时按 task.md 约定回填 online detail §14。

### 11.3 测试后汇报

mvn 编译 → 上述矩阵单测 → 环 2 双端走查 → 逐项告知已验证/未验证；真实被测 agent 复现形态若与桩有差异如实标注。

---

## 12. 风险与开放项

| # | 风险/开放项 | 缓解/处置 |
|---|---|---|
| 1 | **依赖批 1 代码落地**（现零落地） | §10 依赖清单 = 实施顺序门；批 1 先实施再批 2 |
| 2 | **共享槽池跨进程前提（v0.4）**：多进程 worker 下进程内槽池不足，error 与 manual 复现可能跨进程叠加 | §6.2 前提标注；多进程则在 agent 行做轻量执行标记/乐观校验（实施前核对 worker 模型；默认单进程） |
| 3 | **自动触发依赖发版经 offline**（D3 边界）；fire-and-forget 丢触发 | 信号终态挂接 + §5.3 低频补偿对账（丢触发从静默永久降为 ≤1 补偿周期）；手动补建入口留二期（§5.4） |
| 4 | **na → needs_review 风暴 / 对被测 agent 的副作用**（error 复现是真实调用，重放曾出错的输入可能再次触发同类故障/崩溃，v0.3 容错 9 → v0.4 I-3） | RETRYABLE 重试 + 非重试直 na；error 复现独立熔断 key + 共享槽池收敛叠加负载；同 run 批量同根因 na 由 online **聚合单条 review**（§2.3/§6.4，B 包 B-4）；联调用例补「重放触发 5xx/崩溃」归属验证（§11.2 场景 8） |
| 5 | **keyword_not_contains 登记与 DB 行不同批上线** → registry load 启动抛错 | §7.4 同批核验点；激活器 DB op 校验前置 |
| 6 | **断言 KeyError 保守 fail 误伤**（输出结构异常判 fail） | D2 分层；executor 成功已保证 unified 含 answer，分支罕见 |
| 7 | **error run 结果行长期驻留膨胀**（version×active case 累积，复现输出 MEDIUMTEXT，v0.3 性能 4） | pinned 保留终态不落 run 清理；行数 = Σ(agent×发版版本×active case)，实施期核量级后定归档/截断阈值（复现原始输出按需截断，只保终值 + error_type；随批 1 风险 4 归档策略另定） |
| 8 | **存量 active error case（批 1→批 2 间隙）无断言** | §7.3 backfill 特权直写 + 幂等 + **§6.2/§7.5 加载过滤前置（空断言 case 不进 run、不落 na）+ §5.2 C1 创建前门禁（runnable 空不建空 run）** + §5.3 补偿对账直到清零；清零后门禁放行、同锚对账即建（不依赖下次发版） |
| 9 | 积压限速与 pull_loop 周期耦合（批 1 §6.2/风险 5） | 沿用同 agent 并发上限配置；error run 量级小 |
| 10 | **慢 agent 复现超时误杀**（error case 原始故障常是慢场景，缺省 120s 预算不足，v0.3 容错 4/性能 1） | error-run 专用 run_config（§6.1，按 adapter 上限冻结）+ hard_deadline 含**槽池排队等待**估值 / running 心跳豁免回收（§6.6，排队/执行期续租，防活 run 被误收） |
| 11 | **同 (agent,version) 多 run 的判定锚定歧义**（坏 run 重建/崩溃重跑产物，v0.3 容错 2） | 对表选取序：优先最近 completed；无 completed 取最近可判终态（§2.3），选取序并入推送入站判定（B-1(b)）。**false-fixed 收敛（v0.5 R4）**：v1 无离线迟到 run 覆写——若旧坏 run 判 pass 已 closed、后到好 run 判 fail，cluster 停在 false-fixed，由错误线上再现触发 reentry（§7.5 版本门控 → 新 cluster/新 case）或 admin 手动 reopen → 下轮 claim；不承诺不存在机制（B-2 撤销） |
| 12 | **error run 读面敏感**（input/snapshot 实文旁路 envelope 隐藏，v0.3 安全 3） | §9.4：普通读面排除 error run；快照 input 不进推送载荷/人类 read；排障走 admin/内部 API + 日志 |
| 13 | **error run 消费侧契约（已落字，风险解除）**：终态字面量/na 消费/聚合规则原为 offline 主张（solution_detail v1.2 零命中），2026-09-03 已按 B 包落 online `solution_detail` v1.3 + `solution.md` v3.5.3 | **已解除**：推送入站消费规则生效——只消费终态 run（status ∈ {completed, partial_failed, timeout, cancelled}，绝不含 running/pending → 分支 B premature-fixed 已堵）、na 行 → needs_review、缺行 ≠ na 按无终值处理（观察中断由载荷水位字段探测，→ 分支 A 静默 14d TTL 空转已堵）、同 run 同 error_type 聚合单条 needs_review_batch（B-1(a/b/c)/B-3/B-4 落点 + task.md 环 0 B-7 契约冻结）。残余 false-fixed/手误断链见风险 11/17 |
| 14 | **manual version 域放宽**（放 strict semver 后乱序/任意串进入信号面） | 共享域校验（≤64、非空、无空白）+ 同 agent 乱序仅告警不拒；字符串命中靠推送入站闭环兜底（§2.4） |
| 15 | **容量欠跑口径 + 分母守卫驻留**（cap 截断 case 该版不判；分母只增不减） | v1 裁决（R3，§6.1 注 1/2）：**newest-active-first 截断**保证 claim 对象（最新错误）必进 run → 欠跑 = 窗口外**老哨兵**；老哨兵无 claim 盯着（reentry 产新 case 不复活老 case，§2.3 截断标记）→ 欠测不产生 TTL 链，**代价 = 窗口外老错误的回归验证放弃**（该错误再现时以 reentry 新 case 进 run）→ 诚实声明。截断 run 记 `case_truncated` 不伪装全判（§2.3）。DB 单调增长（error case/run/result）与归档/去重列二期（B-9，风险 7） |
| 16 | **needs_review 聚合后人工闭环**（case 引用列表的批量复核/重测入口缺失） | 二期（见开放项）：聚合单条 + 引用列表 + 批量处理入口 |
| 17 | **版本字面量域把断链风险转嫁给人工 fix_version**（v0.5，容错 #7）：online claim fix_version 是 viewer 手工输入自由文本，任何「人手 ≠ agent 自报」（大小写/v 前缀/尾随空格/r47 vs 47）在字面量域内合法但不等值 → offline 永不建该版本 run → 无推送入站 → 静默 14d TTL。**对称源（v0.5.2 补）**：offline 触发侧 manual/held_out run 的 version 由发版登记方（流水线/人工）输入，同样可「≠ agent 自报」→ error run 建在错版本 → online 正确 fix_version 无推送入站 → 同一 TTL 兜底，属流程前提非契约缺陷（发版方应以被测 agent 自报版本原样登记） | §2.4 + B-5：claim 表单候选提示/归一化（offline 已见版本下拉、trim/大小写归一）属 online 交互建议；环 2 补双向 mismatch 用例（§11.2 场景 13） |
| 18 | **纯净 run 前提放大 needs_review 面（v0.6 R5）**：含技术性 na 的 run 内 pass 行不置 verify passed → needs_review（`unclean_run`）；被测 agent 环境不稳（技术性 na 频繁）时，假 fixed 收敛为 review 量膨胀 | 判据正确性优先——不可靠复现环境的 pass 不可信，污染不得传导进 fixed；`unclean_run` reason 带 run_id + error_type 便于定位根因；不纯净 run 不计 K 不清零（不推进亦不推翻已确认序列）；技术性 na 面已收窄（素材缺失源加载过滤前置不可达，§6.4）+ B-4 同 run 同 error_type 聚合缓解条目膨胀 |
| 19 | **K 序列 fixed 到达延迟与窗口截断中断（v0.6 R6）**：auto-fixed 需自 claim fix_version 起连续 K 个纯净可判版本 run 无 fail——低发版频率 agent 的 fixed 确认随版本节奏延迟（每个版本间隔为一观察窗）；老 case 在 newest-active-first 截断下某版本缺行 → 中断 K | K 默认可配（=1 兼容现行为）；单 pass 判 fixed 的即时性换为独立版本样本稀释失真假 fixed（延迟代价换取正确性）；中断场景接受退化诚实欠测（`case_truncated` 区分，§2.3/风险 15）；claim TTL 兜底仍在——缺行中断观察不终止等待，不无限期挂起 |
| 20 | **reentry 同版本收敛依赖人工/升版（v0.6 R7）**：generation>1 cluster claim 填同已完成版本 → 禁 auto-fixed（`reentry_same_version`）；agent 实已修复但用户持续同版本 reentry claim → 该 cluster 卡人工复核/升版 | 正确性优先——同版本旧 run 为修复前证据，用其 auto-fixed 必假；reason 明确提示人工复核或改填新版本（走 R5/R6 正常判据）；§7.5 版本门控同日不等值走 count+1 新 case 路径，与同版本 reentry 语义区分（B-12） |

**开放项（二期）**：显式补建/终态重建入口（§5.4）；**partial_failed（技术 na>0）→ 同 (agent,version) 带退避自动重建**（v0.3 容错 8 提为 v1 内待办；v0.4 consumed 吸收态机制已就绪——二期引入「技术 na 重建」时以**新 trigger_signal_id + 退避窗口**限定，防与补偿对账复合成循环；online claim TTL 兜底文案中显式提示）；**needs_review 聚合后的人工闭环**（case 引用列表批量复核/重测入口，风险 16）；error case 过期/下架运营（批 1 风险 4）；传输 Kafka（批 1 风险 7）；`inconclusive` 三值；多进程 worker 下共享槽池的 DB 侧扩展；error case invalidate 放开时的实跑集语义重审视（§6.3 依赖不变式）；error_type 分类值演进需跨端登记（推送载荷/聚合规则随值域扩展同步，§9.3/B 包）。

---

## 13. 修订记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v0.1 | 2026-09-03 | 首稿：裁决 2Q1-2Q4 固化（D1-D7），执行语义/自动触发/判定器/激活/登记/隔离/测试 |
| v0.2 | 2026-09-03 | 双独立评审（逻辑/契约 + 现码事实）全量修入：批 1 依赖标注化 + 依赖清单；互斥计数隔离（B1）；执行期 agent 并发闸（B2/R2）；scanner salvage 短路（B3）；独立收尾 + na_case 列口径（B4/I1/M4）；version 断链解除（R1）；断言新调用点 + 登记降格（M1/S1）；激活 backfill + 空断言 na（I2）；rerun 拒绝依赖（M5）；probe 跳过（M3）；na 非重试类矩阵；脏数据防线（D8/A3）；dashboard 口径统一（A1）；case_ids 漂移语义（A2）。待用户终审 |
| v0.3 | 2026-09-03 | 第二轮四方向独立评审（性能/安全/容错/逻辑复核）全量修入：熔断隔离 + 调度层未执行归 na + error 分支不走默认 error 落库（容错 B1/性能 3/安全）；执行预算冻结 + hard_deadline 闸等待处置（性能 1/容错 4）；并发闸语义修正（不压 manual per_agent_concurrency=3）+ manual/held_out 接入排他窗（逻辑 W2/性能 2）；error run 终态→online 对表 + 同 version 多 run 选取序（逻辑 W3/容错 2）；触发信号终态前置 + skip 规则完备化 + 洪峰配额 + 低频补偿对账（逻辑 W4/安全/容错 3/6）；na 定义统一三层源 + error_type 固定值（逻辑 W1）；scanner 替代收尾动作 + pending 租约回收（容错 1/安全）；na 回填同事务约束 + uk_result 兜底（容错 5）；backfill 特权直写绕 403 + 幂等可重跑（逻辑 W5）；词表词级净化（安全 4）；人类读面隔离 + 快照 input 裁剪 + 排障路径（安全 3/逻辑 S2）；suite-agent D8 归属校验 + invalidate 不变式声明（安全/容错 7）；跨文档同步项入 §10；测试矩阵扩展 + 风险表补 5 行。待用户终审 |
| v0.4 | 2026-09-03 | 第三轮四方向独立评审（契约对接/逻辑自洽/双端容错/全链路负载）全量修入：**R1 收缩为跨端版本字面量域**（§2.4 新增，manual version 校验放宽，D-14 以共享域形态恢复）；**R2 并发闸改共享 per-agent 执行槽池、废弃 run 级排他窗**（§2.2/D7/§6.2，I4/Z2 双向饿死 + A 契约断链核）；**补偿对账 consumed 吸收态**（trigger_signal_id，K1/B1 无限重建收敛）；**§2.3 对表重构**——na/缺行拆解（B2）、needs_review 触发域校准（A3/A5：claim TTL 兜底替代「超时→needs_review」误标）、批量同根因 na 聚合注（I3）、supersede 覆写（A7）、标注为 offline 主张契约；**素材缺失加载过滤前置**（I2，partial_failed 素材假终态钉死解除，§6.2/§6.4/§7.5）；**error_type 进平台只读面**（per-case na 必带，§9.3）+ 人类读面三面分级收口（I3/S2）；**容量双锁** run 级 case 上限 + 分母治理（D-1/Z3/I-1，§6.1 注）；**B 包 = 对 online solution_detail/task.md 契约修订清单 B-1~B-8**（A1-A9 归并，§10）；跨文档同步项③改 D-14 恢复；测试矩阵/环 2 场景/风险表补至 16 行/开放项同步。待用户终审 |
| v0.5 | 2026-09-03 | 第四轮四方向独立评审（v0.4 新结构 + 契约对接，负载/契约/容错/逻辑）全量修入：**裁决 R3 分母治理 = 守卫驻留 + 诚实欠测**（v1 不引入跨端 deactivate，§6.1 注 1/2 重写：cap 从执行预算反推 + **newest-active-first 截断**消除「最新错误永不判」洞 + `case_truncated` 诚实标记 + 溢出 = 窗口外老哨兵欠测入风险 15；B-9 = 跨端 case 归档二期预留）；**裁决 R4 放弃离线触发覆写**（§2.3，v0.4 A7「复用既有 supersede+reopen」核为不成立 → false-fixed 收敛 reentry/admin reopen，B-2 撤销、风险 11 更新）；**C1 创建前门禁**（runnable 空不建 run，容错 #6/逻辑 #3 空跑 vacuous completed 毒化 (agent,version) 可判终态位）；**B 包重构**（B-1 拆 a/b/c 三条 + status 值集排除 running/pending、B-3 同步二期标注、B-4 聚合载体、B-5 扩 §7.5 版本门控序语义 + claim 表单候选提示、B-6 修 §5.1⑥ run_status 值域、B-8 移出归批 1 同步项）；**C4 锁/并发**（§5.2 读 latest 后移取 agent 行锁内 + 锁序约定、§5.3 挂接点挪 _finish 事务提交后）；**running 心跳判据**（逐 case/排队期续租、僵尸 running 回收，逻辑 #2/容错 #5，§6.6）；**终态写守卫**（§6.3，迟到 EvalResult 拒写）；**na 第五源 scheduler_unexecuted + scanner 回填对象界定**（§6.4）；风险 13 落字前空转如实登记、新增风险 17（fix_version 手工输入）；测试矩阵/环 2 场景/修订记录/附录 A 同步。待用户终审 |
| v0.5.1 | 2026-09-03 | **对接核查小结并入（非评审修订，v0.5 基础上增量）**：三卡对表（online v1.2 / 批 1 v0.2.1 / 本稿）逐缝核清——**S1** 判定消费语义未落 = B 包本体（风险 13 维持）；**S2 澄清** auto_regression=closed_by 标签、判定单链无两源歧义；**S3 澄清** 断言构造已在 D1/§7.3 + 存量 backfill（批 1 三列可空由 D1 收窄，无空洞）；**S4 裁决** reentry 版本门控序语义 = 等值+日期前缀门控（§2.4 细化 + B-5 落字语义）；**S5** 发版=manual/held_out run 硬前提并入 B-7 环 2 用例；**S6** run 级超时批量 na 呈现并入 B-4 设计注。待用户终审 |
| v0.5.2 | 2026-09-03 | **落字回执（P0）+ 措辞/落点收口（P1），非评审修订**：online B 包已按本稿落 `solution_detail.md` v1.3（A1-A7 + 三处排查补正：needs_review_batch 载体定稿 + 批量端点/整批原子 + reentry 同日 count+1 可见性提示）+ `solution.md` v3.5.3 + `task.md` 环 0 B-7——全文「现文 v1.2/仅 offline 主张/需确认后落」框架回正（文件头契约行 →v1.3、§2.3 注、§10 B 包 intro + 新增落字回执 blockquote、B-3/B-4 行、风险 13 解除）；P1：na 源计数统一 5 源矩阵（§2.1/§2.3 L88/D2/§6.4 标题与矩阵头/§11.1 回改）、§5.2 洪峰收敛补创建侧校验落点（内部创建器取 agent 行锁后查该 agent 活跃 error run 数）、§6.3 终态写守卫补收尾同事务顺序（先插 na 后置终态）；**终审再修（1 P1 + 4 P2，仍 v0.5.2）**：P1 心跳续租粒度钉死（error 分支固定时间片 ≪ lease TTL 续租、禁仅逐 case，§6.6/§11.1 + 用例）；在途判定丢失显式声明（§6.3：宁 na 不半终态、`scheduler_unexecuted` 语义放宽为「未获可判终值」）；§11.1 na 矩阵镜像 §6.4 五源逐源一例（词表净化单列非源边界）；§10 小结块补快照注（落字前快照，终态以落字回执为准）；offline 触发侧断链对称声明（风险 17 行 + §11.2 场景 13 双向 mismatch）。待用户终审 |
| v0.6 | 2026-09-03 | **数据流三案裁决（R5-R7，方向性契约修订，用户确认后落稿）**：完整捋 online→offline 数据流后挑战其最薄弱 3 点，收敛为 **auto-fixed 判据重构 = 纯净 run + 跨版本稳定序列**（§2.3 v0.6 注）——**R5 纯净 run 前提**（含技术性 na 的 run 内 pass 不置 verify passed → needs_review `unclean_run`；fail 照旧 reopen；offline 零改动，na/error_type 已透出）；**R6 跨版本稳定序列 K=2**（自 claim fix_version 起连续 K 个纯净可判版本 run 无 fail 才 fixed；fail 即时 reopen；缺行中断观察；载体 = 既有 verify_run_record 逐版本时间线，消费侧改判定，不加表；失真假 fixed 随独立版本样本稀释；老 case 窗口截断中断 K → 接受退化诚实欠测，`case_truncated` 区分）；**R7 reentry 同版本禁 auto-fixed**（generation>1 cluster claim 同已完成版本 → 旧 run pass 转 needs_review `reentry_same_version`，不放开 completed 不重建守卫）；B 包扩展 **B-10~B-12 待落字 online detail v1.4**；风险表补 R5/R6/R7 行；§11.2 补场景 14-16。待用户终审 |
| v0.6.1 | 2026-09-07 | **终审通过（v0.6）并补 3 处消费语义定义（非评审修订，v0.6 基础上增量）**：R5-R7 由「方向性登记」精化为 **online 落字规格**——① 聚合与 reason 值域（na 行沿用 batch；`unclean_run` 同 run 成片 pass 聚合单条 batch；`reentry_same_version` claim 级单条不产 batch；reason 值域随 v1.4 登记 detail §1.4/§7.6/§12.1）；② 纯净可判判据（终态 run + `na_case==0`，消费侧从 na_case 派生、不加纯净性列/offline 字段）；③ K-TTL 收敛终点（K 满 → fixed TTL 停钟 / claim TTL 超窗 → 回退 open，K 观察不延长 TTL，无无限期挂起）。三定义全文见 §2.3 v0.6 注 ①②③ + §10 intro。**2026-09-07 已落字 online `solution_detail.md` v1.4 + `solution.md` v3.5.4 + `task.md` 环 0 登记**；offline 侧运行机制零改动 |
| v0.6.2 | 2026-09-07 | **修订包 R-1/R-2/R-12 落字（2026-09-07 逐问评审拍板；online 已落字 detail v1.5 + solution v3.5.5，offline 运行机制零改动、R-12 代码变更单独立项）**：**R-1** auto-fixed 判据 K 由全局默认改 **claim 级固化 `claim_k`**（§2.3 v0.6.2 注 ①，detail §7.6 判定语义 v1.5；载体 = verify_run_record 逐版本时间线不变）；**R-2** 纯净判据由「run 级 `na_case==0`」下钻「**claimed case 行 + na error_type 影响域分流**」——**§6.4 矩阵新增「影响域（环境/case）」权威标注列**（online 按标注归类、无标注新 error_type 默认从严 → unclean_run + 告警），unclean_run 批纯化 + 撞键裁定收窄（纯 na cluster 退批走 cluster 级单点）；**R-12** `KeywordNotContainsOp` **strip 后空串/纯空白应答 → 保守 fail** 修补决策落 §7.2 注 + §11.1 单测矩阵补「空串/纯空白应答 → fail」行——**共享算子微改属 offline 机制微改、代码变更单独立项**（本稿仅决策与测试登记） |
| v0.7 | 2026-09-07 | **修订包 R-3~R-10 落字（2026-09-07 逐问评审拍板；online 已落字 `solution_detail.md` v1.6 + `solution.md` v3.5.6）**——本批 = 方向性契约修订 + **offline 机制改动混合包**（突破「offline 零机制」项与 R-12 同类：本稿决策 + 规格登记、机制代码单独立项、phase1 稿保持基线）：**R-3 对账版本差集补齐**（§5.3 v0.7 落字块：单最新锚 → agent manual/held_out distinct version − 已有 error run version 差集逐版补建，中间版不再永久丢；§5.2 措辞对齐）；**R-4 cap 溢出 case 只读面透出**（§9.3：case_truncated 计数 → 溢出 case 列表，online 回查欠测诊断）；**R-5「agent 已见版本」只读面**（§9.3：manual/held_out distinct version + 最近终态时间，claim 软校验数据源）；**R-8 cap_gap 探测态节流**（phase1 §6.5 现稿行为修正：invalidated ack 只发一次 + 本地探测补齐，L359 判据收窄至 ack_status ∈ {none,pending} 或 requeue 覆盖；本稿 §2.3 v0.7 注④决策登记，phase1 注记随代码单独立项）；**R-10 input 截断复现边界注记**（§2.3 v0.7 注⑤：offline 不截断不改写、截断复现 pass 如实判，防护在 online 消费侧 reason `input_truncated` 3→4）；§2.3 v0.7 注（R-3~R-11 修订全景）+ §10 v0.7 落字回执块 + §11.2 场景 17-22（R-11 环 2 用例并入；R-6/R-9 online-only 场景归 detail v1.6 §14）。待用户终审 |
| v0.7.1 | 2026-09-07 | **高危 2 条 R-18/R-19 落字（2026-09-07 逐问评审拍板；online 已落字 `solution_detail.md` v1.7 + `solution.md` v3.5.7；offline 运行机制零改动）**——**R-19 error_type 判据字面量错位 + 漏项修正**（本稿核心落点）：§6.4 矩阵行 1 短名 `connect/sse_parse/http` → executor 真值 `connect_error/sse_parse_error/http_error`（executor.py L38 RETRYABLE_ERRORS）；矩阵行 2 + §11.1 镜像补入漏项 `no_usage`/`contract_error`（executor.py L23/L31，非 RETRYABLE → 直接 na）；**落字后全表清点再追改一处遗漏 = §2.3 v0.6.2 注② 内嵌 case 级清单**（同为现行判据正文、初稿仍短名且漏二项，2026-09-07 收口改真值 + 补漏项，见 §2.3 v0.7.1 注①）；case 级清单与 executor 真值同域，从严兜底回归「仅兜底非常态」——防短名错位被 online 当「未知 error_type → 默认从严环境级」系统性误触发 unclean_run（§2.3 v0.7.1 注① + §6.4）；**R-18 input 明文载体 offline 零改动确认**（§2.3 v0.7.1 注②：online 判定态增列 input_snapshot_clean/input_truncated，D19 evidence.input ≤8K 截断快照边界不变，offline 无新字段、error run 复现如实判） |
| v0.7.2 | 2026-09-07 | **修订包 R-20/R-22/R-23 落字（2026-09-07 逐问评审拍板；online 已落字 `solution_detail.md` v1.9 + `solution.md` v3.5.9；offline 运行机制零改动、仅收尾语义钉死 + 护栏登记）**：**R-22** 收尾回填 na 统一钉死 `scheduler_unexecuted`（§6.3 v0.7.2 注：scanner 回收 / orchestrator cancel / run 级超时收尾三路径回填 na 同源同 error_type；§6.4 源行补注；「na case 必带 error_type」不变量全覆盖收尾路径；timeout 不作收尾回填原因）；**R-23** timeout 三层语义分界（§2.3 v0.7.2 注③：case 级 na 源 / run 终态 timeout 半途中断不直接触发 needs_review、claim TTL 兜底 / run 级超时收尾回填 na 环境级 → 关联 pass case 入 unclean_run 批；「run 超时非源」不排除环境级回填污染的间接作用）；**R-20** 复现 run 空答 fail 语义定性 = leakage 空话术复发证据 + pre-scan 显式立项 Phase B code_detail 实施门禁（§7.2 R-20 注：R-12 core 修补不动、空答不落 na 落 verifier fail、现网 keyword_* 空字段依赖 pre-scan 报告 + 合法空答豁免白名单）；§11.1 补收尾 error_type 护栏断言 + §10 v0.7.2 落字 blockquote |
| v0.8.0 | 2026-09-11 | **v1.23 契约反转回填（T-3.8 批 2b；纯文档口径回填、offline 运行机制零改动）**：结果回传方向反转——offline **主动推送** `POST /backflow/regression-results`、online 零 outbound（收单仍是 offline 主动拉 online）。**§9.3 整节改写**（原「平台只读面」→「**推送载荷与出站行为**」，八项新增定义：端点 / 触发时机 = 终态 commit 后异步 / 鉴权 = 静态预共享 secret（无 scope 分置、无 JWT）/ 失败语义 = 超时 5s·重试 3 次·全败记 error 不阻塞收尾 / 载荷 schema（含两水位字段 **agent 级口径硬约束**）/ 响应字段 `links_advanced`·`cases_dropped` 口径 / 拒单规则（`case_id` 重复、`finished_ts` 非法 ISO8601）/ 幂等键 `uk_verify_run`）；**§6.3 收尾表新增「推送」行** + 推送时机注；**§9.4 三处载体同步**（人类读面收为两面分级、推送载荷不含 input 实文）；**R-4 `excluded_case_ids` / R-5「agent 已见版本」只读面整组取消**（`na_case` + per-case `error_type` **非废弃**，载体改为载荷 `cases[]`，online 消费语义不变）。**规格外补改 2 处**：文首契约锚 `solution_detail.md` v1.9 → v1.23（§1 契约行）。 |

---

## 附录 A：裁决记录（traceability）

| 裁决 | 结论 | 固化决策 | 状态 |
|---|---|---|---|
| 2Q1 判分机制 | 复用断言引擎 | D1 + §7.2/7.3 | v0.1 并入 |
| 2Q2 复现失败 | 无终值 na | D2 + §7.1/6.3 | v0.1 并入 |
| 2Q3 run 触发 | 版本变更自动触发 | D3 + §5 | v0.1 并入 |
| 2Q4 执行范围 | 单 fix 版 | D4 + §7 | v0.1 并入 |
| R1 版本体系 | 两平台共享**版本字面量域**，字符串相等锚定（v0.4 收缩，非 semver） | D3 + §2.4 + §6.1 | v0.2 并入，v0.4 收缩 |
| R2 并发防线 | 执行期 agent 并发闸 = **共享 per-agent 执行槽池**（v0.4 语义修正，废弃 run 级排他窗） | D7 + §2.2 + §6.2 | v0.2 并入，v0.4 语义修正 |
| R3 分母治理（v0.5 第四轮裁决） | **守卫驻留 + 诚实欠测**：v1 不引入跨端 deactivate，error case active 分母单调入风险表；回归成本 = run 级窗口固定（cap 反推 + newest-active-first 截断 + case_truncated 诚实标记）；跨端 case 归档 = 二期（B-9） | §6.1 注 1/2 + §2.3 + 风险 15 + B-9 | v0.5 并入 |
| R4 离线触发覆写（v0.5 第四轮裁决） | **v1 放弃**：迟到 error run 不覆写 online 已 fixed 判定（终态只读），false-fixed 收敛 reentry/admin reopen/下轮 claim | §2.3 + 风险 11 + B-2 撤销 | v0.5 并入 |
| 对接核查 S4 reentry 门控序语义（v0.5.1，2026-09-03） | **等值+日期前缀门控**：同日期前缀（YYYY.MM.DD）仅完全等值放行 reentry、同日不等值按上线中 count+1、跨日按日期前缀字符串序；非日期形态回退等值；不解析 tag 数值（字面量域裁决保持） | §2.4 + B-5 | v0.5.1 并入 |
| R5 auto-fixed 判据重构（v0.6 数据流三案） | **纯净 run 前提**：auto-fixed 只接受 run 级 0 技术性 na 的 run；含技术性 na 的 run 内 pass 行不置 verify passed → needs_review（`unclean_run`），fail 照旧即时 reopen，不纯净 run 不计 K 不清零（复现环境不可靠时 pass 不可信，污染不得传导） | §2.3 v0.6 注 + B-10 | v0.6 并入 |
| R6 auto-fixed 稳定序列（v0.6 数据流三案） | **跨版本稳定序列 K=2（默认可配）**：case 自 claim fix_version 起连续 K 个纯净可判版本 run 均无 fail 才 verify passed；fail 任何版本即时 reopen；缺行≠na 中断观察；载体 = 既有 verify_run_record，不加表不加字段（单次 pass 是弱证据，K 个独立版本样本稀释失真假 fixed）；**老 case 窗口截断中断 K → 接受退化诚实欠测**（不引入跨端保窗口机制，`case_truncated` 区分） | §2.3 v0.6 注 + B-11 | v0.6 并入 |
| R7 reentry 同版本复验（v0.6 数据流三案） | **reentry 同版本禁 auto-fixed**：generation>1 cluster（reentry 产物）claim 填 fix_version = 该 (agent,version) 已有 completed run 的版本 → 该 cluster 不得用旧 run（修复前证据）pass auto-fixed → 推送回传 pass 转 needs_review（`reentry_same_version`，人工/升版收敛）；「completed 不重建」守卫不放开（同版复验重建会振荡，动 consumed 锚成本高收益低） | §2.3 v0.6 注 + B-12 | v0.6 并入 |

（引用批 1 附录 A 裁定 Q3 = CaseVersion 延批 2 → 本稿 D6 结论维持惰性不预建。）
