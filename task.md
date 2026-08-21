# AI Agent 评测系统 — 任务拆分与验收（task.md）

> 依据：`solution.md`（v0.3，88 项决策）+ `solution_detail.md`（v1.2，含遗留问题清单 33 条）。
> 用途：动手实现的依据。共 9 个阶段（前置阶段 A/B + 阶段一~七），阶段七为独立、严格、全面的集成测试与端到端测试。
> 「消化 #N」= 落实 solution_detail.md §十二 遗留问题清单第 N 条，并配对应 pytest 断言。

---

## 前置阶段 A：运行环境准备（平台自身 docker compose 地基）

**阶段验收**：`docker compose up` 一键起 backend+frontend+MySQL；密钥齐备且强校验；平台容器可 HTTP 访问宿主机 4 个 agent。

### 任务 A.1 Docker 镜像与编排
- 内容：backend Dockerfile（Python 3.11 + 依赖 + uvicorn `workers=1`）、frontend Dockerfile（Node 构建 + nginx）、MySQL 8.0 镜像（utf8mb4/时区/数据卷挂载）、`docker-compose.yml` 编排三服务 + 内网 network。
- 验收：`docker compose up` 后 MySQL 可连、数据卷重启不丢；单进程部署约束（workers=1）写入 compose（backend `/healthz`、frontend 可访问 留待阶段一/五应用代码就位后验证）。

### 任务 A.2 环境变量与密钥
- 内容：`.env.example`（Fernet 密钥/DB 密码/judge_api_key/review_api_key/JWT 密钥）；启动脚本生成随机密钥并强校验非空 ≥256bit。
- 验收：`.env.example` 齐备；首次启动自动生成密钥；JWT/Fernet 密钥空值启动报错。
- 消化：#31（JWT 启动强校验）。

### 任务 A.3 宿主机 agent 网络打通
- 内容：backend 容器 `extra_hosts: host.docker.internal`；SSRF 白名单放行该地址；出站 Transport 允许解析并直连宿主机内网地址。
- 验收：容器内 curl 到宿主机端口的 agent `/healthz` 通（逐个验证）；base_url 注册宿主机地址通过白名单校验。

---

## 前置阶段 B：agent 契约改造（外部 repo，被评对象就绪）

> 改造代码提交在各自 agent 仓库（`D:\study\aiprojcet\{agent}`），不在 `ai-evaluation`。
> **通用性原则**：评测系统通用，标准契约（solution.md §五）不迁就单个 agent；差距越大的 agent，改造越规范越详细。每个 agent 的改造目标统一 = 收敛到标准契约 + 提供 reset 接口（`POST /admin/reset`，body 传 seed，upsert 造数+重置）+ 鉴权 env 开关（评测环境关），未来新 agent 照此接入。
> 现状与 gap 依据：`agent-baseline.md`（4 agent 真实代码基线）。
> 调试约束：4 个 agent 各自独立 docker compose 运行，调试时**逐个启动**——启动一个、调试、关闭，再启下一个，不同时全起（本机资源限制）。

**阶段验收**：4 个 agent 均满足 solution.md §五 契约，契约探测全绿（SSE 变体：meta/usage/done 必选 + `id:` 帧 + data 内 ts；同步变体：usage/timing/meta，不验 done）。

### 任务 B.1 good-question 契约改造（SSE 变体）
- 内容（对照 agent-baseline.md §二 的 gap）：
  1. usage：`_stream_deepseek` 请求加 `stream_options.include_usage` + 解析末帧 usage → 合成 `usage` 事件；
  2. meta：补 `meta` 事件 `{agent, model, interface, contract_version, git_sha, knowledge_version}`；
  3. SSE 帧：yield 处加 `id:` 单调帧 + data 内 `ts`；
  4. 事件重映射：`token`→`answer`、字段 `content`→`delta`；
  5. 检索→tool_call：透出 `document_id` + 保留 per-doc rerank score，组装 `tool_call{name=检索, result={docs[{id,title,snippet,score}]}}`；
  6. 记忆压缩纳入计量：`_compress_memory` 的 usage 计入累计。
- 执行 repo：`D:\study\aiprojcet\good-question`。
- 验收：契约探测逐字段通过；usage 完整（含压缩调用）；tool_call 可采（含 doc_id/score）；agent 回归不破坏。

