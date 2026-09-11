# 线上观测平台 error 回流 —— offline 侧配套改造方案（批 1：契约先行 + 收单就位）

> 仓库：`agent-evaluation-offline`（线下评测平台；本平台对线上观测平台 `agent-evaluation-online` 的角色 = **判定器 / 复现方**）
> 依据：online 仓 `solution_detail.md` v1.23 §7（平台间契约与复验闭环 D19/D20）+ §8.7/§8.8（平台间端点与鉴权）+ §13.5（平台间审计）。
> 状态：批 1 方案 **v0.3.0（v0.2 四方向独立评审修入后定稿；v0.2.1 契约修订 R1~R3 落改确认、v0.2.2 code_detail 实施转译注记**（均不改方案语义）**；v0.2.3 = 平台间契约方向反转同步**（online `solution_detail.md` v1.23），**v0.2.3 改方案语义；v0.3.0 = 第 2 刀 + 第 3 刀回填 + 术语/锚点收口**，均见文末修订记录）。批 2（判定器 executor 复现 + verifier no_fallback）另出方案，本文件只划边界、不实现。
> 评审（2026-09-03）：A 逻辑与模型贴合 / B 契约贴合 / C 安全与容错 / D 范围与测试 四路独立评审结论已全量修入；**契约修订包 R1~R3 已拍板接受、入环 0 对表**（online solution_detail 修订，offline 本方案按其语义实现并在相关节显式标注依赖）。**R1~R3 已于 2026-09-03 落 online `solution_detail.md` v1.2**（§7.2/§7.3/§8.7/§8.9 + 修订记录），本方案相关实现依赖已解除。
> 关联：online 仓 `task.md`「联调闭环编排（环 0~3）」——批 1 覆盖环 0/环 1 offline 侧；环 2 双端集成、环 3 真实灰度在批 2 及后续。

---

## 0. 定位与切刀背景（读前须知）

Task #4 已切两刀（2026-09-03 拍板）：

| 批 | 范围 | 联调环 | 判定 |
|---|---|---|---|
| **批 1（本文件）** | error 回流收单（pull_loop + 结构自检 + 落库 + 激活 + ack）+ ~~平台只读面~~（**v0.2.3 取消**）+ 服务凭证 + 隔离/防污染 | 环 0 契约冻结 / 环 1 单端 stub | **不产真实判定**（用测试 fixture 通管道，见 §12） |
| 批 2（另出） | 判定器 executor 复现 + verifier no_fallback + error_regression run 执行语义 | 环 2 双端集成 | 真实 pass_fail（= online task.md T-3.8 落点） |

**传输通道结论（2026-09-03 决策）**：error payload 从 online 到 offline **保持 HTTP pull**（offline 主动拉，契约 §7.2），不引入 Kafka。核心理由：① pull 语义下 payload 的生命周期锚定在 **online 自己 DB（assembled 态持久）**，offline 宕机只影响"拉取时效"，online 侧数据一条不少、恢复即续拉——可靠性反而强于 broker retention 有超窗风险的消息模型；② ack 是**业务级结论**（active/draft/invalidated + case_id + reason 码 + 前置矩阵 + 幂等），Kafka offset 是传输级确认承载不了，改 Kafka 只能替换半程、双端机制并存更复杂；③ 产率（online 聚类）与消费率（offline executor 复现，秒~分级慢操作）两端都低，传输不会成瓶颈。本文件不重新论证，仅在 §13 风险节留一句回溯。

---

## 1. 背景、范围与职责边界

### 1.1 定位澄清

online 与 offline 是**两个独立平台、两套语义**（2026-09-03 校正）：
- online = **线上观测平台**：观测 agent 线上行为、聚类 error、组装 payload、claim 修复版本、**接收回归结果推送并判定**（v0.2.3：原「回查验证」改为被动接收，**online 不主动调用本平台任何接口**）。
- offline = **线下评测平台**：评测被测 agent 的接口能力；对 error 回流链路，它承担"**收单 + 就位 +（批 2）复现判定**"。

error 回流闭环一句概括（online 视角）：**线上发现可判定的回归错误 → 把错误现场固化为 case → offline 把它收纳为被测 agent 的"回归哨兵用例" → agent 发布声称修复的版本后，offline 对该版本跑回归（含哨兵）→ offline 把哨兵判定结果主动推给 online，online 判定并按 fix_version 确认修复闭环。**（v0.2.3：末段由「online 主动回查」改为「offline 主动推、online 被动收」。）

### 1.2 批 1 范围（本文件交付）

1. **pull_loop**：仿 scanner_loop 的后台循环，定时主动向 online 拉取 assembled error payload（offline→online HTTP，契约 §8.7）。
2. **结构自检纯函数**：对 D19 信封做必填/可空校验 + 空词表 fail-closed 判定。
3. **收单落库**：error case 收纳为被测 agent 的 error suite 哨兵用例（扩 TestCase + 独立 error suite + 收单记忆表）。
4. **激活与 ack**：收单即激活（建 active case）→ 回写 online `offline_status=active`（带 case_id，幂等）；失败驳回回写 `invalidated`（带结构化 reason）。
5. ~~**平台只读面**：`/api/v1/runs` 与 `/api/v1/runs/{id}/results`（online→offline 回查，契约 §8.7 第二组，D4）。~~ **v0.2.3 取消**——online 不再回查；替代 = §2.4 出站 `POST /backflow/regression-results` 结果推送（**批 2 落点**，非批 1 交付物）。
6. **服务凭证机制**：平台间双向认证的 offline 侧实现（D5）。
7. **隔离/防污染**：error case 不进入 manual run 评测、不进入标注/统计口径、不污染普通 suite（C5）。
8. 判定 stub 只存在于**测试基建**（fixture + fake-online），不进入生产路径（C3）。

### 1.3 批 1 不做（边界，批 2 / 后续）

- executor 复现被测 agent、verifier no_fallback 判分（批 2）。
- `error_regression` run 的**执行语义**：免互斥槽 / 不经 SCORING / 不产 agent_score / 独立保留档（批 2，契约 §7.3 #3/#4）。批 1 只做模型与加载隔离预留。
- `claim → 接收结果推送 → fixed` 的 online 侧流转验证（环 2/批 2）。
- error case 的长期驻留/过期运营策略（批 2 及后续，风险节登记）。
- 前端完整改版（error suite 前端只读标注与隐藏为批 1 收尾小改，见 §5.4）。

### 1.4 决策来源与本文件可追溯性

本文件的设计决策全部来自拍板记录：挑战修正点 C1~C5（2026-09-03 整体 challenge）+ 决策点 D1~D5（逐个拍板）+ 传输通道再审视（保 HTTP pull）。附录 A 有决策记录表。正文按可执行形态落笔，不再出现"待定"；独立评审后如需修订，走修订记录。

---

## 2. 契约锚点（只读引用 online `solution_detail.md`，offline 侧实现对象）

> 本平台不是契约制定方。以下为 offline 侧必须贴合的实现约束，任何字段语义以 online 文档为准。
>
> **契约修订包 R1~R3（2026-09-03 拍板接受，入环 0 对表）**：本方案有三处实现依赖 online `solution_detail.md` 修订；**已于 2026-09-03 落 online `solution_detail.md` v1.2**（修订记录 + §7.2/§7.3/§8.7/§8.9），以下实现依赖已解除——
> - **R1**：pull 响应每条 payload 附 `assembled_ts`（§2.2/§6.2，offline 增量锚唯一可靠来源）；online 落点：detail §7.2/§8.7 `pull/payloads` 响应元素。
> - **R2**：ack 前置矩阵补 `invalidated(offline_cap_gap) → active` 例外（§2.3/§6.5，§7.3「激活」行本支持，v1.1 矩阵文本修订遗漏）；online 落点：detail §7.3/§8.7 ack 前置矩阵。
> - **R3**：ack 400（`ERR_CLUSTER_0003`）响应带当前 `offline_status` + `invalidate_reason`（§6.6，offline 对账 manual-invalidate 竞态需要）；online 落点：detail §8.7/§8.9。

### 2.1 D19 统一 case payload 信封（§7.1）

信封分区与 v1 规则（关键列）：

| 分区 | 字段 | v1 规则（offline 关注点） |
|---|---|---|
| 信封元数据 | `schema_version` | `"1.0"`；非 1.0 → 自检不过 |
| | `case_type` | `"regression_error"`（v1 白名单唯一值） |
| | `payload_id` | uuid4（**幂等键**；offline 全程以它去重） |
| | `source` | `{agent, interface, trace_id, cluster_id, generation}` |
| | `versions` | `{trigger_version, fix_version}`——`fix_version` claim 前组装**恒空**，**不得因此驳回** |
| 通用 evidence | `input` | 快照 input 实文（脱敏 ≤8K）；error 型仅当前请求 input_turns。**必填** |
| | `output` | 代表事件 output 摘要；body 采集默认关**恒 null，不得因 null 驳回** |
| | `session_snapshot`/`retrieve_hit` | **二期恒 null**；不得误判有数据 |
| assert | `assert.no_fallback.config_ref.wordlist_version` | 与 `no_fallback_config.wordlist_version` 同刻固化、**同源同值**；不一致 → 自检不过 |
| assert 区附加 | `no_fallback_config` | `{words:[...], wordlist_version:N}`，随 payload 固化。**空词表/未配置 → 自检不过**（v1 fail-closed，不静默 pass）；offline 判分以 payload 内这份为准，改词表不 retroactive |

**结构自检必填/可空清单（offline 照做，§7.1 注）**：

- 必填：`schema_version / case_type / payload_id / source.agent / source.interface / source.trace_id / versions.trigger_version / evidence.input / assert.no_fallback.config_ref.wordlist_version / no_fallback_config`
- 可空（**缺省/空不得驳回**）：`versions.fix_version`、`evidence.output`、`evidence.session_snapshot`、`evidence.retrieve_hit`、`source.cluster_id`、`source.generation`

信封 JSON 完整样例见 online `solution_detail.md` §7.1（本文件不复制，避免双份漂移；实现与测试从 online 文档取）。

### 2.2 pull 语义（§7.2，offline 侧行为约束）

- online **不 push、不设调度/时钟**；offline 定时来拉。
- pull-API 返回按 `assembled_ts` 升序 + `next_token` 分页游标；offline 崩溃重拉以 `payload_id` upsert 幂等去重。
- **requeue 刷新 `assembled_ts=now()`** → 重推案重新进入增量拉取范围；offline 收到已在案（曾 rejected）的 payload_id 时须进入"重处理"而非"去重忽略"（§6.5）。
- 恢复后积压限速/新鲜度窗口属 **offline 拉取侧行为**（本方案 §6.2）。
- **R1（契约修订，§2 顶部）**：pull 响应需为每条 payload 附 `assembled_ts`（online 时钟）。增量锚必须在 online 时钟域——offline 本地时钟 skew 会导致漏拉/漏 requeue（评审四家独立命中），现响应仅 `{payloads, next_token}` 无时间戳，offline 无数据可锚。§6.2 以其推进增量水位。

### 2.3 ack 前置矩阵与 reason 码（§7.3/§7.4/§8.7）

**ack 端点**：`POST {online_api_base}/pull/ack`，body `{payload_id, action∈{draft,active,invalidated}, case_id?, reason?}`。

| 本平台 action | 前置（online 校验） | 本方案何时发 |
|---|---|---|
| `active` | online 当前 ∈{assembled,draft} 且**必带 case_id** | 收单即激活（建 active case 成功） |
| `invalidated` | online 当前 ∈{assembled,draft} 且**必带结构化 reason** | 结构自检不过 / 映射缺 |
| `draft` | online 当前 =assembled | 本方案**不用**（收单即激活，省一个往返）；预留两步走能力 |

