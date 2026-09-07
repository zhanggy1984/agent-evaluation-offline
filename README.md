# AI Agent 线下评测平台（agent-evaluation-offline）

> **Agent 提测上线前的线下评测平台**：对任意 agent 的核心 LLM 接口在隔离测试环境做 HTTP 评测，量化**答案准确性 / 性能 / token 成本**三类指标，看板呈现汇总、版本对比、逐层下钻定位问题点。**线下**——评测不与生产流量混跑，用固定用例集 + 契约探测在独立测试环境执行。

第一次接触这个项目，只看下面三句就够了：

- **做什么**：把任意 agent（对话、问答、合同审查等 AI 服务）按统一标准接入，用固定用例集在隔离环境里反复调用、量化打分——答案准不准、快不快、贵不贵，一张看板看全，逐层下钻到「为什么扣分」的证据级；
- **怎么做**：契约探测（L0 硬拦截，接口协议不达标直接标失败）→ 逐 case 重置数据 → 调用 → 采集 → 规则断言 + LLM-judge 混合评分 → scorer 汇总 → 门禁墙呈现；agent 只适配标准契约，平台对任何 agent 零特判；
- **好在哪**：任意 agent 统一标准接入即用、评分带判定理由（不用复现现场就能定位问题）、性能/成本真实量化、PDF 报告一键导出；916 项自动化测试 + 四家 agent 门禁全 PASS。

本系统是**生产级线下评测平台**：docker-compose 两容器（backend + frontend）一键启动、MySQL 走共享 infra、强契约驱动任意 agent 接入、规则断言 + LLM-judge 混合评分、门禁墙逐层下钻、PDF 报告导出。

> **🔧 coding 入口（新会话开工先读）——当前主线 = error-backflow（error 回流闭环）**：online 平台 error 事件 → 本仓复现判定 → verifier no_fallback 复验。
>
> 1. **权威实施源**：`error-backflow-task.md`（34 项 O-A.1~O-G.3 + 门 G0~G6 × 里程碑 M1~M9 + 联调环 1/2 + X 系列集成用例）按门认领；实现细节锚 `error-backflow-solution_detail.md` **v1.1**（正文「详设 §x.y」即其章节，含 §12.6 X-1~X-11 集成异常/边界用例）。
> 2. **语义溯源 / 上游契约**：改方案语义才读 `error-backflow-phase1.md`（批 1 v0.2.2）/ `error-backflow-phase2.md`（批 2 v0.7.2）；契约消费 online 仓 `solution_detail.md` v1.10 / `solution.md` v3.5.9 / `task.md` 环 0~3。
> 3. **实施铁律**：`backend/` 截至 2026-09-07 零 error-backflow 代码落地（`case_type`/`is_error_suite`/`error_backflow_inbox`/`error_regression`/`no_fallback` 均无标识符），动手前以当时 HEAD 重对现码；实施前置风险注见 `error-backflow-task.md` header。
>
> 本文件下文 = **老评测平台本体**（backend/frontend）说明；error-backflow 是其上的增量改造，方案文档独立（`error-backflow-*` 前缀）。

---

> ## ⚠️ 前置依赖：共享 infra
>
> 本 agent **不自带任何中间件**，运行前须先部署共享 infra（MySQL 等）。
>
> ```bash
> # 发布物：clone infra 独立仓库后启动
> git clone https://github.com/zhanggy1984/share-infra && cd infra && docker compose up -d
> # 本地开发：infra 位于 ../infra
> cd ../infra && docker compose up -d
> ```

## 目录