### 任务 B.2 customer-service 契约改造（SSE 变体，改造量最大）
- 内容（对照 agent-baseline.md §三 的 gap）：
  1. token 级流式：`deepseek_gateway` 开 `stream:true`，增量转 `answer.delta`；前端伪打字机改接真实增量；加 feature flag 开关，可一键回退非流式（灰度保护）；
  2. 标准 SSE：`sse_format` 补 `event:` 行、`id:` 帧、data 内 `ts`、心跳注释行 `:`；
  3. usage/meta：LLM 调用后透传 `usage`（原始响应有，纯 plumbing）+ 补 `meta` 事件；
  4. 业务动作→tool_call：状态机 execute 节点前后**新增观测层**，合成 `tool_call{name=create_return/create_refund/create_complaint, args, result}`；
  5. confirm：明确跨请求多轮协议（`action` 事件 + 新请求 `content="确认"`），confirm 归一进 tool_call 序列；
  6. 严重性评估 reasoning：`_assess_severity` 透传 `reasoning_content` 为 `reasoning` 事件。
- 执行 repo：`D:\study\aiprojcet\customer-service`。
- 验收：契约探测逐字段通过；流式改造回归（前端打字机接真实增量）；confirm 用例不卡住；tool_call（业务动作）可采；首字延迟口径可测（评测端 perf_counter 采首个 answer.delta）。

### 任务 B.3 smart-procurement 契约改造（SSE 变体）
- 内容（对照 agent-baseline.md §四 的 gap）：
  1. 事件映射：`thinking→stage`、`source→tool_call`、`score→answer`、`thought→reasoning`（仅 score；chat 的 thought 归 answer）；
  2. usage/meta/ts/id：补 `usage` 事件 + `meta` 事件 + `id:` 帧 + data 内 `ts`；
  3. 幂等键协调：明确 idempotency-key 约定（每次 execute 生成新 key；副作用接口 perf_repeat 降 1）。
- 执行 repo：`D:\study\aiprojcet\smart-procurement`。
- 验收：契约探测逐字段通过；usage/score/thought 可采；score 幂等键不触发 422；chat 的 thought 正确归 answer 而非 reasoning。

### 任务 B.4 contract-check 契约改造（同步 JSON 变体）
- 内容（对照 agent-baseline.md §五 的 gap）：
  1. answer：result 的 `violations[]`（违规清单）映射为 `answer`；
  2. reasoning：语义违规的 `message`（LLM 判定理由）聚合为 `reasoning`；
  3. tool_calls：`persist`/`validate` 阶段把每条规则命中组装为 `tool_calls[{name=rule_name, args, result}]`；
  4. usage：`extractor` + `semantic_evaluator` 收集累计 usage，result 透出；
  5. timing：补 `{start_ts, first_token_ts(=N/A), end_ts}`；
  6. meta：补 `{model, git_sha, knowledge_version(=规则集版本), contract_version}`。
- 执行 repo：`D:\study\aiprojcet\contract-check`。
- 验收：契约探测通过（同步变体模板，不验 done）；usage/timing/meta/tool_calls 可采；WAITING_REVIEW 时 result 已含定稿违规清单。

### 任务 B.5 契约探测工具
- 内容：`core/probe.py` 契约探测脚本，**按 `contract_type` 分两套必选字段模板**（SSE：meta/usage/done + `id:` 帧 + ts；sync：usage/timing/meta，不验 done），逐字段回显通过/失败，作为 B.1-B.4 的统一验收出口。执行 repo：`ai-evaluation`（平台侧，非 agent 侧）。
- 验收：对 4 个 agent 跑 probe 输出逐字段通过/失败清单；sync 变体不会被 done 缺失误拦。

### 前置 B 排期与工时估算

> 依据 `agent-baseline.md` 的真实改造量（非「加字段」）。按 1 人全职估，2 人可并行压缩。

| 任务 | 改造量 | 预估工时 | 依据 |
|---|---|---|---|
| B.5 契约探测（平台侧，先行） | 中 | 2 人天 | 两套模板 + 逐字段回显 |
| B.1 good-question | 轻-中 | 2 人天 | 透出/重命名/补字段为主，记忆压缩纳计量略复杂 |
| B.2 customer-service | 大 | 5 人天 | 从零做流式 + 前端 + 状态机新增 tool_call 观测层 |
| B.3 smart-procurement | 中 | 3 人天 | 事件映射 + 幂等键 + 补 usage/meta |
| B.4 contract-check | 中 | 3 人天 | usage 反丢弃 + violations→answer + tool_calls 组装 |
| 验收联调（逐个启动跑 probe + 回归） | — | 2-3 人天 | 受「逐个启动」资源约束，串行 |
| **合计** | | **17-18 人天** | 约 3.5 周（1 人）/ 2 周（2 人） |