- 对**已知** payload_id 重复 ack → online 200 幂等（这是 offline ack 对账重放的安全前提，§6.4）。
- 前置不符 → online `ERR_CLUSTER_0003`(400)；未知 payload_id → `ERR_PULL_0003`(404)。
- **reason 结构化码**：`offline_cap_gap`（offline adapter/能力缺 → **归 offline 重扫自愈**）；`online_content_gap`（payload 内容缺/畸形 → 归 online admin requeue）；`manual_invalidate`（人工判无效，本方案不主动发）。reason 可带补注（缺哪个字段）。
- **R2（契约修订，§2 顶部）**：ack 前置矩阵对 active 补充例外——`offline_status=invalidated 且 invalidate_reason='offline_cap_gap'` 允许 offline 重扫自愈后回写 active（复用 payload_id）。online §7.3「激活」行已声明该语义（"rejected 后 offline 重扫自检通过"），v1.1 矩阵文本修订遗漏，**修文本即可**。offline §6.5 自愈路径依赖此例外。
- **R3（契约修订，§2 顶部）**：ack 400（`ERR_CLUSTER_0003` 前置不符）响应体携带当前 `offline_status` + `invalidate_reason`——offline 收到 400 时无法区分「manual_invalidate 竞态」（可对账自愈）与其它前置不符，需要 online 给当前状态。§6.6 依赖。

### 2.4 平台间端点（§8.7）——本平台实现侧

**offline → online（调用方）**：

| Method & Path | offline 侧动作 |
|---|---|
| `POST /pull/payloads` | 拉取请求 `{schema_version:"1.0", case_type:"regression_error", agent?, limit≤100, since_ts?}` → 响应 `{payloads:[信封], next_token?}`；case_type 白名单外**返回空集**（不传非法值） |
| `POST /pull/ack` | 状态回写（§2.3） |
| **`POST /backflow/regression-results`** | **结果推送（v0.2.3 新增，批 2 落点）**：`error_regression` run **终态 commit 后**异步推「run 终态 + 逐 case 原始行」；鉴权 = **静态预共享 secret**（`Authorization: Bearer <secret>`，三出站端点共用同一 secret、**无 scope 分置**，§9）；幂等键 = `uk_verify_run(link_id, run_id)`（重复推 = 200 `duplicated:true`）；超时 5s、重试 3 次（1s/2s/4s），全败记 error 后放弃、**不阻塞 run 收尾**。载荷字段表见 online `solution_detail.md` §8.7。**必带两个水位字段（同一次查询产出，漏带任一则对应 online 守卫失效；契约必填、非可选）**：① **`agent_latest_version`**（本平台该 agent **已有终态 run 的最大版本**，按门控序）→ online 的 **fix_version 未发版（`no_progress`）**判定（`fix_version > agent_latest_version`）；② **`prev_terminal_version: str \| None`**（**本次 run 之前**、该 agent 最近一个**已到终态** run 的版本）→ online 的**「缺行中断」**守卫；**必填但值可为 `null`**（= 该 agent 此前无任何终态 run，即首次）。**⚠️ 口径硬约束（v0.3.0 依 online `verify.py` 实证）**：两个水位字段均为 **agent 级事实**——统计范围 = 该 agent **全部终态 run**，**不得**按 `trigger_type='error_regression'` 收窄（online `_agent_versions(session, agent)` 签名内无 trigger_type 过滤）。manual/held_out run 虽**不触发推送**，但其版本**计入**水位。offline 侧若误按 trigger_type 收窄 → 与 online「缺行中断」判据系统性错位 → 假中断。**载荷契约约束（v-next 补充）**：**(a)** `cases[]` 内 `case_id` **必须唯一**（同一 payload 内重复 → online **整单拒** `ERR_PULL_0002`）；**(b)** `finished_ts` 必须为**合法 ISO8601**（否则拒单）；**(c)** `cases_dropped` 在幂等重放时**不归零**——offline 重试时**不得**据此判断「没丢数据」；**(d)** `links_advanced` 口径 = online 侧**真正发生终态迁移的 link**（passed/failed/superseded），**非**「本次新落 run 行的 link」；**(e)** **v1 硬约束**：一次回归 run **只对应一个 cluster**（`trigger_signal_id` 单值），跨 cluster 批量回归**不在 v1**。**鉴权口径（v0.2.3 实证）**：online 侧服务凭证实现为**静态预共享 secret**（`require_evaluator`），**非 §9 设计的 JWT**——**三端点共用同一 secret，无 scope 分置** |

**~~online → offline（本平台实现，只读）~~——v0.2.3 整组作废**：

online **不再调用本平台任何接口**（方向反转：回归结果改由 offline 主动推）。原计划的三个只读面**取消实现**：`GET /api/v1/runs`、`GET /api/v1/runs/{run_id}/results`（原 `GET versions` 同组）。连带作废：**§8 平台只读面整节**、**§9 入站凭证链**（`BACKFLOW_INBOUND_SECRET` / `BACKFLOW_INBOUND_SECRET_PREV` / `scope=platform:readonly`）。原「online 消费规则」（回查只读、退避重试、"回查失败待人工"）随之取消——**online 侧的失败探测改为"超时未收到推送"的被动形态**，本平台无对应义务。

### 2.5 offline 期望行为 4 条（§7.3，online 依赖不变式 → 本方案设计约束）

| # | 期望行为 | 本方案落点 |
|---|---|---|
| 1 | pull 后**不改写 case 内容**；内容缺口不就地编辑补全 → 驳回 + invalidated | §5.1 信封原文留档 + §6.3 驳回路径 |
| 2 | ~~list_runs 支持 agent+version+status+**case_id** 过滤；run_results 返回 `case_id+case_type+pass_fail`~~ **v0.2.3 取消**（只读面整组作废）；替代 = **推送载荷字段级对齐**：`case_id + case_type + pass_fail + error_type` 随 `cases[]` 全量推，online 侧全量对账（不存在「缺行」，故原 R-4/R-16 的 `excluded_case_ids` 字段位一并取消） | §2.4 推送端点（批 2 落点） |
| 3 | 回归 run 不占互斥槽 / 不参与 max_active_runs / 独立保留档 | 批 2 落点（批 1 只做模型与隔离预留，§7.3） |
| 4 | error-only run 的 pass_fail = executor 技术判定 ∧ verifier no_fallback，verifier 阶段落终值，不经 SCORING、不产 agent_score | 批 2 落点（§7.3） |

> **「结果未达」判据（v-next 补充，online 侧口径）**：online 判「结果未达」= **事件派生**（由 link / claim 的状态事件推导），**非在线时窗轮询**；阈值 N = `dict_config.claim_ttl_days`（默认 14）——超 N 天无推进即标记；**抑制条件** = 未到期 claim 期内**不**标记。**本平台无需配合**（无对应字段/接口义务），此处仅同步口径，防把 online 侧描述误写成「online 超时轮询」。

---

## 3. 端到端数据流（批 1 生产形态）

```
online                               offline
  │   assembled payload 备好（online DB，无限期待拉）
  │◄─────────────────────────────────┐
  │  pull_loop 周期触发（仿 scanner_loop，GET_LOCK 单例）
  │◄── POST /pull/payloads ──────────┤  ① 拉取（since_ts 增量 + next_token 游标）
  │── {payloads, next_token} ────────►│
  │                                  │  ② 每 payload：inbox 登记/去重（payload_id UK）
  │                                  │  ③ 结构自检纯函数（必填/可空/词表 fail-closed）
  │                                  │  ④ 映射 source.agent/interface → offline Agent/AgentInterface
  │                                  │  ⑤a OK        → 建 error case（error suite, status=active；
  │                                  │      CaseVersion 延批 2，批 1 不预建，见 §5.1）
  │                                  │  ⑤b 自检不过  → 不建 case，inbox 记 rejected(reason)
  │                                  │  ⑤c 映射缺    → 不建 case，inbox 记 rejected(offline_cap_gap)
  │◄── POST /pull/ack ───────────────┤  ⑥ 本地就绪后 ack（active 带 case_id / invalidated 带 reason）
  │── 200（幂等）───────────────────►│  ⑦ inbox ack 落位；未确认的留待对账重放（§6.4）
  │                                  │
  │  （批 2）agent 发声称修复版本 → error_regression run 覆盖哨兵 → executor+verifier 判
  │◄── /backflow/regression-results ─┤  ⑧ run 终态 commit 后异步推送结果（§2.4：fire-and-forget + 有限重试，
  │                                  │    不阻塞收尾；批 1 返回真实 run 为空，以 fixture run 验证序列化）
```

收单侧职责窄化说明（C1 结论）：**本链路不创建任何 error_regression run**。判定 run 的触发方 = 被测 agent 发版回归流程（既有评测模式），时机 = online claim 填 fix_version 之后。批 1 里"该 case 无任何 run 覆盖"是**正常态**，无 run 覆盖即**无推送**；online 侧改由「**超时未收到推送**」被动探测（§2.4/§2.5），**不再有「反查空集 → continue pending 轮询」形态**（§8.2 已作废）。

---

## 4. 架构决策 D1~D5（拍板记录 + 理由 + 弃因）

| # | 决策点 | 结论 | 关键理由 | 弃用备选 |
|---|---|---|---|---|
| D1 | error case 落库组织 | **扩 `TestCase` 同表 + 独立 error suite** | 判定载体（EvalRun/EvalResult join case_id）必须复用现有 run 链路；同表换零割裂 | 独立新表（run/result/反查 join 割裂成两套，改造成本更大） |
| D2 | 判定 run 语义载体 | **新 `trigger_type='error_regression'`** | offline run 语义以 trigger_type 为唯一分支载体（held_out 先例）；§7.3 #3/#4 的免互斥/免分/豁免清理是 run 级新正交面 | 复用 manual + 旁路标记（特殊语义埋 run_config、展示/统计/前台到处特判） |
| D3 | error 回流类型标识 | **`TestCase.case_type` nullable 列，存 online 白名单原字面**（v1='regression_error'；普通 case=null） | run_results 反查直出零转换、口径唯一；online 白名单扩类型只加值 | 正交 boolean `is_error_backflow`（未来多 case_type 须回头改列；反查合成有漂移风险） |
| D4 | 平台暴露面 | ~~**独立只读面 `/api/v1/runs`（含 results）**~~ **v0.2.3 作废**——online 不再出站，本平台**零入站读面** | 原理由：契约 URL 本就带 /v1；现人类面 `/api/runs` 带业务 redact/留出集逻辑，塞平台凭证进同一入口双轨鉴权、风险面扩大 | ~~扩现 /api/runs + 路径 alias（动现人类面 + 双鉴权混入口）~~ **已无须取舍** |
| D5 | 双向认证载体 | **双方向独立 secret + JWT HS256 + iss/scope/exp + 定期轮换**（复用现 pyjwt 栈） | offline 现无 keypair 基建；对称 + scope 最小化 + 单向吊销/审计可溯源；可升级到 infra mTLS | RS256 双向 keypair（引入 PEM 管理基建，除非生产直上 mTLS） |

挑战修正点吸收（C1~C5）与各节映射：C1 收单不建 run → §3/§7；C2 ack 两阶段崩溃洞 → §6.4 inbox ack 对账；C3 stub 不进生产路径 → §1.2-8/§12（测试基建）；C4 映射权威与前置依赖 → §6.3/§13；C5 治理污染面 → §5.3/§7.2 隔离口径。

---

## 5. 数据模型改造

### 5.1 `TestCase` 扩列（error 回流专用；普通 case 语义不变）