- [一、这是什么：解决什么痛点](#一这是什么解决什么痛点)
- [二、系统架构](#二系统架构)
- [三、快速开始（3 步跑起来）](#三快速开始3-步跑起来)
- [四、使用场景与示例](#四使用场景与示例)
- [五、接入 Agent](#五接入-agent)
- [六、技术闪光点](#六技术闪光点)
- [七、技术栈一览](#七技术栈一览)
- [八、配置说明](#八配置说明)
- [九、目录结构](#九目录结构)
- [十、测试与验收](#十测试与验收)
- [十一、开发指南](#十一开发指南)
- [十二、常见问题](#十二常见问题)
- [十三、已知限制与优化方向](#十三已知限制与优化方向)

---

## 一、这是什么：解决什么痛点

agent 提测上线前，质量缺乏系统化量化与呈现：

- **评分口径不一**：答案「好不好」靠人工抽查，无统一维度、无门禁标准；
- **回归难发现**：改造一次代码，无法快速判断「哪些接口、哪些场景变差了」；
- **性能 / 成本不可见**：首字延迟、端到端耗时、token 消耗无量化，上线前不知道是否达标；
- **问题定位慢**：分数低不知道差在哪——是答案错了、推理缺步、工具调用错，还是接口超时。

本系统针对以上痛点，提供四个核心能力：

| 能力 | 实现 | 对应痛点 |
|------|------|---------|
| **强契约评测** | 跑前契约探测拦截，SSE 变体 / 同步变体两种标准契约，任意 agent 统一标准接入 | 口径不一 |
| **规则 + LLM 混合评分** | 确定性断言扣分 + LLM-judge 六级 rubric 精评（含判定理由） | 回归难发现 |
| **性能 / 成本量化** | 真实 usage 透出 × 模型单价表，TTFT/E2E 的 P50/P95，成本趋势 | 不可见 |
| **逐层下钻定位** | 门禁墙 L0 契约 → L3 用例明细 → L3.5 未达标摘要 → L4 证据级回放 | 定位慢 |

**评测范围**：只评「有 LLM 参与的核心业务接口」；注册/登录/CRUD/健康检查不进评测。

---

## 二、系统架构

```mermaid
graph TB
    subgraph CLIENT["客户端入口"]
        WEB["浏览器<br/>Vue3 + Element Plus + ECharts<br/>（frontend/dist）"]
        NGINX["nginx :8180<br/>静态服务 + /api 反代 → api-gateway"]
    end
    subgraph GW["共享网关（共享 infra）"]
        GATEWAY["api-gateway :8099<br/>Host 虚拟域名路由 + X-Request-ID traceId<br/>按真实 IP 限流"]
    end
    subgraph PLATFORM["线下评测平台（backend 容器 :8000）"]
        subgraph L_API["API 层（api/，薄）"]
            API["REST 路由<br/>auth · users · agents · cases · runs · dashboard · config"]
        end
        subgraph L_ORCH["编排层（runner/）"]
            RUN["orchestrator 评测调度<br/>scanner 心跳回收 · scorer 汇总 · executor 执行"]
        end
        subgraph L_CAP["能力层"]
            JUDGE["judge/<br/>LLM-judge 六级 rubric 精评"]
            MET["metrics/<br/>accuracy · performance · cost"]
            ASRT["assertions/<br/>结构/文本/工具/检索断言算子"]
            ADPT["adapters/<br/>配置型驱动（base_url/接口/reset）"]
        end
    end
    subgraph DEP["外部依赖"]
        MYSQL[(MySQL 8（共享 infra）<br/>agent/用例/run/评分)]
        DS["DeepSeek LLM（judge）"]
    end
    subgraph TARGET["被评测 Agent（宿主机，任意接入）"]
        AGENT["标准契约<br/>SSE / 同步二选一 + POST /admin/reset"]
    end

    WEB --> NGINX
    NGINX --> GATEWAY
    GATEWAY --> API
    API --> RUN
    RUN --> JUDGE
    RUN --> MET
    RUN --> ASRT
    RUN --> ADPT
    ADPT -- host.docker.internal --> AGENT
    API --> MYSQL
    RUN --> MYSQL
    JUDGE --> DS
```

> **图读法（自顶向下）**：请求链路 浏览器 → nginx → 网关 → API → runner → 能力层；后端内「API 层（薄）→ 编排层 runner → 能力层」单向依赖，**runner 是唯一编排者**（调度 executor 逐 case、scanner 心跳回收、scorer 汇总），judge / metrics / assertions / adapters 只被 runner 调用；MySQL 与 judge LLM 属外部依赖，仅由后端访问。

**对外链路（统一 API 网关）**：浏览器只访问前端 nginx；nginx 将 `/api` 反代到共享网关 `api-gateway:8099`（`Host: eval.local`），网关按 Host 虚拟域名路由到本 agent 后端，并生成 `X-Request-ID`（后端日志 `trace_id` 即此值）、按真实 IP 限流。网关由共享 infra 仓库提供（`infra/api-gateway/`），未知 Host 一律 403 防串线。**P2-D12：backend 已收敛，不暴露宿主端口（无 `localhost:8100`），后端仅内网可达、对外唯一入口即前端 nginx → 网关**；宿主侧开发脚本经 `localhost:8180`（网关链路）或容器内 `8000`（`docker exec`）访问。

**关键链路**：触发评测（手动）→ 契约探测（L0 硬拦截）→ 逐 case：reset(seed) 造数归位 → 调用 agent 接口（采集 answer/usage/timing/tool_call）→ 断言 + judge 评分 → scorer 汇总（加权平均 + 维度门禁 + run 终态）→ 门禁墙呈现 + 未达标摘要 → PDF 报告导出。

---

## 三、快速开始（3 步跑起来）

> 前置：Docker Desktop（Linux 容器）、Python 3.11。
> **共享 infra**：本 agent 不自带任何中间件（仅依赖共享 infra 的 MySQL）。启动前先部署 infra（见 infra 仓库 README：`docker compose up -d`）。

### 第 1 步：配置环境变量

```bash
cp .env.example .env
# 编辑 .env，至少填入（缺失则启动直接报错）：
#   DB_PASSWORD=xxx        # 共享 infra 的 ai_evaluation 库密码
#   JWT_SECRET=xxx         # ≥256bit（JWT 签名）
#   FERNET_KEYS=xxx        # 逗号分隔多代密钥
# 可选：
#   JUDGE_API_KEY=xxx      # judge LLM API Key（语义维度评分必需）
#   ADMIN_PASSWORD=xxx     # 空库重建 admin 初始密码
#   AGENT_AUTH_SECRETS=xxx # P2-D17：被评 agent 调用凭证（JSON，key=agent name；
#                          #   如 {"customer-service":{"username":"admin","password":"…"}}；
#                          #   缺省则被评 agent 按匿名调用，seed_data 不含明文）
#   DEMO_EVALUATOR_PASSWORD=xxx / DEMO_VIEWER_PASSWORD=xxx  # P2-D18：seed 建 demo 账号密码（缺省不建）
#   SMOKE_PASSWORD=xxx     # P2-D18：verify_fresh_start 改密后统一密码（验证脚本运行期）
```

> **P2-D18 验证脚本凭据约定**：`backend/` 下验收/监控脚本（`verify_*.py`、`trigger_*.py`、`expand_*.py`、`monitor_run.py` 等）一律不入库、不写死凭据——登录密码从对应 env 读取（evaluator→`DEMO_EVALUATOR_PASSWORD`、viewer→`DEMO_VIEWER_PASSWORD`、admin→`ADMIN_PASSWORD`；DB 直连→`DB_PASSWORD`），缺 env 即 fail-fast。宿主跑读项目根 `.env`，容器内跑读 compose 注入的同一批 env。

### 第 2 步：启动应用容器（backend + frontend）

```bash
docker compose up -d --build
# backend 启动时自动执行 alembic upgrade head 建表（幂等：存量库已到 head 无操作）
# 依赖共享 infra MySQL 就绪；MySQL 未起时 backend 可能 crash-loop，先起 infra 再起本编排
docker compose ps                 # ai-eval-backend / ai-eval-frontend 全部 Up
curl localhost:8180/healthz       # {"status":"ok"}（经前端 nginx → 网关）
```

**平台端口**（宿主侧）：

| 容器 | 宿主端口 | 容器内 |
|------|---------|--------|
| backend | 无（P2-D12 收敛，仅内网） | 8000 |
| frontend | 8180（`FRONT_HOST_PORT`） | 80 |

> 本 agent 只起应用容器；MySQL 在共享 infra（库 `ai_evaluation`）。

> **P2-E8 Linux 部署上线检查**：容器以非 root（uid 10001）运行，落盘目录权限取决于宿主挂载目录。
> Docker Desktop（Windows）挂载权限宽松无需处理；**Linux 部署需宿主先授权**，否则容器内上传/报告导出
> 因无写权限失败：
> ```bash
> chown -R 10001:10001 uploads backend/exports   # uploads=用例附件、backend/exports=导出报告临时文件
> ```

**备份（P2-D14）**：`scripts/backup_db.ps1` 全库备份（共享 infra MySQL 容器内 mysqldump，`--single-transaction --no-tablespaces`，密码从 `../infra/.env` 读，不硬编码），滚动保留最近 14 份。

```powershell
powershell -ExecutionPolicy Bypass -File scripts/backup_db.ps1   # → backups/ai_evaluation_YYYYMMDD_HHMMSS.sql
```

恢复：`docker exec -i shared-mysql mysql -h127.0.0.1 -uevaluation -p<PASS> ai_evaluation < backups/xxx.sql`。

### 第 3 步：初始化数据（seed 手动执行）

> seed 不会在启动时自动跑，需手动执行一次（幂等 upsert，可重跑）。seed 会初始化默认系统配置与账号数据，登录即可使用。

```bash
docker compose exec backend python -m app.seed
```

**跑起来了**：

```bash
open http://localhost:8180        # 浏览器前端
```

| 角色 | 账号 | 密码 | 可做什么 |
|------|------|------|---------|
| 管理员 | `admin` | 由 `.env` 的 `ADMIN_PASSWORD` 注入 | 建 agent/接口、改全局配置、管理用户 |
| 评测员 | `evaluator` | 由 `DEMO_EVALUATOR_PASSWORD` 注入（缺省不建） | 触发评测、标注用例 |
| 查看者 | `viewer` | 由 `DEMO_VIEWER_PASSWORD` 注入（缺省不建） | 只读看板 |

> 触发评测前需确认被评 agent 服务在线（标准契约 + `POST /admin/reset` 就绪），且宿主侧对应端口未被其他服务占用。
> **跑起来之后怎么用**（登录/角色/看板/触发评测/逐层下钻/导出报告）→ [用户使用指南](docs/用户使用指南.md)。

---

## 四、使用场景与示例

### 4.1 给谁带来什么

**对评测方（QA / 评测工程师）**
- **门禁把关**：门禁墙按 agent 汇总通过率与均分，未达标用例一眼可见；
- **证据级回放**：L4 证据含 agent 最终回答、思考链全文、judge 判定理由、断言详情、工具调用、usage/timing，无需复现即可定位问题；
- **报告导出**：run 明细一键导出 PDF，审计留档；token 明文仅返回一次，DB 存 sha256。

**对 agent 开发方**
- **量化得分**：agent_score / 每维度分（完成度/事实性/思考链/工具使用）0-100 制，未达标维度明确列出「低于达标分 70」；
- **judge 判定理由**：每条不达标带 judge 逐维度判定依据，可回灌修 agent，而非一句「分低」；
- **回归对比**：按 run 对比版本得分走势，改造前后一目了然。

**对管理者**
- **看板汇总**：门禁墙 / 评分趋势 / 性能趋势 / 成本趋势四视图；
- **基线对比**：最近 run × 接口 × 维度 vs 达标分，gap 即差距。

### 4.2 一次评测怎么走（平台使用主流程）

平台的使用是一条主线，从接入到出报告共五步：

```
① 接入 agent   Agent 管理 →「快捷接入」向导（或手动新建 agent）
② 准备用例     用例管理：新建测试集 → 新建用例 → 打 gold 标
③ 触发评测     看板「触发评测」→ 选 agent / 测试集 / 版本 → 确认
④ 看结果       评测记录 → 明细 → 门禁墙 L0 → 用例明细 L3 → 证据回放 L4
⑤ 导出报告     run 明细页「导出报告」→ PDF 留档 / 汇报
```

- 每个用例 0-100 分 + 各维度分，L4 证据回放能看到 **judge 判定理由**（为什么扣分）；
- 想验证「改造是变好还是变坏」：同配置再触发一次，用「版本对比」选两次 run；
- 每一步的操作细节（角色权限、run 状态含义、结果解读）见 [用户使用指南](docs/用户使用指南.md)，15 分钟完整跑通见其第四章。

### 4.3 接一个新 agent

平台是**公开、统一标准**的：任何 agent 满足标准契约即可接入，平台零特判。接入完整教程（manifest v2 + SSE/同步契约 + 平台注册 6 步 + 离线自测）见：

- **Agent 接入评测指南**（面向接入工程师，从零走通）→ [docs/Agent接入评测指南.md](docs/Agent接入评测指南.md)
- **接入指南**（manifest v2 + 标准契约完整规范，含 4 家已接入样例）→ [docs/接入指南.md](docs/接入指南.md)

---

## 五、接入 Agent

本平台是**公开、统一标准的评测系统**：任何 agent（内部自研或第三方）只要满足平台定义的标准契约即可接入评测，平台对 agent 零特判。接入方 agent 独立部署（各自 docker compose），通过 `host.docker.internal` 被平台访问。

| 接入要求 | 说明 |
|---------|------|
| 标准契约 | SSE 变体（`usage`/`done` 必选 + `meta` 建议首事件 + `id:` 帧 + data 内 `ts`）或同步变体（`answer`/`usage`/`timing`，不验 done）二选一，判定口径以 `backend/app/core/probe.py` 为准 |
| 造数重置 | 提供 `POST /admin/reset`，跑前重置业务数据，保证用例可重复 |
| 鉴权开关 | 评测环境鉴权走 env 开关旁路，方便编排压测 |

> agent 侧契约改造与回归在各 agent 仓库完成，不在本仓库。契约探测不达标 → run 直接 partial_failed（硬拦截），接入即用。
>
> **接入教程**：面向完全不了解平台的人，从「平台怎么评你」讲到「注册 6 步」+ 离线自测 → [Agent 接入评测指南](docs/Agent接入评测指南.md)；技术规范权威来源（manifest v2 + SSE §5.1 / 同步 §5.2 达标细节 + 通用自测工具 `scripts/verify_agent.py`）→ [接入指南](docs/接入指南.md)。

---

## 六、技术闪光点

### 1. 强契约驱动 + 平台定标准（agent 只适配，平台零特判）
平台定义标准契约（SSE 变体：`usage`/`done` 必选 + `meta` 建议首事件 + `id:` 帧 + data 内 `ts`；同步变体：`answer`/`usage`/`timing` 必选，不验 done；判定口径以 `backend/app/core/probe.py` 为准），接入方 agent 按标准收敛改造（标准契约 + `POST /admin/reset` 造数重置接口 + 鉴权 env 开关）。**平台侧禁止 `if agent` 特判**——发现/校验只认标准契约信号，契约探测不达标直接拦截报错，保证任意新 agent 接入即用。

### 2. 规则 + LLM-judge 混合评分
- **确定性断言**：结构/文本/工具/检索四类断言算子（白名单插件注册），扣分制不否决，维度内精评；
- **LLM-judge**：语义维度（factuality/reasoning）用六级锚点 rubric（1.0/0.8/0.6/0.4/0.2/0.0）+ 理由，不输出模糊连续分；
- **分数合成**：加权平均 + 每维度阈值门禁（pass/fail），N/A 剔除重归一化，error 率硬门禁。

### 3. 金标准 = 校准集，驱动基线对比
`is_gold` 用例是绝对水平基线：baseline_target 从金标准校准集自动标定（接口 × 维度 vs 达标分），`golden_answer` 同时作为 judge 判分的事实参考。

### 4. run 状态机 + 多 worker 跨进程 DB 化
run 终态由 `scorer` 汇总（`partial_failed` 只认执行/技术 error，`fail` 未达标不改变终态）；scanner 后台心跳租约 + 硬超时回收；跨进程状态全部 DB 化：run 互斥（agent 行 `FOR UPDATE`）、reset(seed) 锁、熔断（`agent_circuit`）、scanner 单例（`GET_LOCK`）——多 worker 部署也不丢状态。

### 5. 门禁墙逐层下钻（L0 → L4）
| 层 | 内容 | 作用 |
|----|------|------|
| L0 | 门禁墙（agent 级汇总） | agent 均分 / 通过率 / 用例数；跑前契约探测不达标 → run 直接 partial_failed（硬拦截） |
| L3 | 用例明细 | 每用例得分 + 每维度分 |
| L3.5 | 未达标摘要 | 未达标用例 + 维度分 < 达标分的逐条判定理由 |
| L4 | 证据回放 | agent 回答 / 思考链 / judge 判定 / 断言 / 工具调用 / usage |

### 6. 插件化评测
维度（completeness/factuality/reasoning/tool_usage + ttft/e2e/cost）、adapter（配置型声明 base_url/接口/reset/conversation_id，通用引擎执行）、rubric、断言算子四者均可插拔——新增评测维度不碰核心代码。

### 7. 成本透明 + 单价可配
agent 透出真实 usage（不估算），× 模型单价表（元/百万 token）算成本；`model-prices` 支持峰谷价（deepseek-chat 空闲 1.5/4.5），成本面板展示趋势 + 明细 + 单价。

### 8. 安全与合规基线
- **RBAC 三角色**：admin / evaluator / viewer + JWT；评测触发需 Staff；
- **SSRF 白名单**：`DEFAULT_AGENT_CIDRS` 代码常量与 DB seed 双处同步，agent 直连仅内网；
- **安全响应头**：CSP 收紧 `default-src 'none'`、`X-Frame-Options: DENY` 等；
- **报告安全**：token 明文仅返一次、DB 存 sha256、PDF 页脚水印；
- **插件白名单**：断言算子 class_path 白名单校验。

### 9. 工程化细节
- 后端容器热挂载源码（改代码 `docker compose restart backend` 即生效，无需 rebuild）；
- 宿主跑 pytest 有环境开关（`RUN_INTEGRATION=1` 才连库，默认 integration 自动 skip）；
- 场景清单由 seed 固化，用例标注（gold / 场景 / 断言）走用例管理页。

---

## 七、技术栈一览

| 层 | 技术 | 说明 |
|----|------|------|
| 后端 | Python 3.11 + FastAPI | async/await，单进程 uvicorn（workers=1），scanner/judge 后台协程 |
| ORM/迁移 | SQLAlchemy 2 (async) + Alembic | 异步 ORM，run 快照（case_version 表）保证历史自包含 |
| 前端 | Vue3 + Vite + Element Plus + Pinia + ECharts | 5 个业务页面（看板/配置/Agent/用例/用户），JS 非 TS |
| 数据库 | MySQL 8 | utf8mb4，业务权威数据源 |
| LLM judge | DeepSeek（openai 兼容） | 语义维度六级 rubric 精评 |
| 插件 | 断言算子白名单 + 配置型 adapter | 可插拔评测维度 |
| 测试 | pytest + pytest-asyncio | 单元 / 集成（`RUN_INTEGRATION=1` 连库） |
| 部署 | docker compose | backend + frontend 两容器 + 共享 infra MySQL |

---

## 八、配置说明

### 8.1 环境变量（`.env`，完整模板见 `.env.example`）

| 变量 | 必填 | 说明 |
|------|:---:|------|
| `DB_PASSWORD` | ✅ | 共享 infra 的 `ai_evaluation` 库密码 |
| `JWT_SECRET` | ✅ | JWT 签名密钥，≥256bit |
| `FERNET_KEYS` | ✅ | Fernet 对称密钥，逗号分隔多代 |
| `JUDGE_API_KEY` | ⬜ | judge LLM API Key（语义维度评分必需） |
| `ADMIN_PASSWORD` | ⬜ | 空库重建 admin 初始密码 |
| `AGENT_AUTH_SECRETS` | ⬜ | 被评 agent 调用凭证（JSON，key=agent name；缺省按匿名调用） |
| `DEMO_EVALUATOR_PASSWORD` / `DEMO_VIEWER_PASSWORD` | ⬜ | seed 建 demo 账号密码（缺省不建） |
| `SMOKE_PASSWORD` | ⬜ | `verify_fresh_start` 改密后统一密码（验证脚本运行期） |

### 8.2 配置中心（运行期热改，管理员页）

运行期参数在**配置中心**改，多为**热生效**（改完即时生效，部分需重启，界面会标明）：

| 配置 | 作用 |
|------|------|
| judge LLM 地址 / 模型 | 判分大模型指向（密钥走环境变量） |
| judge 重复判分次数 | 同一题判几次取多数决（默认 3），越多越稳、越贵 |
| judge 并发 / 调用超时 | 判分并发度与单次超时 |
| judge 判分缓存 / TTL | 跨 run 复用历史判分（进程内 LRU）；模型漂移时热关强制重新判分 |
| 单用例超时 / 整 run 超时 | 评测执行时限 |
| judge 出站白名单 | 允许 judge 访问的 LLM 域名（SSRF 安全） |
| 全局并发 / 单 agent 并发 | 平台同时运行的评测数与单 agent 内并行用例数上限 |

---

## 九、目录结构

```
ai-evaluation/
├── backend/                 # 后端源码（FastAPI）
│   ├── app/
│   │   ├── main.py          # 应用入口（scanner + judge worker 协程）
│   │   ├── api/             # REST 路由（auth/users/agents/cases/runs/dashboard/config/...）
│   │   ├── runner/          # orchestrator / scanner / scorer / executor（评测执行链路）
│   │   ├── judge/           # LLM-judge 六级 rubric 精评（含跨 run 判分缓存）
│   │   ├── metrics/         # 维度评分（accuracy/performance/cost 三类）
│   │   ├── assertions/      # 断言算子（结构/文本/工具/检索 + 白名单注册）
│   │   ├── adapters/        # 配置型 adapter（base_url/接口/reset/conversation_id）
│   │   ├── models/          # SQLAlchemy 模型
│   │   └── core/            # 配置/安全(JWT)/错误/审计/探测/限流/重试
│   ├── tests/               # 单元 + 集成（RUN_INTEGRATION=1 连库）
│   ├── alembic/             # 数据库迁移
│   └── requirements.txt
├── frontend/                # 前端（Vue3 + Vite + Element Plus + Pinia）
│   ├── src/
│   │   ├── views/           # Dashboard（性能/成本/覆盖三面板）/Agents/Cases/Config/Users
│   │   ├── api/             # axios 接口模块
│   │   ├── components/      # EChart / TermTip 术语引导
│   │   ├── constants/       # configMeta / terms（配置中心与 seed 双处同步）
│   │   └── utils/           # runFilter / caseParse
│   └── nginx.conf           # 静态服务 + /api 反代
├── uploads/                 # 文件型用例自存文件（宿主 ↔ 容器挂载）
├── scripts/                 # seed / 初始化脚本
├── docs/                    # 用户使用指南 / Agent 接入评测指南 / 接入指南
├── docker-compose.yml       # 2 容器编排（backend+frontend）+ 共享 infra MySQL
├── .env.example             # 环境变量模板
├── prd.txt                  # 产品需求
├── solution.md              # 技术方案（v0.3，88 项决策）
├── solution_detail.md       # 技术方案明细（v1.2，含遗留问题清单）
├── task.md                  # 任务拆分与验收（前置 A/B + 阶段一~七）
└── README.md
```

---

## 十、测试与验收

**四家 agent V2 门禁全部通过（2026-08-31）**：发布前门禁评测（门禁判定规程 V2：规则维度全过 + judge 2/3 多数决 + run 级 agent_score 均值 ≥85），4 家现有 agent 全 PASS——

| agent | 复测 run | 结果 | 均分 |
|---|---|---|---|
| gq（2300）| 2558/2559/2560 | 16 case 全 PASS | 99.46 |
| cc（2299）| 2561/2562/2563 | 10 case 全 PASS | 100.0 |
| sp（2297）| 2564/2565/2567 | 9 case 全 PASS | 92.48 |
| cs（2298）| 2566/2568/2569 | 17 case 全 PASS | 97.12 |

gq 是唯一需门禁 target 调整方（factuality 90→80、reasoning 85→80——judge 6 档步进下 85/90 等价必须满分档，评语正面的「轻微瑕疵」稳定 80 被误伤）；其余三家 target 现状无需调整。

**实测全绿（2026-09-01 全量回归）：后端单测 781 + 集成测试 95 + 前端单测 40 = 916 项（pytest / vitest 收集的 test 函数计数）。**

后端单测覆盖（`backend/tests/`，781 项）：

- **runner 链路**：orchestrator（重试 / 限流 / per-run 桶）、executor、scorer、run 超时、契约探测 probe、engine、assembler；
- **judge / metrics**：judge 六级评分、LLM 调用、metrics 维度（factuality/reasoning/completeness/tool_usage + ttft/e2e/cost）、跨 run 判分缓存；
- **断言 / 插件**：四类断言算子、断言白名单、插件注册；
- **平台能力**：仪表盘聚合（门禁墙 / 覆盖 / 基线）、基线达标分、标注、审计、安全（响应头/SSRF）、熔断器、幂等清理、限流接线。

集成测试（`tests/integration/`，95 项，`RUN_INTEGRATION=1` 才连库）：完整 run 执行、scanner 回收、7.6 多 worker 互斥。

前端单测（vitest，40 项）：`utils/` 纯函数（runFilter / caseParse / meta 常量）+ `Onboarding.test.js` wizard 组件测试（5 项）。

**跑测试**：

```bash
# 后端单测（不连库，宿主直接跑）
cd backend
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -p pytest_asyncio.plugin tests/ --ignore=tests/integration

# 集成测试（需连库）。Windows 宿主无法直达 docker bridge IP，需 socat 转发
# （详见 tests/integration/conftest.py；转发容器已存在则复用，DB_PORT 按实际转发端口）：
# 共享 infra MySQL 已映射宿主 33061，Windows 宿主可直接连接（无需 socat）：
cd backend
$env:RUN_INTEGRATION='1'; $env:DB_HOST='127.0.0.1'; $env:DB_PORT='33061'; $env:DB_PASSWORD='<密码>'
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -p pytest_asyncio.plugin tests/integration

# 备选：若宿主连库被拒（1045 Access denied），改在 backend 容器内临时装 pytest 跑
# （连 mysql:3306，来源为容器 IP 不受拒）：
docker exec ai-eval-backend pip install pytest pytest-asyncio
docker exec ai-eval-backend env RUN_INTEGRATION=1 python -m pytest -p pytest_asyncio.plugin tests/integration

# 前端单测
cd frontend && npm run test
```

---

## 十一、开发指南

### 环境
```bash
cp .env.example .env
docker compose up -d --build        # 起平台
docker compose exec backend python -m app.seed   # 初始化数据
```

### 改后端代码
- `backend/` 以只读卷热挂载进容器（`./backend:/app`），**改代码 `docker compose restart backend` 即生效**，无需 rebuild；
- 改依赖（requirements.txt）才需 `docker compose build backend`。
- 改了 orchestrator/scorer 等核心逻辑后必须 restart backend（uvicorn 不自动重载）。

### 改前端代码
- 前端是构建产物进 nginx，**改 `frontend/src/` 必须 `docker compose build frontend && docker compose up -d frontend`** 重建镜像。

### 跑测试 / 验收
- 单测不连库直接跑；集成测试需 `RUN_INTEGRATION=1` 且平台容器 + 被评 agent 栈在线；
- 提交前先跑单测，确保不破坏既有用例。

### 新增评测维度
- metrics 子包新增维度（继承 base + 注册到 registry）→ 配置启用 → 前端展示映射 → seed 配置项同步。

### 新增接口
- api 子包新增 router → 注册到 `main.py` → 前端 api/ 模块 → 权限按角色；
- **契约优先**：涉及 `*Api.java` 等对外契约变更需架构组批准（本项目遵循）。

### 数据库变更
- 改模型后 `docker compose exec backend alembic revision --autogenerate -m "描述"` → `alembic upgrade head`；
- 新增 `system_config` 项需手动补 seed 才进库（seed 不自动跑）。

### 编码规范（约定）
- 4 空格缩进、阿里 Java 规范思想；注释写「为什么」、中文注释、英文标识符；
- 新增接口/消费者打印入参出参（debug 级）；核心逻辑（Service 业务分支）必须单测覆盖。

---

## 十二、常见问题

| 现象 | 处理 |
|------|------|
| 登录提示账号锁定 | 试错触发登录滑动窗口锁定，等窗口过或用管理员重置 |
| 前端 8180 白屏 | 前端改过代码未 rebuild：`docker compose build frontend && docker compose up -d frontend` |
| 改后端代码不生效 | 容器挂载源码但 uvicorn 不自动重载：`docker compose restart backend` |
| 跑真实评测 agent 连接失败 | 先确认被评 agent 服务在线（标准契约 + `POST /admin/reset` 就绪），宿主端口未被占用 |
| 触发评测后 run 立刻「部分失败」 | 大概率契约探测未通过（agent 接口协议不达标）或 agent 不在线，看 run 明细的失败原因 |
| 宿主跑 pytest 报 pytest_html 缺 py.xml | 加 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` + `-p pytest_asyncio.plugin` |
| 集成测试连库失败（2003） | 先确认共享 infra MySQL 已启动（`docker start shared-mysql`）；Windows 宿主直连 `127.0.0.1:33061` 即可，无需 socat |
| httpx 报 localhost 502（后端无日志） | 宿主 Clash 系统代理写进注册表，httpx trust_env 读到 → 验证脚本一律 `trust_env=False` |
| run 显示「完成」但有用例未达标 | 语义如此：`partial_failed` 只认执行/技术 error，`fail`（未达标）不改变终态；看黄色「N 未达标」tag |
| 报告导出 token 泄露担忧 | token 明文仅返回一次、DB 存 sha256，导出走带鉴权的 /api/exports |
| 数据库查数据 | 共享 infra MySQL 宿主直连：`mysql -h127.0.0.1 -P33061 -uevaluation -p<密码> ai_evaluation`（密码见 infra/.env `MYSQL_EVAL_PASSWORD`） |

---

## 十三、已知限制与优化方向

**如实说明当前已知的边界问题：**

1. **单进程部署（workers=1）**：scanner / judge worker 是后台协程，执行器并发受单进程限制。跨进程状态已全部 DB 化（run 互斥 / 熔断 / scanner 单例），理论上可多副本，但当前编排固定单实例；高并发评测只能通过配置中心调大「同 agent 并发 run 上限」×「单 agent 并发」。
2. **judge 判分成本与缓存**：同一用例 judge 判 N 次（默认 3）取多数决，越稳越贵。跨 run 判分缓存（`judge_cache_enabled`，进程内 LRU）复用历史判分降低重复成本——但命中时证据回放的 `repeats` 呈现为相同多数决值（非 N 次独立采样），且 TTL（默认 24h）只兜底 judge 模型行为漂移，极端情况下历史判分可能滞后于模型行为变化。模型漂移时**热关缓存**强制重新判分。
3. **前端构建产物进 nginx 镜像**：改前端代码需 rebuild 镜像（`docker compose build frontend`），体验不如后端热挂载。
4. **集成测试依赖真实 MySQL**：`tests/integration/` 需 `RUN_INTEGRATION=1` + 共享 infra MySQL（Windows 宿主直连 `127.0.0.1:33061`），CI 环境需自带库，无法纯内存跑。

---

## 文档索引

- **用户使用指南**：[docs/用户使用指南.md](docs/用户使用指南.md)（面向使用者：登录与三角色 / 15 分钟跑通 / 五大页面 / 结果解读）
- **Agent 接入评测指南**：[docs/Agent接入评测指南.md](docs/Agent接入评测指南.md)（面向接入工程师：契约 / manifest / 平台注册 6 步 / 离线自测）
- **接入指南**：[docs/接入指南.md](docs/接入指南.md)（manifest v2 + 标准契约完整技术规范，含 4 家已接入样例）
- **产品需求**：[prd.txt](prd.txt)
- **技术方案**：[solution.md](solution.md)（v0.3，88 项决策）
- **方案明细**：[solution_detail.md](solution_detail.md)（v1.2，含遗留问题清单）
- **任务拆分与验收**：[task.md](task.md)（前置阶段 A/B + 阶段一~七）