**依赖与顺序**：
1. B.5 先行（probe 是 B.1-B.4 的验收出口，没有它无法验收）；且 B.5 在改造前先对现有接口录「行为基线」（现有响应字段/事件快照），改造后 diff 只增不删，防生产接口回归坍塌；
2. B.1-B.4 编码可并行（4 个独立 agent 仓库，改代码不需同时起服务）；
3. 验收阶段受「逐个启动」约束串行：每天起 1-2 个 agent 跑 probe + 回归，跑完关闭再启下一个。

**关键路径**：B.5（2 天）→ B.2 customer-service（5 天，最长）→ 逐个验收（2-3 天）。

**阶段验收目标（可度量 checklist）**：
- [ ] 4 个 agent 契约探测全绿（B.5 probe 逐字段通过；SSE 验 meta/usage/done + id/ts，sync 验 usage/timing/meta）
- [ ] 成本维度可采：4 个 agent 均透出 usage（含 good-question 记忆压缩、contract-check 反丢弃）
- [ ] 工具维度可采：4 个 agent 均透出 tool_call（检索 / 业务动作 / 规则命中）
- [ ] 副作用接口 perf_repeat 降 1 生效（退款/下单/上传/score 采样不重复执行业务）
- [ ] 每个 agent 自身回归不破坏（原有单测/集成测试通过）
- [ ] customer-service 流式改造回归：前端打字机接真实增量，TTFT 首字口径可测
- [ ] 改造前后行为基线 diff 只增不删（B.5 probe 改造前录基线、改造后对比，旧字段/事件不丢失）
- [ ] customer-service 流式 feature flag 可回退非流式（灰度开关生效）
- [ ] 4 个 agent 均提供 reset 接口（`POST /admin/reset`，body 传 seed，upsert 语义：造数+重置到初始态，归位范围含业务数据+对话上下文，seed→固定 data_id；reset 逻辑 = 造数脚本封装）
- [ ] 4 个 agent 鉴权均改为 env 开关（AUTH_ENABLED）：评测环境关（评测直连）、生产环境开
- [ ] 请求侧通用改造（见 §5.0）：URL 无路径变量、body=input 透传、多轮 conversation_id、reset 同步 upsert

---

## 阶段一：工程脚手架与数据模型（地基）

**阶段验收**：工程可本地启动；DDL 迁移成功；seed 数据（7 维度/内置算子/默认权重/admin 用户）就位；登录鉴权与 RBAC 可用；安全基线代码到位。

### 任务 1.1 工程骨架
- 内容：FastAPI 工程 + `config.py`（pydantic-settings，仅密钥 env）+ `db.py`（async engine，pool_size 显式配置）+ alembic + docker-compose（`uvicorn workers=1`）。
- 验收：`docker compose up` 后 `/healthz` 返回 200；DB 连接池按并发上限配置；单进程部署约束写入启动脚本。

### 任务 1.2 数据模型与迁移
- 内容：solution_detail.md §三 全部 DDL 落 SQLAlchemy 模型 + 首版迁移；`seed.py`（dimension 7 code / 内置 assertion_op / 默认权重模板 / admin 初始用户）。
- 验收：迁移在空库执行成功；seed 后 `dimension`/`assertion_op_def`/`agent_dimension_weight` 可查；外键/唯一键/索引与 DDL 一致；test_case 支持 `input_type='conversation'` + `input_turns` + `turn_golden_answers`。
- 消化：#2（content_hash 覆盖 weight/threshold）、#24（`eval_run.run_config` 快照落点）、#29（issue `reporter_id`）、#19（`eval_result.usage` 存 cache_hit/miss）。

### 任务 1.3 安全基线与鉴权
- 内容：`core/security.py`（MultiFernet/JWT HS256/bcrypt/refresh 轮换+familiy 复用检测）；`core/logging.py`（结构化日志+脱敏）；`core/http.py`（出站统一 Transport：解析→校验 IP→直连透传 Host，禁 redirect）；`deps.py`（get_current_user 每请求查 DB 角色 + require_role + 对象级校验）。
- 验收：登录/refresh/改密走通；JWT 密钥非空≥256bit 启动强校验；日志不含 Authorization/凭证/正文；`base_url` 注册时白名单校验生效；contract-check 无鉴权（裸奔）已标注，auth_config 留空不做凭证配置。
- 消化：#31（JWT 启动强校验）、#30（allowlist 禁 0.0.0.0/0 + audit + is_hot=false）、#32（XSS 转义规范落前端约束）。