| 新列 | 类型 | 约束 | 语义 |
|---|---|---|---|
| `case_type` | VARCHAR(32) | nullable | online 白名单原字面（v1 `'regression_error'`）；**普通 case=null**。用 VARCHAR 非 ENUM：值域跟随 online 白名单演进、避免频繁 ALTER；应用层常量白名单校验。 |
| `payload_id` | VARCHAR(64) | nullable，**UNIQUE**（UK `uk_case_payload_id`） | online uuid4 幂等键；普通 case=null（MySQL 唯一索引允许多个 NULL）。 |
| `backflow_envelope` | JSON | nullable | **信封原文只读副本**（结构自检通过时快照，§2.5 #1 不改写）。含 source/versions/evidence/assert/no_fallback_config 全部字段，供批 2 复现与追溯。同 payload_id 被 requeue 内容刷新后重拉 → **以最新信封覆盖留档**（§6.5，保审计链）。 |
| `interface_id` | 沿用 | 非空 | error case 指向被测 agent 的来源接口（映射结果，§6.3）。 |

**约束放宽（条件分支）**：`expected` / `assertions` / `metrics` 由 non-null 改为 nullable（ALTER + 迁移）。

- 创建/更新校验分支：普通 case（`case_type IS NULL`）→ 三列必填（现约束语义**不变**）；error case（`case_type='regression_error'`）→ 三列**允许空**（无 golden expected、不走 SCORING）。
- **三列放宽后的写校验收口（评审 A-I6）**：DB `NOT NULL` 约束放宽后，现 `update_case` 通用 setattr 循环会允许普通 case 显式置 `expected/assertions/metrics=null` 静默落空（原 DB IntegrityError→500 防线消失）。在 `core/case_rules.py` 加纯函数 `assert_normal_case_golden_fields(case_type_is_null, fields)`，普通 case 三列非空校验**收敛到 create_case / update_case / pull_loop 三处写路径共用**；error case 分支只允许 null。
- error case 的 `input` 装载 envelope `evidence.input`：**纯装载不改写语义**（§2.5 #1）。evidence 形状由 online 定义，不强制 offline 标准 input schema（`{content,params,...}`）；有 turns 结构的落 `input_turns`，`input_type` 取 `conversation`，否则 `text`。error case 专属校验分支放行，**不得用普通 case 的 input schema 校验驳回**。
- error case 的 `is_gold=false`、`is_held_out=false`（天然成立，不再置真）；`validated_*_version`、`needs_review` 不适用。

**CaseVersion 快照延批 2（评审 A-I2 裁定 Q3）**：本批收单**不预建** CaseVersion v1。理由：① 现码无「创建即快照」先例——CaseVersion 只在 run 执行落 result 时由 `_ensure_case_version`/`_ensure_salvage_version` **惰性**创建，收单事务内同步建是一条新写路径；② 预建快照的 `_content_hash`（现 `sha256(repr(sorted(snapshot.items())))`）若与运行期两处口径不一致，error case 首次真跑会被判 content 变更、产出内容重复的脏 v2；③ 批 1 不产 run，预建对 `EvalResult.case_version_id`（非空 FK）零增益。**不变式改由「content 不可变」写守卫（§5.3，error case 全部写端点 403）+ 运行期惰性快照共同保证**——首次 error_regression run 时惰性快照即信封原文。批 2 若确需预建，须先把快照构建 + `_content_hash` 抽成三处（orchestrator/scorer/pull_loop）共享 helper。

### 5.2 `error_backflow_inbox`（收单记忆 + ack 对账状态机，新表）

**为什么需要这张表（D1 之外的必要补充）**：三态 payload 不能都建 case——结构自检失败/映射缺**不建 case**，但要记住"收到过、结论是什么"，否则：① 每轮重拉重复驳回刷日志/重复 ack；② requeue 后无法判断该"重处理"还是"已处理过"。ack 是本地落库之外的远端动作，两阶段无事务 → 需要 ack 状态机对账（C2）。

**DDL 草案**（字段以落库实现可调，语义以本表为准）：

```sql
CREATE TABLE error_backflow_inbox (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  payload_id    VARCHAR(64)  NOT NULL,                       -- online uuid4，幂等键
  schema_version VARCHAR(16) NOT NULL,
  case_type     VARCHAR(32)  NOT NULL,
  envelope_json JSON         NOT NULL,                       -- 信封原文留档（不改写），供重自检/审计
  status        VARCHAR(24)  NOT NULL,                       -- new / case_created / rejected（本地决策，见状态机）
  reject_code   VARCHAR(32)  NULL,                           -- online_content_gap / offline_cap_gap / manual_invalidate（rejected 时）
  reject_detail VARCHAR(512) NULL,                           -- 缺哪个字段/原因补注
  case_id       BIGINT       NULL,                           -- 建成的 error case id（case_created 时；作废后置 NULL）
  ack_status    VARCHAR(16)  NOT NULL,                       -- none / pending / acked / blocked（远端闭环，见状态机）
  last_error    VARCHAR(512) NULL,                           -- 最近处理异常摘要（排查）
  received_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_inbox_payload (payload_id),
  KEY idx_status_ack (status, ack_status, updated_at)        -- 对账/自愈/卡住重扫三类扫描位（评审 C-I7）
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**inbox 状态机（评审 A-B1/B2/B3 + B-B2 + C-I6 修入：拆两维 + 复位规则 + blocked 终态 + new 恢复）**：

原 `status='acked'` 双终态设计有语义二义（同时表达"曾 ack 过"与"最近一次 ack 已闭环"，requeue/自愈跨越两次独立 ack 时不成立）。改为**两维正交**：

- `status`（**本地决策**）：`new` 登记待处理 → `case_created` 已建 case（记 case_id）或 `rejected` 不建 case（记 reject_code）。**不再有 `acked` 值**。
- `ack_status`（**远端闭环**）：`none` 未确认 / `pending` 已发未确认（网络错/超时）/ `acked` online 200 幂等 / `blocked` 4xx 有界重试后**停发**待人工（区别于 pending 的重试态）。
- **闭环 = 远端已回写**：`case_created + ack_status=acked`（active 闭环）与 `rejected + ack_status=acked`（invalidated 闭环）都是终态。

**迁移与不变量**：
1. **复位规则（评审 A-B1 修复核心）**：任何「重新决策」入口——requeue 重处理（§6.5）、cap_gap 自愈重跑（§6.5）、blocked 人工复位——**必须先复位 `ack_status='none'`**（requeue 场景并覆盖 envelope_json；cap_gap 自愈建 case 场景清 reject_code），使该行重新进入对账/重扫可见范围。否则 `ack_status='acked'` 残留会让对账条件永不命中、补发 ack 永不发生。**【R-8 例外修订 2026-09-07（phase2 v0.7 §2.3 注④，H7-7）**：cap_gap 自愈重跑对**已 acked 的 invalidated 闭环行不再复位**——进「等待接入」探测态（每小时本地重跑自检+映射，补齐→建 case+发 active 复用 payload_id，仍缺→静默等轮）；复位规则仅适用首次/对账未闭环（`ack_status∈{none,pending}`）与 requeue/内容刷新场景。勿按本条旧语义对 invalidated 行补发 ack，code 随单独立项落。】**
2. **`new` 卡住恢复（评审 A-B2）**：登记后处理崩溃/异常的行（`status='new'` 且 `updated_at` 超时或有 `last_error`）由 **inbox 驱动重扫**重跑 pipeline（复用留档 `envelope_json`，天然幂等）——**不依赖重拉窗口**（游标已过不回头）。pull_loop 循环第 ② 步（§6.1）。
3. **ack 对账**：只扫 `status ∈ {case_created, rejected} AND ack_status ∈ {none, pending}`（§6.4）；`acked`/`blocked` 不重放。
4. **ack 4xx 有界重试后落 `blocked`**（§6.6）；`blocked` 是停发态，运维看板/审计可查，人工复位后走规则 1。
5. **非法组合**（case_created 无 case_id / rejected 无 reject_code / status=new 但 ack_status=acked 等）列入单测矩阵。

### 5.3 error suite 组织与隔离（D1/D3/C5 落地）

- **一个被测 agent 一个 error suite**（`TestSuite` 归 `agent_id`；suite 内 case 各自 `interface_id` 指向来源接口——不按 interface 建 suite，避免 suite 爆炸）。
- `TestSuite` 新增 `is_error_suite BOOLEAN NOT NULL DEFAULT FALSE`：收单自动建（按 agent upsert，`name` 形如「{agent.name}·error 回流哨兵集」）；**人工建普通 suite 不允许置真**（系统内部专用）。
- **前端只读标注**（批 1 收尾小改）：Cases.vue suite 列表对 `is_error_suite` 套只读样式（无新建用例/无编辑 golden/无删 suite 按钮；dashboard/用例计数口径标注"回流哨兵"）。后端守卫为强制层，前端为体验层。
- **error case 读面收敛（评审 C-I5）**：error case 的 `backflow_envelope` 原文（含 evidence input/output 实文，可能含线上现场敏感内容）**不向普通 viewer/标注角色暴露**——case 详情/列表读接口对 `case_type IS NOT NULL` 的 case 隐藏 envelope 敏感字段（仅内部/审计/平台面可见），防线上现场内容经 offline 读面二次扩散。批 1 收尾与前端只读一起做；`list_suites` 透出 `is_error_suite`（§11）供前端路由。
- **case 单体写守卫（评审 A-B3×1/B-B3/D-阻断1 三家独立验证现码后修入；批 1 改动清单补入 `api/{cases,annotations,scaffold}.py`）**：凡 `case_type IS NOT NULL`（error case），以下现通用写端点一律 **403**（error case 建成即 content 定稿，§2.5 期望行为 #1 不变式）：
  - `api/cases.py` `update_case`（含 status/input/content/三列变更）、`invalidate_case`（DELETE 作废）；
  - `api/annotations.py` `add_annotation`（会改写 annotation_status 并进 recalc 状态机）；
  - `api/scaffold.py` `add_case_scenes`（会污染 coverage 的 tagged 口径）。
  守卫放后端（前端只读样式是体验层）；普通 case（`case_type IS NULL`）行为不变。
- **suite 级防护**（后端强制）：
  - `is_error_suite` 的 suite：禁止人工删除；禁止人工往内增/改普通 case；禁止置入 golden/held_out。
  - **upsert 约束（评审 A-C3）**：收单建/找 error suite **固定按 `agent_id AND is_error_suite=true`** 查找，不存在则建——不依赖名字（现模型无 (agent_id,name) 唯一约束），与人工同名普通 suite 撞名时自动加后缀，杜绝把 error case 塞进普通 suite。
- **run 创建入口（评审 A-I4/D-4 修入；`api/runs.py` 补守卫，既有语义不变）**：`create_run`/`rerun_run` 对 `is_error_suite` 的 suite 一律拒绝；`held_out` 路径同防（error case 恒 `is_held_out=false`，双保险）。trigger 白名单只放 `manual/held_out`（§7.1）。
  - `_load_run_cases`（§7.2）对非 `error_regression` run **额外排除 `case_type` 非空**的 case——即使绕过 suite 选择，error case 也不会被普通评测 run 带走。

### 5.4 统一统计/标注过滤面（C5 收敛；评审 A-I1/D-7 扩全）

error case 会被各类"普通用例"语义的聚合扫到。**收敛规则：凡普通用例语义的聚合/展示，统一前置过滤 `case_type IS NULL`**（单一表达式，D3）。error case 永不进"待双人标注/待办"、永不参与 agent_score/门禁/黄金口径。**改动点收口（评审实测完整污染点清单，非仅三处）**：
- 标注面板/待办：`api/annotations.py` `annotation_todo` + annotation 状态聚合；
- dashboard 用例/覆盖计数：`api/dashboard.py` 用例计数 + **coverage 的 `used_ids`/`tagged`**（error case 的 interface_id 不得让只被哨兵覆盖的接口显示"已覆盖"，污染覆盖率口径）；
- suite/用例列表计数：`api/cases.py` `list_suites` per-suite `case_count`（error suite 自身计数 = 哨兵数，独立呈现不算污染）。
每处加 `case_type IS NULL`（suite 层聚合用 `is_error_suite=false`）前置条件，**不改现有聚合语义**。
- **写侧同步封死（评审 A-C4）**：`add_annotation`/`add_case_scenes` 对 error case 403（§5.3）——过滤面隐藏之外的第二道闸，防按 id 直标改写 annotation_status/场景标签。

> **cleanup 归属修正（评审 A-I1）**：现 `runner/cleanup_rules.py` 只删 `EvalRun`（run 级），**不聚合 TestCase**——error case 不会被 run 清理误清，§5.4 不需要为 cleanup 加 case_type 过滤。真正的批 2 风险是 error_regression run 被 run 级清理连带删掉 → 由 §7.3 登记「error_regression trigger 的 run 级清理豁免（pinned）」，批 2 落。
> 注：`annotation_status` 对 error case 保持默认 `draft`（不新增枚举态），靠 §5.4 过滤 + §5.3 写守卫双闸拦截，避免引入"NA 标注态"扩散全平台。

---

## 6. 收单链路（pull_loop）

### 6.1 循环骨架（仿 `runner/scanner.py` scanner_loop）

```
pull_loop（main startup 起 asyncio task，周期默认 60s，env 可配）
  ├─ GET_LOCK('backflow_pull_loop', 0) 单例互斥（多 worker 只一个执行；同 session 成对 RELEASE）
  ├─ ① ack 对账扫描（§6.4）：补发未确认 ack（status∈{case_created,rejected} ∧ ack_status∈{none,pending}）
  ├─ ② inbox 卡住重扫（§5.2 规则 2）：status=new 超时/异常行复用留档 envelope 重跑
  ├─ ③ 增量拉取（§6.2）：逐已注册 agent，assembled_ts 高水位 + next_token 迭代
  ├─ ④ 低频自愈扫描（§6.5）：rejected(offline_cap_gap) 行重自检；间隔解耦（如每小时）
  └─ 异常不退出循环；每轮 try/except + logger.exception（沿用 scanner 风格）
```

复用 scanner 的全部既有模式：`SessionLocal`、`GET_LOCK`/`RELEASE_LOCK` 同 session、异常兜底、`asyncio.sleep`。

**出站通道（评审 C-B2/A-I5 修入）**：pull/ack 出站必须 **async httpx `AsyncClient`**——本仓单进程 workers=1 共享 event loop，同步阻塞（含同步 DNS）会冻结 API + scanner + judge 全部协程；不得 new 同步客户端。online 主机走 **SSRF peer 允许名单**（复用/扩现 `core/http.py` allowlist 或新增 peer env，§10/§13 变更清单落地），环 1 用 MockTransport、环 2 真联调前把通道策略落进 §10。超时 + 5xx 退避按 §6.6。

### 6.2 增量拉取（offline 拉取侧行为，§7.2 归属；评审 B-B1/A-C2/C-I3/D-3 四家修入）

**增量锚必须在 online 时钟域——不再用 offline 本地墙钟当 since_ts**（评审四家独立命中：本地时钟 skew 漏拉/漏 requeue、轮末前向推进留永久漏拉窗口）。依赖契约修订 **R1**（pull 响应每条 payload 附 `assembled_ts`；已于 2026-09-03 落 online `solution_detail.md` v1.2，§2 契约修订包）：

- **游标持久化**：`next_token` 分页游标存 `system_config`（`backflow_pull_token`）；增量水位 `backflow_since_ts` = **本批已成功登记（入 inbox）的 payload 的 `max(assembled_ts)`（online 时钟）**，而非轮末墙钟。**空轮（无返回/全为已处理）不前移水位**——防「拉取快照 → 写游标」间隙新 assembled 的 payload 被永久跳过。
  - **实施转译注（2026-09-07，code_detail v0.2 §5.2）**：本行「单全局 `backflow_since_ts` = 本批成功登记 max」为**权威语义**；编码级实现采用**逐 agent 独立水位**——`backflow_since_ts = {agent_name: iso8601}`（存 system_config 单键），各 agent 只按**该 agent 成功登记** payload 的 `max(assembled_ts)` 推进、空轮**各自不前移**。理由：本 §6.2 下方「逐已注册 agent 拉取」下，单全局水位存在「快 agent 登记推高水位 → 慢 agent 已装配未拉 payload（assembled_ts < 新水位）被永久跳过」的固有漏拉窗口（评审 D-3 边界）；per-agent 记账 = 同一契约语义的**实现超集细化**（请求体本就按 `agent` 单值 + 各自 `since_ts` 请求，契约零改动）。**不改本行权威口径、不改契约**；下文凡引用「水位」处均以本注为实施口径。
- **requeue 可见性靠契约**：requeue 刷新 `assembled_ts=now()` → 大于等于已存水位 → 重新进入增量范围（契约 §7.2 语义，offline 不需对 requeue 行做游标特殊处理；§6.5 重处理判据 + envelope 覆盖兜底内容刷新）。
- **失败/卡住行不依赖重拉窗口**：由 §5.2 规则 2 inbox 驱动重扫（复用留档 `envelope_json`）兜底，与游标解耦。
- **拉取范围 = 逐已注册 agent（评审 D-13/B-S5 裁定 Q4）**：pull_loop 按 offline `Agent` 表已注册名单**逐名带 `agent=<name>`** 拉取（契约 §8.7 pull 支持 agent 单值过滤）。收益：① 完全未知 agent **根本进不了响应**——不建 inbox、不驳回、不留 invalidated 占 online 重推位、无自愈全量重扫噪音；② 已知 agent 未接入才走 `offline_cap_gap`；③ 未来新增被测 agent 只需 offline 注册即自动纳入拉取名单（名单与 resolve 同源于 `Agent.name`）。agent 数少（现 4 家），请求量可忽略。每 agent 拉取仍带 `limit≤100` + `next_token` 翻页直至空。
- **积压限速**：批 1 消费率 = 收单建 case 速率（无 executor），由 `limit` + 周期天然限速；批 2 引入 executor 后按 run 并发约束调节周期/limit（开放项，§13）。
- 逐 payload 处理**单点 try/except**：一个 payload 失败不阻塞整批（inbox 记 `last_error`；`status=new` 卡住行由规则 2 重扫恢复）。

### 6.3 逐 payload 处理（含结构自检纯函数与映射）

```
处理(payload):
  1. inbox upsert(payload_id)：已存在 → 依判据（§6.5 重处理 vs 幂等跳过）或 §5.2 规则；新 →
        status=new, ack_status=none
  2. validate_envelope(envelope) → (ok, errors[], verdict)   # 纯函数，core/error_payload.py
       必填校验照 §2.1 清单；可空缺省不驳；schema_version/case_type 白名单；
       assert.config_ref.wordlist_version == no_fallback_config.wordlist_version（同源同值）；
       空词表/words 缺失/wordlist_version 非法 → fail（v1 fail-closed，不静默 pass）
     not ok 且 verdict='content_gap'（内容缺/畸形：必填缺失、词表空、版本值非法）→
             reject_code='online_content_gap'，reject_detail=errors 摘要（指明缺项）→ ack invalidated
     not ok 且 verdict='version_drift'（版本不识别：schema_version != "1.0"、case_type 进新白名单值，
             评审 B-V2）→ 同样 reject_code='online_content_gap'，但 reject_detail 注明
             「版本不识别，需 offline 升级/联调同步，requeue 不愈」——与空词表（补齐词表+requeue 可愈）
             在运维处置上区分，避免误导 admin 反复 requeue
  3. 映射（C4，砍别名后精确匹配，§6.7）：
        resolve_agent(source.agent) → Agent            # Agent.name 精确（两平台现 4 家逐字同名）
        resolve_interface(agent, source.interface) → AgentInterface   # §6.7 归一规则（METHOD 前缀+占位通配）
        任一步缺 → reject_code='offline_cap_gap'（被测 agent 未接入/接口未注册，归 offline 接入轨补齐
                 + offline 重扫自愈，§6.5）→ ack invalidated
  4. 建 error case（同一事务）：
        upsert agent 级 error suite（agent_id + is_error_suite=true，§5.3）
        TestCase(case_type='regression_error', payload_id, suite_id=error_suite,
                 interface_id, input 装载 evidence.input, input_type 判定,
                 status='active', expected/assertions/metrics=None,
                 backflow_envelope=envelope原文, is_gold=F, is_held_out=F)
        （无 CaseVersion 预建——延批 2，§5.1）
        inbox.status=case_created, case_id=新 case id, ack_status=none
  5. commit 后 ack active（带 case_id）→ inbox ack_status 落位（§6.6）