### 任务 1.4 配置系统
- 内容：`system_config` 表 CRUD + `scope`（run/global/registration）语义落地；权重/阈值/价格/算子/rubric 管理端点；配置项清单（§七）全部可配；baseline_target 从金标准校准集自动标定（改阈值双签+审计）。
- 验收：`GET/PUT /config/global`、`/agents/{id}/weights`（agent 级默认）+ `/agents/{id}/interfaces/{iid}/weights`（interface 覆盖）、`/agents/{id}/interfaces/{iid}/targets`、`/model-prices` 可读写；scope 语义与 is_hot 一致。
- 消化：#25（heartbeat_interval scope/is_hot 统一）、#23（perf_repeat_count 默认 5，P50/P95 都算，P95 暂不准）。

---

## 阶段二：统一契约与评测执行引擎（核心链路）

**阶段验收**：用一个 mock adapter 能跑通完整 run 生命周期（pending→running→scoring→completed/partial_failed），产出统一结果对象并落库。

### 任务 2.1 SSE 解析器与统一结果对象
- 内容：`core/sse_parser.py`（半帧/多行 data/id/心跳、`done` 前必有 `usage` 校验、EOF 无 done 判 error）；`core/assembler.py`（ResultAssembler：list+join 拼接、按 id/ts 去重用 set、TTFT 用评测端 perf_counter）。
- 验收：单测覆盖半帧/乱序/缺 usage/EOF/万级 delta 去重；TTFT/端到端计时口径与 §5.3 一致。

### 任务 2.2 通用配置引擎
- 内容：`adapters/engine.py`（读 adapter_config → reset(seed) upsert → 组装请求（固定 URL + input 透传 + conversation_id）→ 标准 SSE/JSON 解析 → 多轮循环）+ `adapters/schema.py`（adapter_config 校验：base_url / interface.path / method / contract_type / reset.path / seed_field / conversation_id_field）+ 副作用声明（interface 级 side_effect）。
- 验收：mock 配置跑通 reset upsert + 请求 + 标准解析；SSE/JSON 解析正确；配置 schema 校验生效。

### 任务 2.3 run 生命周期（orchestrator）
- 内容：`runner/orchestrator.py`（create_run 互斥 FOR UPDATE / probe 移出锁事务 / 快照 / execute_run 心跳独立 task 贯穿全程 / finalize 幂等可重入）。
- 验收：mock run 能走完状态机；崩溃后 scanner 可回收；cancel 生效；finalize 双触发不重复置终态。
- 消化：#5（timeout salvage 与 finalize 守卫一致）、#6（心跳为独立 task 覆盖 scoring）、#21（probe 移出锁事务）、#20（scanner①③ salvage 对称回填）、#13（fail-only 终态 = completed）。

### 任务 2.4 单用例执行（executor）
- 内容：`runner/executor.py`（加权信号量先 per-agent 后全局 / 熔断检查 / 串行采样 / 每次采样 reset(seed) upsert 归位 / 副作用接口 perf_repeat 降 1（interface 级 side_effect 声明）/ 成本全 attempt 累加 / 多轮用例循环 turns 真实连续调用）。
- 验收：单用例跑通；Timeout 时该次 usage 仍记账（失败也产生成本）；副作用接口（退款/下单/上传/score）采样不重复执行业务；多轮用例跑通（agent 真实回复作下轮上下文，token 整段累计）。
- 消化：#4（失败 attempt token 记账）、#7（同步变体补 meta 或 model 来源）。

### 任务 2.5 熔断限流与后台扫描
- 内容：`core/circuit_breaker.py`（closed/open/half_open 状态机）、`core/limiter.py`（加权信号量）；`scanner.py`（回收/judge 续跑/硬超时/finalize 兜底/数据清理，逐条 try/except 隔离）。
- 验收：熔断/限流/退避生效；scanner 各任务可运行且单条异常不中断整体。
- 消化：#15（judge_task attempts 耗尽转 failed）。

---

## 阶段三：评分、门禁与 judge（评分数值）

**阶段验收**：规则维度评分正确；agent_score/门禁判定正确；judge 骨架搭好（judge_task 表 + 抢占闭环）。

### 任务 3.1 断言算子
- 内容：`assertions/base.py` + 内置算子（field_present/value_range/tool_called/source_hit/keyword_contains/list_pr）。
- 验收：每个内置算子有单测（含 source_detail 落库）；自定义算子仅 admin 注册且 class_path 白名单校验。

### 任务 3.2 评测维度（metrics）
- 内容：`metrics/base.py` + completeness/tool_usage（规则）+ ttft/e2e/token_cost（读 timing/usage，含 cache 计费）。
- 验收：规则维度分 = 断言通过率×100；**tool_usage 通过 tool_called/tool_not_called/source_hit 断言打分**（当前 4 agent 无 function calling，评的是透出的业务工具动作：检索/规则命中/业务动作）；性能/成本独立呈现；成本按 miss/hit 分开计。
- 消化：#19（cache 计费）。

### 任务 3.3 评分合成与门禁
- 内容：`runner/scoring.py`（维度级 N/A 归一化防除零 / 权重取 interface 级（agent 默认 + interface 覆盖）/ agent_score=非 error/na 用例均值 / judge_incomplete 消费 / 门禁用例级：用例 pass = 每有效维度 >= snapshot.threshold（语义维度 judge 均值-σ 保守判定）/ error 硬门禁：error 数>0 不判 pass、率>10% block、任一 suite error>0 → agent 不绿 / suite 级 + agent 汇总 / 多轮：总分取平均但任一 turn 不达标即用例 fail / 性能预聚合：从 N 次采样算 P50/P95 落预聚合列）。
- 验收：全 N/A 用例 pass_fail='na'；N/A 维度剔除重归一化；门禁判定正确；error 硬门禁生效（error 数>0 不绿、率>10% block）；judge_incomplete 的 run 被 L0 跳过。
- 消化：#8（agent_score 口径）、#9/#10（N/A 与门禁交互 + threshold 消费落地）、#11（门禁排除 error/na）、#12（suite 加权口径）、#33（P1 权重 seed 只含启用维度）。

### 任务 3.4 judge 批处理
- 内容：`judge/llm_client.py`（OpenAI 兼容 `POST {base_url}/chat/completions`，judge/review 双 llm_profile 各配 base_url/api_key/model_name，换厂商纯改配置）+ `runner/judge_batch.py` + `judge/client.py`（抢占/claim_id 认领/attempts 上限/租约/退避/失败分类/回写 fencing/pending_human；judge 输出含 confidence；review profile 建议异构（同厂商仅告警，不拦截）；语义维度 judge 采样 judge_repeat 次取均值+方差）。
- 验收：并发 4 worker 无重复认领；429/503 退避、4xx 失败、超限 failed；judge 回写带 claim_id 栅栏；judge 输出含 confidence、review profile 可配同厂商（仅告警不拦截）、采样取均值+方差；pending_human 有 resolve 出口。
- 消化：#1（claim_id 每次认领唯一）、#14（off-by-one）、#16（失败分类）、#17（fencing）、#18（pending_human 出口 + 超时降级）。

---

## 阶段四：agent adapter 接入与初始化脚手架（接入被评对象）

**阶段验收**：4 个 agent 的 adapter 都能跑通真实调用，采到 usage/TTFT/E2E（契约已由前置阶段 B 就绪）。

### 任务 4.1 通用配置引擎（engine.py）
- 内容：`engine.py`（一次编写，所有 agent 复用）：读 adapter_config → reset(seed) upsert 拿真实 id（同步）→ 组装请求（固定 URL + input 透传 + conversation_id）→ 发请求 → 标准 SSE/JSON 解析 → 多轮循环 turns；副作用接口 perf_repeat 降 1（按 interface 声明）。
- 验收：用 mock agent 的配置跑通 reset upsert + 请求 + 标准解析全链路；SSE/JSON 解析正确；多轮循环 conversation_id 复用跑通。

### 任务 4.2 4 个 agent 的 adapter_config 配置 + 接入验证
- 内容：为 4 个 agent 各写一份 adapter_config 声明（base_url / interface.path（固定 URL，无变量）/ method / contract_type / reset.path + seed_field / conversation_id_field），不写 per-agent 代码；接入真实调用验证。
- 验收：4 个 agent 均纯配置接入，真实调用采到 usage/TTFT/E2E；good-question 多轮 conversation_id 跑通；customer-service confirm 用例不卡；smart-procurement score/chat 跑通；contract-check reset 上传+result 评测跑通；首字 N/A（ttft_ns）正确。
- 消化：#7（同步变体 meta/model 来源）。

### 任务 4.3 初始化脚手架
- 内容：接口手动录入（agent 管理页表单填 path/method/contract_type，codegraph 扫描仅作可选候选建议）+ 场景清单提取 + 用例骨架模板生成（静态，结构+断言模板）+ 造数逻辑并入 agent reset 接口（seed upsert，见前置 B）；用例创建时评测系统自动生成固定 seed 存进 input。
- 验收：接口手动录入生效（不依赖 agent 代码在本地）；场景清单 + 用例骨架模板(draft) 可产出入库；用例创建自动生成 seed；4 个 agent reset upsert 能造出首批 10-20 条用例数据。
- 消化：#26（golden_answer/assertions 修改权限剥离给 admin 或双签）。