```

**结构自检纯函数隔离**：`validate_envelope` 放 `core/error_payload.py`，无 IO、可单测全覆盖（必填/可空/词表/版本/一致性矩阵），不依赖 DB（C3 同精神：判定逻辑与传输解耦）。

**幂等去重**：`payload_id` UK 全链唯一；重复拉取/崩溃重拉 → 同一 payload 不重建 case、不重复 ack（§6.4 对账幂等依赖 online 200）。

### 6.4 ack 对账（C2：本地完成 × 远端 ack 两阶段崩溃洞）

- **顺序铁律：本地先落库（case 建成 / reject 结论入 inbox）→ 再发 ack**。绝不可先 ack 后建——先 ack 后崩溃会留下"online 已 active、offline 无 case/case_id"的永久断链。
- ack 发出后 online 未确认（网络错/超时/非 200）→ `ack_status` 置 `pending`，**不落 acked**。
- **对账扫描**（每轮循环第 ① 步）：`status ∈ {case_created, rejected} AND ack_status ∈ {none, pending}` 的行 → 重放 ack（幂等；online 对已知 payload_id 重复 ack=200）。`acked`/`blocked` 行**不重放**（评审 A-B3：blocked 是停发态不是重试态，避免每轮空转打 online）。重放持续失败 → `last_error` 累计 + 日志告警，不阻塞其他 payload。
- `ack_status='acked'` 才视为该行闭环；崩溃恢复后依赖此表而非"重拉时机"补 ack（online 对 active payload 不再返回拉取，重拉救不了未 ack 的 case_created 行——**对账必须存在**）。

### 6.5 requeue / 重扫自愈（§7.4 两条自愈闭环的 offline 侧落点；评审 A-B1/B-B2 修入）

| 驳回来源 | reason | offline 侧自愈动作 |
|---|---|---|
| offline 能力缺（agent/interface 映射不到、adapter 缺） | `offline_cap_gap` | **offline 重扫自愈**：低频（每小时）扫 `status='rejected' + reject_code='offline_cap_gap'` 的行（循环第 ④ 步）→ **先复位 `ack_status='none'`（§5.2 规则 1）** → 用 inbox 留档的 `envelope_json` 重跑结构自检 + 映射 → 通过 → 建 case → ack active（复用 payload_id）。**依赖契约修订 R2**：online ack 矩阵补 `invalidated(offline_cap_gap)→active` 例外（§7.3「激活」行本支持，v1.1 矩阵文本遗漏；环 0 确认，已于 2026-09-03 落 online `solution_detail.md` v1.2，§2 契约修订包）。被测 agent 接入补齐后无需 online 干预自动恢复 |
| online 现场内容缺/畸形 | `online_content_gap` | 等待 online admin requeue：requeue 刷新 `assembled_ts=now` → 下一轮增量拉取**再次返回该 payload_id** → inbox 见 `status='rejected' + reject_code='online_content_gap'` → **复位 `ack_status='none'` + `status→new` 重处理**（不是幂等跳过）→ 重拉信封**覆盖 inbox `envelope_json` 留档**（同 payload_id 内容已刷新，保审计链）→ 自检通过 → 建 case → ack active。本平台不自愈，避免用缺字段现场造次品 |

**判据"重处理 vs 幂等跳过"（含复位规则，评审 A-B1 修复核心）**：拉到已在案的 payload_id：
- `status='rejected'`（ack_status 任意，含已 acked 的 invalidated 闭环）→ **重处理**：`ack_status='none'`（requeue 场景并覆盖 envelope_json）→ 走 §6.3 步骤 2~5；**【R-8 修订 2026-09-07（phase2 v0.7 §2.3 注④，H7-7）**：cap_gap 自愈场景判据收窄——仅 `ack_status∈{none,pending}`（首次 reject/对账未闭环）或收到 requeue/内容刷新（`online_content_gap` 重拉覆盖）时复位重处理；**已 acked 的 invalidated 闭环行不再复位、不重发 invalidated**，进「等待接入」探测态。本条「含已 acked 的 invalidated 闭环」为 v0.2.1 旧语义，勿按旧语义实现，code 随单独立项落。】**
- `status='case_created'`（处理中 / 已 active 闭环）→ 幂等跳过（不重建、不重复 ack）；
- `status='new'` 卡住行 → 幂等跳过本次重拉 + 由 §5.2 规则 2 inbox 重扫恢复。

**manual_invalidate 竞态（评审 B-B2，依赖 R3；R3 已于 2026-09-03 落 online `solution_detail.md` v1.2，§2）**：收单即激活使契约"人工 invalidate 仅限 assembled/draft"的窗口恰好覆盖「offline 已建 case、ack active 未达 online」的 case_created 期——此时 ack active 收 400（online 已 invalidated）。**R3 语义**：400 响应带当前 `invalidated + reason=manual_invalidate` → 本地作废已建 case（`case_id` 置 NULL、case 本体 invalidated 软删语义）→ 该行落 `status='rejected' + reject_code='manual_invalidate' + ack_status='acked'`（远端已是 invalidated，不需再 ack）→ admin requeue 后重拉走重处理（上行判据第一条）自愈。

### 6.6 ack 发送细节（评审 A-B3/C-I6/B-S1/D-9 修入）

- 出站携带 **offline 自签服务凭证**（§9），header 规范见 §9.3。
- `action=active` 必带 `case_id`；`action=invalidated` 必带 `reason`（码 + 可选补注）。
- 非 2xx **按类分流**：
  - **404 `ERR_PULL_0003`（未知 payload_id）**：指向 online 数据丢失或基址错配 → **即时告警端到端连通性**，该行 `ack_status='blocked'` + `last_error` 注明，人工核查 online 端——不静默长期重试。
  - **400 `ERR_CLUSTER_0003`（前置不符）**：offline 状态理解与 online 不一致。有界重试（连续 N 次，N 实现约定）后落 **`ack_status='blocked'`（停发态，运维看板可查）**，不再每轮空转重放（评审 A-B3：blocked 与 pending 语义分离，pending 可自动重试、blocked 停发）。**依赖契约修订 R3**（已于 2026-09-03 落 online `solution_detail.md` v1.2，§2）：400 响应带当前 `offline_status` + `invalidate_reason` 以区分「manual_invalidate 竞态」（§6.5，可对账自愈）与其它前置不符——manual_invalidate 竞态不入 blocked、走 §6.5 对账自愈；其它前置不符按此处有界重试落 blocked。
  - **5xx / 网络错**：指数退避重试，`ack_status='pending'`，不置 blocked。
- **400 与 404 处置分开（评审 B-S1）**：404 是端到端连通性告警、400 是状态对账问题走 blocked——不得混入同一个"人工核查"桶长期沉默。

### 6.7 source → offline 实体映射（C4 权威；评审 D-2/6/12 修入：砍别名 + interface 归一独立）

- **agent 映射 = `Agent.name` 精确匹配（评审裁定 Q2：砍别名）**：
  - 依据：① 现 4 家被测 agent 在 online agent 表（solution_detail §2.1/§10）与 offline `SEED_AGENTS` **逐字同名**；② 本批拉取按已注册 agent 名单逐名发起（§6.2），响应 `source.agent` 必在名单内，映射失败即「已知 agent 但接入未就绪」→ `offline_cap_gap` → 接入补齐后自愈（§6.5），干净可恢复态。
  - 原「`backflow_agent_alias`/`backflow_interface_alias` 落 system_config 维护 JSON」**砍除**：offline 配置 schema（`sysconfig_schema`）只支持 int/number/str/bool/list，无 dict 类型，admin 配置中心撑不起该映射；为它扩配置 schema 是跨切面改动，超出批 1 收尾量级（评审 D-6 核实）。若环 2/批 2 真实联调出现命名不一致再补（届时扩 schema 或存 JSON 串 + resolve 兜底）。
  - 拉取名单（§6.2）与 resolve 同源于 `Agent.name` 注册，无第二处维护。
- **interface 映射 = 归一化匹配（评审 D-2 独立必补；别名救不了 interface）**：online `source.interface` 是「`METHOD ` + path」单串、模板占位符名与 offline 可能不同（契约样例 `"POST /api/chat/{id}"` vs offline 存 `/api/chat/{session_id}`），朴素等值匹配必失败 → 全员 `offline_cap_gap`。归一规则：
  1. 剥前导 `METHOD ` 前缀（首个空格前）得 path 串；
  2. method 精确比较（payload 前缀 method vs `AgentInterface.method`）；
  3. path 逐段匹配：`{...}` 模板占位符段归一通配（对齐该位置任一段），其余段精确比较。
  仍不命中 → interface 未注册 → `offline_cap_gap`（接入轨补齐）。
- **前置依赖（显式声明）**：本链路能收单的前提 = **被测 agent 已在 offline 完成 v2 接入**（`Agent` + `adapter_config` + `AgentInterface` 注册就绪，能复现）。未接入的 agent 的所有 payload 会走 `offline_cap_gap` 驳回（管道通但没活干）——环 2 联调前必须先补齐被测 agent 接入轨，见 §13。

---

## 7. run 判定就位（批 1 边界：只做模型与隔离预留，不产判定）

### 7.1 trigger_type 扩展

`backend/app/models/run.py` `TRIGGER_TYPE = ("manual", "held_out", "error_regression")` + MySQL ENUM 迁移（alembic）。`error_regression` 语义（契约 §7.3 #3/#4）**执行期全部留批 2**，批 1 只保证：

- 模型层可创建/查询该类型 run（供 fixture run 与批 2 使用）；
- 隔离过滤正确（下节）；
- **执行期安全闸（评审 D-4 修入，防「模型预留」与「可被执行」脱钩）**：现 `orchestrator._run` 不识别 trigger_type，若 error_regression run 被 start/rerun 会按 manual 全流程真实执行（占互斥槽、打被测 agent 产费用、error case 三列 None 触发 `_enabled_semantic_dims` 异常）。批 1 补两处，批 2 放开：
  - `create_run`/`rerun_run` 入口 trigger 白名单只放 `manual/held_out`（防 DB 直插的 error_regression run 被 rerun）；
  - `orchestrator._run` 对 `trigger_type='error_regression'` **先早退**（记日志，不执行）——5 行安全闸，不违反 C3（判定 stub 不进生产路径），把「模型预留」和「可被执行」解耦。

### 7.2 `_load_run_cases` 加载分支（隔离强制层）

`runner/case_loader.py` 现过滤：`suite_id + status='active' + is_held_out==(trigger=='held_out')`。改为：

```
if run.trigger_type == 'error_regression':
    条件 = suite_id + status='active' + case_type IS NOT NULL
           + is_held_out == False          # 防御性（error case 恒 false，评审 A-A2）
else:
    条件 = suite_id + status='active' + is_held_out==(trigger=='held_out')
           + case_type IS NULL                                      # 普通评测永不带哨兵