---

## 阶段五：前端看板（呈现层）

**阶段验收**：全部页面可用；看板可下钻到 L4 证据级；权限矩阵生效（viewer 不可达 L4）。

### 任务 5.1 基础页面
- 内容：登录/改密、配置中心、用户管理。
- 验收：登录/登出/改密走通；配置中心可读写全部配置项；用户管理可建/禁用/改角色。

### 任务 5.2 agent 管理与用例管理
- 内容：agent 管理（含权重/门禁/探测/插件注册）、用例管理（CRUD/文件上传/标注/待办队列/场景打标/留出集可见性标记）。
- 验收：agent 注册+探测可用；用例增删改查/作废/打标/双人标注流转可用；独立评测角色标注（owner 不可标 golden_answer/断言）；留出集用例 owner 不可见；文件上传 UUID+类型校验生效。

### 任务 5.3 评测触发与看板
- 内容：run 触发/列表/取消/重跑；看板 L0 门禁墙（最新 version 跨 suite 汇总，陈旧 suite 单独标注）/L1 趋势/L2 版本对比（run 间方差，分数差 > 2σ 才标显著）/L3 用例明细/L3.5 门禁失败摘要（viewer 可达）/L4 证据（`/results/{id}/evidence`）。
- 验收：触发一次 run 后看板出分；L0→L4 逐层下钻可达；viewer 可看门禁失败摘要（fail 维度+用例+原因），L4 证据（reasoning 全文/judge 理由）403；版本对比只标显著变化。
- 消化：#28（L2 compare 返回体只回摘要、裁证据字段）。

### 任务 5.4 辅助页面
- 内容：性能面板、成本面板、覆盖率页、问题列表页。
- 验收：性能/成本面板读预聚合列出图；覆盖率提示盲区；问题列表可登记/流转。

---

## 阶段六：辅助能力（P2/P3）

**阶段验收**：报告导出、问题跟踪、覆盖率、基线、元评测、数据清理、用例骨架 LLM 增强生成、告警通知均可用。

### 任务 6.1 报告导出
- 内容：PDF（reportlab）/Excel（openpyxl）异步导出 + export_token 表 + 审计/水印。
- 验收：导出异步不阻塞；下载一次性+绑定 user_id+拒绝 viewer；审计落 audit_log。
- 消化：#27（导出授权）、#22（ProcessPool spawn + 分页取数防 fork/pickle）。

### 任务 6.2 问题跟踪闭环
- 内容：issue 状态机 + 每次 run 后自动复现验证 + 回归集。
- 验收：open→fixing→fixed→verified→closed 流转；复现验证幂等且跳过 error 用例。

### 任务 6.3 覆盖率与基线
- 内容：scene_catalog + case_scene 覆盖率；is_gold 金标准 + baseline_target 基线对比。
- 验收：覆盖率提示已覆盖/未覆盖场景；基线对比展示 agent 离达标分差距。

### 任务 6.4 元评测与数据清理
- 内容：judge 漂移检测（写 judge_drift_history）+ 人工抽检；留出集复测（is_held_out 用例跑 held_out run，overfit_gap 超阈值告警+门禁降级）；agent 侧测试数据定期清库 + 保留 N 次分批删（排除 pinned 关键版本）。
- 验收：漂移历史序列可查、超阈值告警；留出集复测跑通、overfit_gap 计算正确、超阈值告警+门禁降级；清理幂等、issue 关联 run 不清理、pinned 关键版本 run 不删。

### 任务 6.5 用例骨架 LLM 增强生成（P3）
- 内容：在任务 4.3 静态骨架模板基础上，用 LLM（走 `llm_client` OpenAI 兼容）自动补 input 描述/场景化用例内容，产出更完整用例骨架(draft)；黄金答案永远人工。
- 验收：从场景清单+接口一键生成带 input 描述的用例骨架(draft)入库；黄金答案/断言仍人工；LLM 走 llm_client、api_key 不落盘。
- 消化：#53（黄金答案永远人工）、#54（LLM 增强放 P3）、#88（LLM 走 OpenAI 兼容）。