```

（`case_ids` 子集逻辑叠加不变。）这是 error case 与普通 case **同表隔离**的最终防线：即使 run 对象被绕过 suite 守卫指到 error suite，普通 run 也 load 不到哨兵；error_regression run 也 load 不到普通 case。manual×held_out×error_regression × case_ids 组合用例进单测（§12.1）。

### 7.3 批 2 承接（本文件不实现，仅落点声明）

error_regression run 的执行语义（免互斥槽 / 不经 SCORING / 不产 agent_score / 独立保留档 pinned 豁免 / verifier no_fallback 落 pass_fail 终值）、run 的创建触发（发版回归流程）、**offline 结果推送闭环（v0.2.3 更名，原「online claim 后回查闭环」；§2.4）** → **全部批 2**（另出方案）。~~批 1 反查面对无 run 覆盖的 case 返回空集 = 正确 pending 语义。~~ **v0.2.3**：该「反查面」随 §8 作废取消；批 1 不再有任何对外读面，其交付物收敛为「收单链路 + 凭证 + 隔离」三项。

**批 2 前置登记（评审多方向点名，防漏改）**：
- **run 级清理豁免**：现 `cleanup_rules.py` 按 agent+suite 保留 N 次删 `EvalRun`；error_regression run 须 pinned 豁免（契约 §7.3 #3），清理先归档 pass_fail 终态（评审 A-I1/D-8）。
- **dashboard 全聚合排除**：`dashboard.py` gate/trend/compare/perf/cost/baseline 现只排除 `held_out`，批 2 引入 error_regression run（无 agent_score）后须统一加 `trigger_type != 'error_regression'` 排除，否则污染版本/趋势/成本卡片与 stale suite 判定（评审 A-A3）。
- **error_regression run 创建入口强制**：`case_ids` 必须显式非空定向集合（不置 null——原 §8.2 反查 JSON_CONTAINS 依赖；该节 v0.2.3 已作废，但「定向非空」约束因归因需要**独立保留**，评审 B-V1/A-A4）；`version` 落被测 agent 声称修复版本 = online claim fix_version。
- **version 非 semver 放开（评审 D-14）**：现 `create_run` 强校验 `^\d+\.\d+\.\d+$`，online fix_version 形如 `2026.08.31-r47`——批 2 error_regression run 创建入口须放开该校验（fixture 阶段无感，批 2 会踩）。

---

## 8. 平台只读面 `/api/v1/runs`（D4）——**v0.2.3 整节作废**

> **v0.2.3 作废声明**：online 不再主动回查本平台，本节只读面**取消实现**，**不作为批 1 / 批 2 的实施对象**。
> - **作废**：§8.1 路由与鉴权挂点、§8.2 端点规格（`GET /runs`、`GET /runs/{run_id}/results`）、§8.4 online 侧调用约束——后者原列的本平台配合义务（按 agent+version+status+case_id 过滤、`excluded_case_ids` 透出）**一并解除**。
> - **保留有效**：**§8.3 字面量对表**——`run_status` 终态值集仍被结果推送载荷复用（§2.4），该表的字面量对齐工作**仍须做**，只是承载它的是推送载荷字段而非只读面响应。
> - 保留章节正文仅为历史设计记录，**实施时以 §2.4 推送契约为准**。

### 8.1 路由与鉴权挂点——**v0.2.3 作废**

> **v0.2.3 作废**：新文件 `backend/app/api/platform_runs.py` 与依赖 `require_service_scope("platform:readonly")` **一并取消实现**（§8 整节作废）；结果回传改由 offline 主动推送 `POST /backflow/regression-results`（§2.4）承载。以下正文仅作历史设计记录。

- 新文件 `backend/app/api/platform_runs.py`：`APIRouter(prefix="/api/v1/runs")`，`main.py` 挂载（`include_router(platform_runs.router)` 即可，prefix 已在路由内）。
- **鉴权独立**：不依赖 `get_current_user`/`require_role`（人类 JWT 体系）。新增依赖 `require_service_scope("platform:readonly")`（§9），校验服务凭证 JWT（iss/scope/type/exp）。
- 该面**只读**、无任何写端点；现人类 `/api/runs` 及业务逻辑（redact/留出集隐藏）一概不动。

### 8.2 端点规格——**v0.2.3 作废**

> **v0.2.3 作废**：本节点规格（`GET /api/v1/runs`、`GET /api/v1/runs/{run_id}/results`）随 v0.2.3 契约反转**整组作废**；结果回传改由 offline 主动推送 `POST /backflow/regression-results`（§2.4）承载。
> 原规格的服务端强制 `trigger_type=error_regression` 前置、`agent/version/status/case_id` 过滤语义、`case_id` 的 JSON_CONTAINS 约定、响应最小字段裁剪、未判定返空集语义**均不再实现**——整组降格为历史设计记录（可追溯见 git 历史与本文件修订记录）。

### 8.3 字面量对表（评审 B-V3，批 1 产出物）

offline 判定结果暴露的是平台内部枚举，online **接收推送后判定**依赖其精确语义，契约未给字面量。批 1 出一张对表作为环 2 输入，明确：
- run `status` ∈ `{pending, running, scoring, scoring_failed, completed, partial_failed, timeout, cancelled}` → online 判定映射（哪些算"已出结果"：completed / partial_failed 都算可判？scoring_failed/timeout 算 infra-error → online needs_review？）；
- `pass_fail` 四值 `{pass, fail, error, na}` → error/na 是否计入 online「failed」；error 型 case 只应有 pass/fail。
- 约定：**批 2 verifier 对 error case 只落 pass/fail 终值**（不落 error/na），避免歧义进入 online 判定语义（评审 N4 提前约定）。

### 8.4 online 侧调用约束（本平台配合项）——**v0.2.3 作废**

> **v0.2.3 作废**：原约束（online 回查只读、连续失败退避在 online 侧、本平台无需状态）随契约反转**整组作废**——online 不再出站，本平台**无配合义务**；online 侧失败探测改为「**超时未收到推送**」的被动形态（§2.4/§2.5）。出站凭证轮换见 §9.5。本节原正文随作废删除。

---

## 9. 服务凭证机制（D5，offline 侧）

### 9.1 角色与 trust root

| 方向 | 请求方 | 验方 | trust root（签发方） | scope |
|---|---|---|---|---|
| offline→online（pull/ack **+ 结果推送**，**v0.2.3 合并为一行**） | offline | online | **online** 生成 `BACKFLOW_OUTBOUND_SECRET` 下发给 offline | ~~`backflow:pull` / `backflow:report`~~ **v0.2.3 无 scope 分置**（三出站端点共用同一 secret） |
| ~~online→offline（回查只读）~~ | — | — | **v0.2.3 作废**（online 不再出站） | — |

> **v0.2.3 方向反转**：`BACKFLOW_INBOUND_SECRET` 及其 `_PREV` 宽限值**整条凭证链作废**——online 已删除全部出站调用，本平台不再需要验签 online 的任何请求，`scope=platform:readonly` 不再被使用。
>
> **v0.2.3 实证收敛（重要，勿再按本表实现）**：online 侧**从未实现**本节设计的 JWT service token 体系（`iss`/`scope`/`exp`/`create_service_token`），其真实实现 = **静态预共享 secret**（`api/deps.py` `require_evaluator`，`secrets.compare_digest` 比对 `settings.evaluator_service_secret`）。故 §9.2 的「出站自签 JWT」、§9.3 的 `create_service_token` / `verify_service_token` helper、§9.4 的 `backflow:*` scope 分置**均按此收敛**：**三个出站端点（pull/payloads、pull/ack、backflow/regression-results）共用同一 secret，不做 scope 分置**。本平台出站只需在 `Authorization: Bearer <secret>` 处填该预共享 secret；无需 JWT 库、无需签发流程、无需 exp 管理。「推送凭证不得复用调 pull 面」的隔离要求**当前不成立**，与凭证轮换一并归 online `#2` credential 缺口批。下表保留为历史设计记录。

验方持有验签材料并管理轮换；对称 HS256 下"持证方签发"与"验方验签"同 secret，方向隔离靠 **secret 分置 + iss/scope 载荷校验 + 审计**（D5 已接受此取舍；真正的密码学方向隔离=非对称，留 infra mTLS 升级位，§9.6）。

### 9.2 JWT 载荷（**v0.2.3 实证作废**：实际为静态预共享 secret、无 scope 分置、无 JWT）

```jsonc
// offline 出站自签（pull/ack 用，offline 持 BACKFLOW_OUTBOUND_SECRET 签发）
{ "type": "service", "iss": "agent-evaluation-offline", "scope": "backflow:pull",
  "iat": <now>, "exp": <now+5min> }
// offline 出站自签（结果推送用，同一个 BACKFLOW_OUTBOUND_SECRET，v0.2.3 新增）
{ "type": "service", "iss": "agent-evaluation-offline", "scope": "backflow:report",
  "iat": <now>, "exp": <now+5min> }
```

> **v0.2.3**：原「online 入站自签 `scope=platform:readonly`（用 `BACKFLOW_INBOUND_SECRET` 签发，供本平台验签）」**整条删除**——online 不再发起任何出站调用，本平台不存在入站服务请求。**本平台生产零入站验签面**。

- `type='service'` 与人类 access token（`type='access'`）**显式区分**；decode 校验 `type`，拒人类 token 冒充服务、拒服务 token 进人类面。
- exp 短时效（分钟级）+ 调用时即时签发（出站）或即时验签（入站），无需长期 token 分发，配合轮换。

### 9.3 helper 落点（`core/security.py` 扩展）

```
create_service_token(secret, iss, scope, minutes=5) -> str   # pyjwt HS256，type=service
verify_service_token(token, secret, *, expected_iss, expected_scope) -> payload
    # decode 硬编码 HS256 + 校验 type/iss/scope/exp；失败 → ApiError(401)
```

与现 `create_access_token/decode_access_token`（jwt_secret 人类体系）完全隔离，不共用密钥、不共用载荷类型。
> **v0.2.3 实证（勿再实现本节）**：online 侧**从未实现**本节设计的 JWT service token 体系——`create_service_token`/`verify_service_token` **不落地**；出站鉴权 = **静态预共享 secret** 置于 `Authorization: Bearer <secret>`（§2.4）。


### 9.4 配置（`core/config.py` + `.env.example`）

| env | 说明 | 强校验 |
|---|---|---|
| `BACKFLOW_OUTBOUND_SECRET` | online 下发、offline 出站（pull **与结果推送**）携带的凭证（**三出站端点共用同一 secret，不做 scope 分置**——v0.2.3 实证收敛，§9.1） | 非空 ≥256bit（仿 jwt_secret 启动强校验） |
| ~~`BACKFLOW_INBOUND_SECRET`~~ / ~~`BACKFLOW_INBOUND_SECRET_PREV`~~ | **v0.2.3 删除**——online 不再出站，本平台无入站验签面（原「轮换宽限旧值」随之取消） | — |
| `BACKFLOW_ONLINE_API_BASE` | online `/api/v1` 基址（http(s)://host:port），出站拼 `/pull/payloads`、`/pull/ack`、**`/backflow/regression-results`**（v0.2.3） | 非空 URL |
| `BACKFLOW_PULL_INTERVAL_SECONDS` | pull_loop 周期，默认 60 | >0 |
| `BACKFLOW_PULL_LIMIT` | 单批拉取上限，默认 50（online limit≤100） | 1..100 |
| `BACKFLOW_ENABLED` | 功能开关（默认 off；联调/上线打开） | boolean；true 时联动启动强校验 + router 挂载（§10 item 2/3） |

（别名映射已砍除，见 §6.7——不新增任何 `system_config` 键；拉取名单维护在 `Agent` 表注册。）

### 9.5 轮换

- ~~入站（offline 验 online）：offline 换 `BACKFLOW_INBOUND_SECRET`，旧值移 `_PREV` 保留宽限期 → 通知 online 换签发密钥 → 宽限过后清 `_PREV`。验签尝试列表 = `[new, prev]`。~~ **v0.2.3 取消**——本平台无入站验签面，`_PREV` 宽限机制不再需要。
- 出站（offline 签发给 online 验）：online 换 `BACKFLOW_OUTBOUND_SECRET` 下发给 offline 后，offline 即时切新值（token 短效，无存量吊销问题）。**v0.2.3：pull/ack 与结果推送共用同一 secret**，轮换一次两侧同时生效（scope 不随轮换变化）。
- 实现对齐 online 契约"凭证轮换同步"（§13.5）。

### 9.6 升级位

生产若走公共网关终止 / infra mTLS（online task.md §17 #14/#15），把 §9 的 header 校验替换为网关级双向认证，应用层改动收敛在 `require_service_scope` 依赖内。

---

## 10. 环境变量与启动（变更清单）

1. `.env.example` 增补 §9.4 全部键 + 启动脚本强校验（非空/长度/URL 格式）。
2. `core/config.py` `Settings` 增补对应字段（`extra="ignore"` 保持）。**门控完整化（评审 D-5）**：`BACKFLOW_ENABLED=true` 时启动强校验强制两 secret 非空 ≥256bit + `BACKFLOW_ONLINE_API_BASE` 为合法 URL（补入现 `_validate_secrets`）——防「开了开关忘配 secret，pull_loop 每轮用空 secret 签名」。
3. `main.py`：startup 增起 `pull_loop`（受 `BACKFLOW_ENABLED` 门控）。~~`platform_runs` router 挂载**随门控**~~ **v0.2.3 取消**（§8 只读面整节作废）——本平台无入站读面，门控只作用于 pull_loop 与（批 2）出站推送，不存在「面挂但全 401」半开态。
4. alembic 迁移：§5.1 TestCase 扩列 + §5.2 inbox 新表 + §5.3 TestSuite.is_error_suite + §7.1 trigger_type ENUM 扩展。

---

## 11. 复用与不改清单

**复用（不新造）**：

| 现机制 | 复用处 |
|---|---|
| `scanner_loop` 骨架（GET_LOCK 单例/异常兜底/周期） | pull_loop（§6.1） |
| `SessionLocal`/`core.db` | 全部收单事务 |
| pyjwt HS256 栈 | service token（§9.3，独立 secret） |
| `CaseVersion` 快照机制 | 批 2 运行期惰性快照（§5.1 延批 2，批 1 不预建） |
| 现 `TestCase` input 统一格式与 `input_turns` 列 | error case input 装载（§5.1） |
| 审计（`AuditLog`/`write_audit`） | 收单/驳回/激活/轮换审计留痕（user_id=0 系统） |
| alembic 迁移流程 | §10 item 4 |
| 现 `Agent`/`AgentInterface` 只读查询 | 映射（§6.7） |

**明确不改（保护面）**：

- `*Api` 类、`scaffold` discover/sync、现人类 API 路由与鉴权**体系**（人类 JWT 校验方式不变）。
- `Agent`/`AgentInterface` 接入模型（只读）。
- executor / orchestrator / scorer / judge worker 主链路（批 1 零改动；`_load_run_cases` + orchestrator `_run` 早退闸是唯二触碰的 runner 文件，§7）。
- dashboard / annotation 现有聚合语义（只加 §5.4 前置过滤 / §5.3 写守卫，不改既有普通用例语义）。

**补守卫但语义不变（评审 B-B3/A-I4：原「不改 /api/runs、/api/cases」措辞有误导——文件被动，但只加守卫不改既有普通语义）**：
- `api/runs.py`：`create_run`/`rerun_run` 拒 `is_error_suite` 的 suite + trigger 白名单（§5.3/§7.1）。
- `api/cases.py`：`update_case`/`invalidate_case` 对 `case_type IS NOT NULL` 403；`create_suite`/`update_suite` 禁人工置 `is_error_suite=true`（§5.3）；`list_suites` 序列化**透出 `is_error_suite`**（评审 D-15，前端只读标注/run 下拉依赖）。
- `api/annotations.py` `add_annotation`、`api/scaffold.py` `add_case_scenes`：对 error case 403（§5.3/§5.4）。

**被改但语义不变**：`models/case.py`（扩列/放宽约束为条件分支；**TestSuite/TestCase/CaseVersion 同文件，本仓无 `models/test_suite.py`**——评审 A-C1/D-11 修正引用）、`models/run.py`（TRIGGER_TYPE 加值）、`runner/case_loader.py`（case_type 过滤分支）、`runner/orchestrator.py`（`_run` 对 error_regression 早退闸）、`core/config.py`（门控强校验，§10 item 2）、`core/security.py`（service token helper，§9.3）、`main.py`（加 loop/router）。

**新增文件**：~~`backend/app/api/platform_runs.py`（§8 只读面）~~ **v0.2.3 删除**（§8 整节作废）、`backend/app/core/error_payload.py`（validate_envelope 纯函数，§6.3）+ inbox 数据访问层（§5.2）。**v0.2.3 新增（批 2 落点，非批 1）**：出站结果推送 client（指向 `BACKFLOW_ONLINE_API_BASE`，可仿现有 httpx 基建），挂 `_finish_error_regression` 收尾之后（fire-and-forget + 有限重试，**不阻塞 run 收尾**）。

---

## 12. 测试与联调（环 1 offline 侧；stub 只在测试基建，C3）

### 12.1 单元测试

| 对象 | 覆盖 |
|---|---|
| `validate_envelope` | 必填字段逐一缺失、可空字段缺省不驳（fix_version/output/session null）、schema_version 非 1.0、case_type 非白名单、wordlist_version 不一致、空词表 fail-closed、超长 input、**版本不识别 vs 内容缺的分类（V-8）** |
| inbox 状态机 | 两维迁移矩阵；**非法组合单测**（case_created 无 case_id / rejected 无 reject_code / status=new 但 ack_status=acked 等，A-A1）；**复位规则**（requeue/cap_gap 自愈进重处理 ack_status 必清，A-B1）；`new` 卡住行 inbox 重扫恢复（A-B2） |
| ack 对账 | case_created/rejected 未确认重放；acked/blocked 不重放；**400 连续 N 次落 blocked 有界停止（D-9）**；5xx 退避保留 pending；**404 连通性告警分流（B-S1）** |
| 映射 | agent name 精确/找不到→offline_cap_gap；**interface 归一：METHOD 前缀剥离、占位符名不同（`{id}` vs `{session_id}`）仍命中、method 不符不命中（D-2）** |
| service token | 签发/验签/iss/scope/type/exp；人类 token 拒；expired 拒 |
| case_loader 隔离 | manual/held_out 排除 case_type 非空；error_regression 只含 case_type 非空（+ is_held_out=false 防御）；**manual×held_out×error_regression × case_ids 组合（A-A2）** |
| suite/run 守卫 | manual/held_out 创建 run 拒 error suite；error suite 禁人工删/增普通 case；**error case 直接 update_case/invalidate_case/add_annotation/add_case_scenes 403（写守卫组，B-B3）** |
| 游标 | 空轮水位不前移；批内 max(assembled_ts) 推进；**乱序/未来 assembled_ts 边界（D-3）** |

### 12.2 环 1 集成（fixture + fake-online，无真实 online）

fake-online = 测试内 httpx MockTransport，stub `POST /pull/payloads` 与 `POST /pull/ack`（返回契约形状：payloads **逐条附 assembled_ts（R1）**、含 200 幂等语义）+ **`POST /backflow/regression-results` 端点桩（批 2 落点，批 1 仅立桩）**，按契约 §7.1 信封样例造 fixture。覆盖：

1. **正向**：fixture 信封流 → pull_loop 单轮 → 自检 → 建 error case（active，无 CaseVersion 预建）→ ack active（Mock 断言收到 action=active+case_id）→ inbox 闭环。
2. **reject**：畸形信封/空词表 → 不建 case → ack invalidated(reason=online_content_gap)；**版本不识别（schema_version="1.1"）→ 同 code 但 detail 注明需 offline 升级（V-8）**。
   > **⚠️ 可达性订正注（2026-09-11，仅限环 2 口径，环 1 不受影响）**：本场景的「版本不识别」分支在**环 1（本节的 fixture + fake-online / MockTransport，信封由 fixture 直构）下有效且必须保留**——它测的是 offline 自身 `validate_envelope` 守卫，**链路不经过 online 的 pull 层**。但在**环 2（真实 online 联调）与真实运行下不可达**：online `api/pull.py:88` 对声明 ≠ `SCHEMA_VERSION` 的拉取请求**直接 400 `ERR_PULL_0002` 拒单**、对非白名单 `case_type` **返空集不传**（`pull.py:94`），而 offline 拉取时声明 `{schema_version:"1.0", case_type:"regression_error"}`（本仓 `error-backflow-solution_detail.md:292`）、`validate_envelope` 亦硬校验 `schema_version == "1.0"`（同文件 `:309`）⇒ **offline 永远收不到会触发该分支的信封**，该分支属**防御性冗余**。**⇒ 勿据本场景推断线上会产生「版本不识别」类 invalidated 行**（online 侧 R-7 曾据此推断实害，已于 2026-09-11 订正为「对象不可达、实害不成立」）。
3. **cap_gap**：agent 已注册但接口未接入 → ack invalidated(offline_cap_gap)；**完全未知 agent → 拉取请求根本不带它、不进 inbox（D-13/Q4）**。
4. **ack 对账**：模拟 ack 发送抛错 → inbox ack_status=pending → 下轮对账重放成功。
5. **requeue 重处理**：`rejected(online_content_gap)` 已闭环 → 重拉同 payload（Mock 再次返回，内容已刷新）→ **envelope_json 覆盖 + ack_status 复位 none** → 重自检通过 → 建 case ack active（A-B1/B-S3）。
6. **幂等**：同 payload 重复拉 → 不重建、不重复 ack。
7. **崩溃恢复**：登记后未 ack → 重启续跑 → 对账补 ack；**status=new 卡住行 → inbox 重扫恢复（A-B2）**。
8. **fixture 推送（**批 2 落点**，原「fixture run 反查」，v-next 改写）**：造 `error_regression` run（case_ids 含 error case）+ eval_result（**run 汇总字段按 scorer 同源聚合，不手工填——防字段形状假绿，D-10**）→ run 终态 **commit 之后**由 offline 主动推 `POST /backflow/regression-results`（fake-online 的 MockTransport **增补该端点桩**）→ 断言 online **收到**载荷，且载荷含 `case_id/case_type/pass_fail/error_type/error_detail` 与两个水位字段（`agent_latest_version` / `prev_terminal_version`）；**重放同 run 幂等**（`uk_verify_run` → `duplicated:true`，且 `cases_dropped` 不归零）；**manual/held_out run 不触发任何推送**（哨兵隔离验证）。

   > **批 1 范围**：仅立 fake-online 的 MockTransport 端点桩；推送调用与载荷断言均在批 2 实现后启用。
9. **凭证**：无凭证/人类 token/scope 错/iss 错 → 401/403；平台面人类 token 拒。
10. **ack 400/404 链路级（D-9/B-S1）**：Mock ack 返 400 前置不符 → 有界重试后 inbox 落 blocked（非 acked）；404 → 连通性告警 + blocked。**空轮水位不前移 + 翻页中途新 assembled 不漏拉（B-B1/S6）**。

> 注：R1~R3 已于 2026-09-03 落 online `solution_detail.md` v1.2（§2 契约修订包）；环 1 中对应断言的 Mock 仍按修订后形状扩展实现，与真实 online 的对齐在环 2 联调。

### 12.3 测试后汇报

README 测试计数（后端/集成/前端）更新；环 1 验收报告仿 `acceptance-report-78.md` 惯例出。

---

## 13. 风险与开放项

| # | 风险/开放项 | 等级 | 说明与缓解 |
|---|---|---|---|
| 1 | **C4 前置依赖：被测 agent v2 接入就绪** | 高 | error 回流能收单的前提 = 被测 agent 已在 offline 注册（Agent+adapter+AgentInterface 可复现）。拉取按已注册名单逐名发起（§6.2），名单内未接入的 agent 的 payload 走 offline_cap_gap 驳回（接入补齐后自愈）。**环 2 联调前必须先走通被测 agent 接入轨**；方案 §6.7 显式声明。 |
| 2 | error suite 运维认知 | 中 | 系统自动建的 suite 无 golden/无标注、前端只读；人工不得误当普通 suite 编辑/删。后端守卫强制 + 前端标注；文档/使用指南补一条。 |
| 3 | 批 1/批 2 时序边界 | 中 | 批 1 不含结果推送（推送 = 批 2 落点），online 侧簇在批 1 期间恒为 pending（无 `verify_run_record`）——这是正确态；若运营误读为"未生效"需文档澄清。推送 → 判定 → link 终态的端到端时序验证在批 2。 |
| 4 | error case / inbox 长期驻留策略 | 中 | 哨兵 case 一旦 active 长期驻留（多次修复都覆盖它）；过期/废弃清理（online superseded 后 offline 是否回收）批 2 及后续定，当前不自动清理。**inbox 终态行（acked/blocked）保留策略同批 2 定**（acked 且 case superseded 后按 N 天归档清理）——当前登记开放项，防库膨胀 + 双存 envelope 体积（评审 C-I7/D-8）。 |
| 5 | 积压限速与批 2 executor 耦合 | 低 | 批 1 收单速率高（无 executor）；批 2 引入复现后按 run 并发调节周期/limit。开放项。 |
| 6 | 凭证轮换运维 | 低 | 双值宽限（§9.5）；轮换走配置变更 + 审计。 |
| 7 | 传输通道回溯 | 低 | 保 HTTP pull 的论证见本文件 §0；若未来量级/扇出需求出现，重新评估（Kafka 引入点是批 2 及后续，非本批）。 |
| 8 | ~~契约修订 R1~R3 未落~~ **已解除（2026-09-03）** | ~~高~~ | 增量锚（R1 pull 响应带 assembled_ts）、cap_gap 自愈（R2 ack 矩阵例外）、manual-invalidate 竞态对账（R3 400 带当前状态）三处实现依赖已随 online `solution_detail.md` v1.2 落改解除（§2 契约修订包；detail §7.2/§7.3/§8.7/§8.9）。实现依赖标注保留在 §2/§6.2/§6.5/§6.6/§12 供追踪。 |
| 9 | ack 400/404 处置运维面 | 中 | 400→blocked 有界停止、404→连通性告警分流（§6.6）；blocked 行需运维面板可见（inbox 管理查询）+ 人工复位入口（复位走 §5.2 规则 1）。 |
| 10 | error case 写守卫遗漏 | 中 | 若实现只做 suite 级守卫、漏 case 级（§5.3 三文件 403），error case 可被 update_case 静默改写 → 判定输入漂移击穿契约期望行为 #1。已列入 §11 改动清单 + §12.1 单测组，实现时逐文件核对。 |
| 11 | 出站通道正确性（评审 C-B2/A-I5） | 中 | pull/ack 出站必须 **async httpx**（单进程 workers=1 共享 event loop，同步阻塞会冻结 API+scanner+judge 全部协程）；online 主机进出站 SSRF peer 允许名单（新增 env 或入现白名单），环 1 用 MockTransport、环 2 真联调前把通道策略落进 §10 变更清单。 |