### 任务 6.6 告警通知渠道
- 内容：告警通知 webhook（企微/邮件，可配）+ 通知开关 + 接收人配置；告警去重窗口 + 恢复通知 + 按 run 聚合。
- 验收：门禁失败/error 超限/过拟合降级等触发告警；同告警去重、恢复时发恢复通知；通知发送失败不阻塞主流程。
- 消化：#30（看板标红 + 可选通知）、#74（告警收敛：去重窗口 + 恢复通知 + 按 run 聚合）。

---

## 阶段七：集成测试与端到端测试（独立、严格、全面）

**阶段验收**：全链路跑通；单元/集成/E2E/性能/故障/安全/边界/对账测试全覆盖；遗留清单 33 条全部有对应测试断言；出具验收报告。

### 任务 7.1 单元测试补全
- 内容：pytest 覆盖（每个测试点配断言）：
  - SSE 解析：半帧/多行 data/`id:` 帧/心跳/`done` 前必有 `usage`/EOF 无 done 判 error/万级 delta 去重；
  - ResultAssembler：list+join 拼接、按 id+ts 去重、TTFT 用评测端 perf_counter；
  - 断言算子：field_present/value_range/tool_called/tool_not_called/source_hit/keyword_contains/list_pr，每个含 source_detail 落库；
  - 评分合成：规则维度=断言通过率×100、N/A 归一化防除零、agent_score 口径、门禁判定、judge_incomplete 消费、扣分制（assertion_penalty，语义维度断言挂扣分不否决）、多轮每轮取平均但任一 turn 不达标即 fail；
  - judge 抢占：claim_id 每次认领唯一/off-by-one/fencing/失败分类/pending_human 超时降级；
  - 熔断限流：closed/open/half_open 状态机/half_open 单探针 try_acquire_probe/指数退避；
  - 配置引擎（engine.py）：adapter_config 解析、reset(seed) upsert 归位（seed→固定 data_id、归位范围含对话上下文）、标准 SSE/JSON 解析、多轮 conversation_id 循环、confirm 自动确认、副作用接口重试禁（side_effect 门控）；
  - 权限边界：viewer 裁剪（L3/L4/导出/compare）、留出集 owner 不可见、字段级权限（evaluator 不可改 golden_answer/assertions）；
  - 成本：全 attempt 累加、cache hit/miss 分开计。
- 验收：核心逻辑覆盖率达标（覆盖上述每个测试点）；遗留清单 33 条每条有对应断言，全绿。

### 任务 7.2 集成与数据一致性测试
- 内容：
  - runner×scoring×judge×配置引擎 多模块联调；
  - 数据对账：`pass+fail+error+na==total_case` 恒成立、case_version 快照与当时用例一致、eval_result.data_id 与 reset 返回一致、run_config 快照与 run 创建时配置一致、export_token 随 run 清理（ON DELETE CASCADE）、预聚合列（ttft/e2e/total_tokens/total_cost）与明细一致；
  - 配置热生效：权重/阈值/价格改动即时影响下一次 run；scope=run 配置（case_timeout/perf_repeat_count/judge 参数）在途 run 不受影响（读 run_config 快照）；
  - 崩溃恢复：进程重启后 scoring 续跑（先续 lease 再挂心跳，防 scanner ① 抢先标 timeout）、judge_task 队列恢复、scanner 回收。
- 验收：多模块联调跑通；对账无差异；配置热生效且快照正确；崩溃后恢复续跑且不丢数据。

### 任务 7.3 端到端测试（真实 agent）
- 内容：
  - 逐个启动 4 个 agent（各自 docker compose，一次一个、跑完关闭），评测平台跑全量用例；
  - 核对标准事件（meta/usage/tool_call/done）与原始 LLM 返回一致；
  - 准确性四维度打分正确，**含 tool_usage：tool_call 事件→断言→维度分 全链路**；
  - 门禁/趋势/版本对比/下钻 L0-L4 全链路；
  - 报告导出内容与 DB 一致；
  - 多轮对话用例（good-question/customer-service/chat）conversation_id 循环、每轮判分取平均正确；
  - reset(seed) upsert 归位跑通：seed→固定 data_id、data_id 回传 execute、contract-check 上传+result 评测、smart-procurement 6 步造数。
- 验收：采到 usage/TTFT/E2E；tool_usage 打分与真实工具动作一致；门禁/趋势/版本对比/下钻正确；报告导出一致；多轮分值=每轮平均；reset upsert + data_id 回传全链路跑通。