---

## 附录 A：决策记录（traceability）

| 来源 | 决策/结论 | 落点 |
|---|---|---|
| 2026-09-03 整体 challenge C1 | 收单侧不建 run；判定 run 由发版回归流程触发 | §3、§7 |
| C2 | inbox ack 状态机 + ack 对账扫描（先本地落库再 ack） | §5.2、§6.4 |
| C3 | stub 判定只进测试基建，不进生产路径 | §1.2、§12 |
| C4 | source→Agent 映射权威 + 被测 agent 接入前置显式化 | §6.7、§13 |
| C5 | 同表隔离 + 统一过滤面（case_type IS NULL） | §5.3、§5.4、§7.2 |
| D1 | 扩 TestCase 同表 + 独立 error suite | §4、§5 |
| D2 | 新 trigger_type='error_regression' | §4、§7 |
| D3 | case_type nullable 列存 online 原值 | §4、§5 |
| D4 | ~~独立平台只读面 /api/v1/runs~~ **v0.2.3 作废**（online 不再出站，本平台零入站读面） | ~~§4、§8~~ **§2.4**（改由出站推送承载） |
| D5 | 双方向独立 secret + HS256 + iss/scope/exp | §4、§9 |
| 传输通道再审视 | 保 HTTP pull，不引入 Kafka | §0、§13 |
| 2026-09-03 四方向独立评审（A/B/C/D） | 阻断 3 簇 + 重要项 + 次要簇全量吸收：① inbox 两维状态机（拆 status/ack_status + 复位规则 + blocked 终态 + new 恢复）；② error case 写守卫（api/{cases,annotations,scaffold}.py 403 + create_run 拒 error suite + held_out 同防）；③ 增量锚改 assembled_ts 高水位（弃本地墙钟）+ 逐已注册 agent 拉取；④ 只读面限定 error_regression；⑤ orchestrator 早退闸；⑥ interface 归一（METHOD 前缀+占位通配）；⑦ 过滤面扩全（coverage/list_suites/dashboard 全聚合）；⑧ 门控完整化 + async 出站 | §5~§12 |
| 评审裁决 Q1 | 契约修订包 R1~R3 **接受**，入环 0 对表（online solution_detail） | §2/§6.2/§6.5/§6.6 |
| 评审裁决 Q2 | **砍** backflow_agent_alias/interface_alias（sysconfig 无 dict 支撑；现 4 家 agent 同名） | §6.7 |
| 评审裁决 Q3 | CaseVersion 预建**延批 2**（写守卫 + 惰性快照保不变式） | §5.1 |
| 评审裁决 Q4 | 拉取改**逐已注册 agent**（未知 agent 不进响应） | §6.2/§6.5 |

## 修订记录

| 版本 | 日期 | 内容 |
|---|---|---|
| v0.1 | 2026-09-03 | 草稿：批 1 范围、契约锚点、决策 D1~D5、数据模型、pull_loop、平台只读面、服务凭证、测试与联调计划。待独立评审。 |
| v0.2 | 2026-09-03 | **四方向独立评审修入定稿**。裁决：契约修订包 R1~R3 接受（入环 0 对表）；砍别名；CaseVersion 延批 2；逐已注册 agent 拉取。修入：inbox 拆两维状态机 + 复位规则 + blocked 终态 + new 恢复（A-B1/B2/B3、B-B2、C-I6、D-9）；error case 单体写守卫 403 + create_run 拒 error suite（api/runs/cases/annotations/scaffold 纳入改动清单，§11 措辞修正）；增量锚改 assembled_ts 高水位、空轮不前移、逐 agent 拉取（§6.2）；只读面限定 error_regression + 字面量对表（§8）；interface 归一 + 版本不识别分支（§6.3/6.7）；过滤面扩全 + cleanup 归属修正（§5.4）；orchestrator 早退闸 + 批 2 前置登记（§7）；门控完整化 + async 出站 + SSRF 名单（§6.1/§10/§13）；测试全面补齐（§12）。 |
| v0.2.1 | 2026-09-03 | **契约修订 R1~R3 落改确认**：online `solution_detail.md` v1.2 已落（R1 pull 响应逐 payload 附 `assembled_ts` / R2 ack 矩阵补 `invalidated(offline_cap_gap)→active` 例外 / R3 ack 400 带 `offline_status`+`invalidate_reason`；detail §7.2/§7.3/§8.7/§8.9 + task.md 环 0 登记），本方案对应实现依赖解除（§2 契约修订包 / risk 8 状态更新）。方案语义未再变更。 |
| **v0.2.3** | 2026-09-10 | **平台间契约方向反转同步（online `solution_detail.md` v1.23）——本平台由「被回查」改为「主动推结果」。** ① **新契约**：新增出站 `POST /backflow/regression-results`（§2.4）——`error_regression` run 终态 commit 后异步推「run 终态 + 逐 case 原始行」，鉴权 `scope=backflow:report`，幂等键 `uk_verify_run(link_id,run_id)`，超时 5s / 重试 3 次 / 全败放弃不阻塞收尾；**必带 `bound_version_first_seen`**（online 的 fix_version 守卫依赖此字段，漏带即守卫失效）。② **整组作废**：原 online→offline 三个只读面（`GET /runs`、`GET /runs/{run_id}/results`、`GET versions`）**取消实现**——**§8 整节作废**（**§8.3 字面量对表保留有效**，`run_status` 值集仍被推送载荷复用）；§2.4 下行表、§2.5 期望行为第 2 条同步改写。③ **凭证链收缩**：`BACKFLOW_INBOUND_SECRET` 及 `_PREV` **整条作废**（本平台无入站验签面）；§9.1 方向表、§9.2 载荷、§9.4 配置、§9.5 轮换四处同步；出站为**同一 `BACKFLOW_OUTBOUND_SECRET` 按 scope 分置**（pull/ack=`backflow:pull`、推送=`backflow:report`）。④ **R 条去向**：R-4（`excluded_case_ids`）/ R-16（字段位）**取消**——推送为全量对账，不存在「缺行→轮询」；R-5「agent 已见版本」只保留守卫最小信息，改由载荷 `bound_version_first_seen` 承载（**本平台仍须自行判定该 (agent,version) 是否首见终态 run**）；R-3 的 online 侧缺行语义取消（**offline 侧版本差集补建保留**）。⑤ **范围影响**：本平台 error-backflow 系列**代码零落地**（详见 §7 与 task 文件自陈），故本次为**纯文档契约同步、零代码返工**；批 1 的收单链路（pull/ack）**不受影响**，新增出站为**批 2 落点**。⑥ **实证收敛（online 第 2 刀开工时回填）**：**(a) 凭证形态**——online 实际实现为**静态预共享 secret**（`api/deps.py` `require_evaluator`），**非本文件 §9 设计的 JWT service token**；§9.1/§9.2/§9.4 已就地标注收敛口径，**三个出站端点共用一 secret、无 scope 分置**，`create_service_token` / `verify_service_token` **本平台无需实现**。**(b) 载荷新增必填 `agent_latest_version`**——与 `bound_version_first_seen` 同一次查询产出；online `verify.py:247-255` 的**防假连续**原依赖「该版本有无终态 run」的主动查询（`list_runs` → `cur is None`），推送模式下无载体，故以该字段作「**offline 已覆盖版本水位**」。**两字段漏带任一，对应的 online 守卫即失效**（契约必填，非可选）。 |
| v0.2.2 | 2026-09-07 | **code_detail v0.2 实施转译注记**：§6.2 单全局水位补「逐 agent 独立水位」注——编码实现按 per-agent `backflow_since_ts = {agent_name: iso8601}`（各自 max 推进、空轮各自不前移），消解逐 agent 拉取下「快 agent 推高单水位 → 慢 agent 已装配未拉 payload 永久漏拉」固有窗口；同一契约语义超集细化，不改权威口径与契约（code_detail v0.2 §5.2）。方案语义未变更。 |
| **v0.3.0** | 2026-09-11 | **平台间契约第 2 刀 + 第 3 刀回填（online 结果推送接收面 / claim 判据收敛 / `rejudge` 安全网）——推送源收敛 + 载荷字段增删 + 只读面整组作废 + 术语对齐。** ① **载荷字段（§2.4 出站表）**：**删 `bound_version_first_seen`**（online 侧已无读取点）；保留 **`agent_latest_version`** 必填并明示其承担「fix_version 未发版 → `no_progress`」判定（`fix_version > agent_latest_version`）；**新增必填 `prev_terminal_version: str \| None`**（语义 = 本次 run 之前该 agent 最近一个**已到终态** run 的版本；**值可为 `null`** = 该 agent 此前无任何终态 run，即首次）——承担 online 的「缺行中断」守卫，**必须与 `agent_latest_version` 同一次查询产出，漏带则对应守卫失效**。**新增载荷契约约束 5 条**：`cases[]` 内 `case_id` **必须唯一**（同一 payload 内重复 → online **整单拒** `ERR_PULL_0002`）；`finished_ts` 必须为**合法 ISO8601**（否则拒单）；`cases_dropped` 幂等重放**不归零**（offline 重试时不得据此判断「没丢数据」）；`links_advanced` 口径 = online 侧**真正发生终态迁移的 link**（passed/failed/superseded），**非**「本次新落 run 行的 link」；**v1 硬约束：一次回归 run 只对应一个 cluster**（`trigger_signal_id` 单值，跨 cluster 批量回归不在 v1）。② **§2.5 加注**：online 判「结果未达」= **事件派生**（由 link/claim 状态事件推导，**非在线时窗轮询**），阈值 N = `dict_config.claim_ttl_days`（默认 14）+ 抑制条件（未到期 claim 期内不标记）；**本平台无需配合**。③ **轮询链整组作废（online `recheck_job` 已删）**：§3 数据流图 ⑧ 由「online 回查 `GET /api/v1/runs`」改画 **offline→online 出站推送箭头**（`POST /backflow/regression-results`，run 终态 commit 后 fire-and-forget）；§3 尾段「反查空集 → continue pending 轮询」改写为「**超时未收到推送**」被动探测；**§8.1 / §8.2 / §8.4 降格为作废引用块**（§8.2/§8.4 原规格正文随作废删除，§8.3 字面量对表保留有效）；§10 item 3 删 `platform_runs` router 门控条；§12.2 第 8 条「fixture run 反查」改写为「fixture 推送」（Mock 断言 online 收到 + 幂等 + 哨兵隔离）；附录 A D4 行补作废标注（原漏改）。④ **凭证口径自洽**：§9.4 `BACKFLOW_OUTBOUND_SECRET` 说明由「同一 secret，~~按 scope 分置~~」改为「**三出站端点共用同一 secret，不做 scope 分置**」；§9.1 方向表 `backflow:pull` / `backflow:report` 两行**合并为一行**（消除 §9.1 与 §9.4 的自相矛盾）。⑤ **术语**：「回查」凡按「online 主动拉取」解者一律作废改写（§1.3 / §8.3；历史修订记录与 v0.2.3 已改对处保留）。⑥ 本次为**纯文档回填、零代码改动**；批 1 收单链路（pull/ack）契约语义不变。 |