### 任务 7.4 性能与压力测试
- 内容：
  - 并发上限：全局 max_inflight + per-agent 并发不超配、连接池不耗尽；
  - run_timeout 公式验证：不误杀正常 run（含慢 judge 大 run）、不放过死锁 run；
  - 延迟口径：P50/P95（默认 5 次采样，P95 暂不准，见 #23；多轮用例仅首轮 TTFT 进分布）；
  - 成本累加：全 attempt 求和、cache hit/miss 分开计，与真实账单核对；
  - 大 run 压测（数百用例）；
  - 事件循环不阻塞（bcrypt 走 run_in_executor、大 answer 拼接不卡）。
- 验收：无连接池耗尽/事件循环阻塞；run_timeout 正确；成本口径与真实账单偏差可接受；大 run 稳定跑完。

### 任务 7.5 故障注入与恢复测试
- 内容：注入 agent 宕机/重启、SSE 断流、DeepSeek 限流（429/503）、进程崩溃、单用例超时、judge 超时；验证熔断/退避/断流续推（Last-Event-ID）/崩溃恢复/timeout salvage；副作用接口 Timeout 不重试（防重复执行业务）；scoring 慢 judge 不误杀（独立心跳贯穿）；pending_human 超时自动降级（scanner ③.5）。
- 验收：故障后 run 能出分或正确标 timeout/partial_failed；judge 无重复认领；互斥槽不被卡死；finalize 幂等（双触发不重复置终态）；副作用接口只执行一次；慢 judge 的 scoring run 不被误杀；pending_human 超时能自动收敛出终态。

### 任务 7.6 安全测试
- 内容：
  - SSRF：rebinding、重定向绕过、0.0.0.0/0 白名单、内网地址校验、base_url 双字段一致性（adapter_config 出站 vs agent 列校验）；
  - reset(seed) 并发安全：per-agent 锁串行，多 worker 不互踩；
  - 插件 RCE：class_path 白名单绕过；
  - JWT：伪造/复用/alg=none、refresh 轮换 + family 复用检测；
  - RBAC 越权：viewer 拿 L3/L4/导出/compare 证据字段、evaluator 改配置、对象级越权；
  - prompt injection：judge 分隔符包裹、rubric 占位符、golden 含指令降级；
  - 文件：穿越/UUID/魔数/大小上限、XSS、CSRF；
  - 凭证：日志脱敏、GET 不回传 auth_config。
- 验收：各攻击面被拦截；viewer 拿不到证据级字段；无高危漏洞。

### 任务 7.7 边界与异常数据测试
- 内容：
  - 空 suite / 单用例 suite；
  - 全 N/A 用例（pass_fail='na' 落行）；
  - 全 error 用例（终态 = partial_failed）；
  - fail-only（有 fail 无 error）终态 = completed；
  - pending → cancelled（留审计记录）、cancelled 已完成用例 usage 记账不算分、rerun 建新 run 复制参数；
  - 标注 single 终态（常规用例单人标+review 通过）、金标准 double→consensus/disputed、disputed→draft 废弃重标；
  - issue 状态机 open→fixing→fixed→verified（自动）→closed（自动）、复现 fail 回退 open；
  - baseline_target 双签 approval_status（auto/pending_approval/approved）、approved 才参与快照；
  - 缺失字段的 meta/usage（契约不完整）；
  - 超大 answer/reasoning（MEDIUMTEXT 边界）；
  - judge 全失败（judge_incomplete 消费）；
  - 权重全 N/A 维度剔除重归一化。
- 验收：每个边界场景正确落库、正确出分、不崩溃、不除零；全 N/A/全 error 终态符合状态机；cancelled/rerun/标注/issue/双签状态机流转正确。

### 任务 7.8 验收评审
- 内容：对照 88 项决策 + 33 条遗留清单逐项核对，出具验收报告（含覆盖率、未覆盖项、已知风险）。
- 验收：验收报告齐备，可进入上线决策。

---

## 附：依赖与里程碑

- 前置阶段 A（运行环境）→ B（agent 契约改造）→ 阶段一 → 二 → 三 → 四 → 五 → 六 → 七，按序推进。
- 前置阶段 B 是外部 repo 依赖：改造代码提交在 `D:\study\aiprojcet\{agent}`。阶段一~三（数据模型/执行引擎/评分）不依赖 B，可与 B 并行先做；阶段四 adapter 依赖 B 契约就绪，需排在 B 之后。
- 阶段七贯穿始终：每阶段的单测随做随补，阶段七统一做集成/E2E/性能/故障/安全收口。
- 遗留清单 33 条是贯穿性的"编码 TODO"，已在各任务标注「消化 #N」，阶段七逐条验证闭环。
