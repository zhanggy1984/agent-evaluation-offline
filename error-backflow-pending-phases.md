# error-backflow 待办分解（阶段 · 明细项 · 批次）

> **本文件是什么**：把「当前还缺什么」一次性列全的**派生视图**——按阶段归拢、按验证面切成可单独出方案的批次。
> **本文件不产生新事实**：每条明细项的权威来源仍是它自己的台账（下表「来源」列）。
> **冲突时以来源台账为准**，本文件只负责「不漏」。
>
> **落成时间**：**2026-09-15**。**状态标记一律带「当时」框架**——本文件的任何「已做/待做」只对
> **2026-09-15** 成立；引用前请先按 §7 的复核方式重跑命令，不要凭本文件的字面当现状。

---

## 0. 本会话已拍板的决定（**不得回退**）

| # | 决定 | 依据 |
|---|---|---|
| D1 | **甲/乙 分流判据 = 「当前是否还有对象要动」**（不是「分流当刻判要动」） | 用户 2026-09-15 拍板 |
| D2 | **P2-1 先行于 P0-2** | 证据的时态：P2-1 有现在时证据（见 §3.1），P0-2 只有将来时（pre-scan 实测零空答） |
| D3 | **`/gate` 的 `total_case`/`pass_rate` 分母 = 排除 `error_regression`** | error run 的 case 集是 error case，与常规/留出**不同源** |
| D4 | **P2-1 范围 = 只动 `/gate` + `/trend`** | 实测只有这两面有非空数据对象；其余四面**无对象**（见 §3.2） |

---

## 1. 阶段与明细项

### P0 — 门禁自证

| 编号 | 对象 | 来源 | 级别 | 当时状态（2026-09-15） |
|---|---|---|---|---|
| **P0-1** | `R-20` pre-scan 报告（G0 出口物） | offline `error-backflow-task.md` O-A.1 · `solution_detail.md` §9.4 | 交付物 | **已产出** = `error-backflow-pre-scan-report.md`（仓根，**未提交**） |
| **P0-2** | `R-12`：`KeywordNotContainsOp` 空答语义 PASS→FAIL | offline `error-backflow-task.md` O-E.9 | **A**（`text.py` 为 manual/held_out 与 error_regression **共享算子**） | **G0 已解锁，待做**（任务 #258） |

### P1 — 出站与回收（**两条都跨仓**）

| 编号 | 对象 | 来源 | 级别 | 当时状态（2026-09-15） |
|---|---|---|---|---|
| **P1-1** | 出站 **401 不重试**（`O-F.8` 判据「secret 缺失/错误 → 出站被拒**且不重试**」） | offline `error-backflow-task.md` O-F.8 | **A**（要动 `BackflowClientError` 形态 = 跨模块签名） | **缺口确证**：异常只带 message、**不携带状态码** ⇒ 调用方无法按码分流 ⇒ 401 照样重试 3 次后放弃 |
| **P1-2** | `R-26` / `O-F.9` 双 `Host` 头（容器名出站整体不可用） | offline `error-backflow-task.md` O-F.9 | **A**（根因在 `core/http.py` 共享客户端，**影响面不限本特性**） | **缺口确证**：现为**绕过**（`backflow_client._client()` 显式入白名单），根因未修；代价 = 该分支**跳过 IP 解析校验** |

### P2 — 看板取数面（**两条不同链**）

| 编号 | 对象 | 来源 | 级别 | 当时状态（2026-09-15） |
|---|---|---|---|---|
| **P2-1** | `api/dashboard.py` 的 `/gate` + `/trend` 排除 `error_regression` | `O-F.4`（**部分**） | B（两行谓词） | **缺口确证**；口径/范围已拍（D3/D4）；**方案已出**（见 §5） |
| **P2-2** | `O-F.4` **余项**：写守卫谓词单一来源 + `cleanup` 豁免核对 + 403 全清单核对 | offline `error-backflow-task.md` O-F.4 | B | **缺口确证**：三常量 `CASE_TYPE_IS_NULL` / `IS_ERROR_SUITE_FALSE` / `TRIGGER_NOT_ERROR_REGRESSION` 在 `backend/` **grep 零命中** |

### P3 — 登记项（**不进排期**，登记不拍板）

| 编号 | 对象 | 来源 |
|---|---|---|
| **P3-1** | `T-4.15` 7d 聚类窗 | online `task.md`（**未拍板**） |
| **P3-2** | `F-2` 降格声明**无 `task.md` 载体** | online `docs/integration-report.md:140` |
| **P3-3** | 详设 §9.4 把 `assertion_results` 写成**表**（实为 `eval_result` 的 **JSON 列**） | 本轮 pre-scan 新发现 |
| **P3-4** | `O-F.8` 正文的 secret 名（`backflow_outbound_secret` / `BACKFLOW_INBOUND_SECRET`）**已作废**，实配 `settings.evaluator_service_secret`（`core/config.py:38`） | 本轮复核新发现（**文档旧、实现对**） |
| **P3-5** | **error 通道 `usage` 全空**：84 条 `usage=[null]`、`total_tokens=0`、`total_cost` 全 NULL，而 `answer` **73 条非空** | **本轮新发现，待取证**——未读 error 通道 `_save_result` 与 usage 抽取路径，**不判定** |

### P4 — 验收覆盖

| 编号 | 对象 | 当时状态（2026-09-15） |
|---|---|---|
| **P4-1** | `T-3.17` 四仓镜像烤码早于本批 + `T-3.18`/cs 各 2 条真机未复验 | 卡在「镜像早于本批」；`task.md:299/:300` **台账自陈未验收** |
| **P4-2** | offline 卡 G0 的六行 | **已解锁**（随 P0-1） |
| **P4-3** | `T-5.x` 缺真实 agent | 缺口 |

---

## 2. 批次切分（**按验证面切**，每批 = 一份方案 + 一次独立验证）

| 批 | 含明细项 | 验证面 | 前置 |
|---|---|---|---|
| **批 1** | P2-1 | **真库 + 反事实对照**（改的是 SQL 谓词，纯函数单测覆盖不到） | 无（**方案已出**） |
| **批 2** | P0-2 | **存量回归 + 注入对照**（共享算子，须防误伤 manual/held_out） | G0 ✅ |
| **批 3** | P1-1 | **出站异常分流**（可桩化到单测层） | — |
| **批 4** | P1-2 | **`core/http.py` 传输层**（必须真机：双 Host 头只有实发才现形） | — |
| **批 5** | P2-2 | **写端点 403 全清单** | — |
| **批 6** | P4-1 | **镜像重建后复验** | 需重建四镜像 |

> **批 3 与批 4 同属出站却拆开**：一个验「分流逻辑」，一个验「传输层实际不再带双 Host」。**验证面不同**，能证明的东西不同，不能合并。
>
> **批内不掺下一批的改动**（哪怕码已写好）。

---

## 3. 取证结论（2026-09-15，`ai_evaluation` 库）

### 3.1 error run 存量（P2-1 是**现在时**缺陷，非潜在缺陷）

`eval_run` **292** 行，其中 `error_regression` **125** 行、**113 行已终态**
（`completed` 68 / `partial_failed` 15 / `cancelled` 21 / `timeout` 9）。

`version` 桶混入 `manual` + `error_regression` 的 **≥20 个**（`0.1.0`/`0.2.0`/`0.3.0`/`1.16.0`/`2026.09.14-c3v1*` 系列…）。
`eval_result.pass_fail='error'` **5 条**（salvage 污染路径**真发生过**）。

### 3.2 但**污染面只有 2 个**（订正先前的「七个面都混」）

| 面 | error run 在该面的数据 | 结论 |
|---|---|---|
| `/gate` | `total_case`/`pass_case`/`fail_case` **非空** | **有对象 → 要排** |
| `/trend` | run 级 `total_case`/`pass_case`/`error_case`/`pass_rate` **非空** | **有对象 → 要排** |
| `/perf` | `ttft_p50`/`e2e_p50` 在 125 条 error run 上 **全 NULL** | **无对象 → 不改** |
| `/cost` | `total_cost` **全 NULL**；`total_tokens` 73 条非空**但值全为 0** | **无对象 → 不改** |
| `/compare` | `score_per_dimension` **84/84 全 NULL**（`score_total` 同） | **无对象 → 不改** |
| `_agent_dim_series`（2σ 源） | 同上，`na/None` 本就被剔 | **无对象 → 不改** |

**另一处订正**：`error_regression` 的 `agent_score` **125/125 全 NULL** ⇒ 先前「**门禁分**被污染」**不成立**；
成立的只有「`pass_rate` **分母**被做大」。

### 3.3 复算命令（数字与上述结论的一致性校验）

```bash
# ① error run 存量与终态分布
docker exec ai-eval-backend python -c 'import os;from sqlalchemy import create_engine,text;u=os.environ["DB_USER"];p=os.environ["DB_PASSWORD"];h=os.environ["DB_HOST"];P=os.environ["DB_PORT"];d=os.environ.get("DB_NAME") or "ai_evaluation";e=create_engine(f"mysql+pymysql://{u}:{p}@{h}:{P}/{d}");c=e.connect();print(c.execute(text("select trigger_type,coalesce(status,\"NULL\"),count(*) from eval_run group by 1,2 order by 1,2")).fetchall())'

# ② version 桶混用
#    把上一行的 select 换成：
#    select version,group_concat(distinct trigger_type) from eval_run group by version having count(distinct trigger_type)>1

# ③ 三个面无对象（ttft/cost/dim 一起）
#    select r.trigger_type,count(*),sum(er.score_per_dimension is not null),sum(er.total_cost is not null)
#    from eval_result er join eval_run r on r.id=er.run_id group by 1
#    select trigger_type,count(*),sum(ttft_p50 is not null) from eval_run group by 1

# ④ P2-2 三常量零命中
grep -rn "TRIGGER_NOT_ERROR_REGRESSION\|CASE_TYPE_IS_NULL\|IS_ERROR_SUITE_FALSE" backend/
```

> ⚠️ `DB_PASSWORD` 只经环境变量传递、**不回显**；**不得用 `sed` 脱敏 `docker inspect` 输出**（实测漏过密码）。

---

## 4. 未验清单（**显式**，不许被 §1 的确定语气盖过）

1. **P0-2 的适用面未定**：pre-scan 的 780 条 keyword 断言**未按 `trigger_type` 拆分** ⇒ 不知道其中多少来自 error run。「零空答」这条证据的覆盖面**比 §1 读起来窄**。
2. **P1-1 只读了两处**：客户端 `backflow_client.py` 与 `error_push.py`；**其余调用方是否另有分流未读**。
3. **P3-5 无成因证据**：只有「`usage` 全空 + `answer` 非空」这个对照，**任何解释都不许写成结论**。
4. **`/perf`、`/cost`、`/compare` 判「无对象」是取证结论，不是「验证过没问题」**——将来 error run 开始写 perf/cost 时，**这三面要回头重判**。
5. **P4-1 的四条真机项**本轮**未复验**，状态取自台账自陈（`task.md:299/:300`）。

---

## 5. 批 1（P2-1）方案摘要

- **改动**：`backend/app/api/dashboard.py` **两处**（`:43` `/gate`、`:60` `/trend`），照邻行 `held_out` 既有写法加**字面量**谓词。
- **不建共享常量**：`held_out` 在本文件有 7 处字面量，是既有惯例；只给 `error_regression` 建常量会造成不对称。「谓词单一来源」属 **P2-2**，不在本批。
- **不动 `build_gate_cards`**：`held_out` 的排除**只在 SQL 层**（`dashboard_rules.py:27-28` 只过滤 status）⇒ 与 `held_out` 同型。
- **验收**：A 真机探针（异步直调 `gate()`/`trend()`，断言 `total_case` **等于**「同 version 下 manual run 之和」——写成**等于谁**）；B **反事实对照**（去掉谓词必红）；C 全量单测；D ruff；E 文档回填。
- **不做**：`/perf`、`/cost`、`/compare`、`_agent_dim_series`、纯函数层、共享谓词常量、error 通道 usage 采集。

---

## 6. 现场状态（未提交 / 残留 / 待裁）

> 本节是「会丢的东西」的清单。**数字由 `git status --porcelain` 与 `ls` 当场产出**（2026-09-15），
> 引用前请重跑，**不要凭印象**。

### 6.1 提交状态（**2026-09-15 已提交，⚠️ 未推送**）

本会话成果已分 **3 笔**提交（两仓工作区**已干净**，`git status --porcelain` 空）：

| 仓 | 提交 | 内容 |
|---|---|---|
| offline | `1ab233f` | 台账对账订正（`error-backflow-status.md` + `error-backflow-task.md`，31 insertions） |
| offline | `0c34f19` | 新增 `error-backflow-pre-scan-report.md` + `error-backflow-pending-phases.md`（本文件） |
| online | `b66af1a` | 台账对账订正（`docs/integration-report.md` + `revision-design-register.md` + `task.md`） |

> 上述 3 笔的**内容** = 本会话的 **20 处 C 类台账对账订正**（online `task.md` 1 + `integration-report.md` 13 +
> `register` 1 + offline `status.md` 3 + offline `task.md` 2）+ 两份新报告。
> 另：**本节自身的自纠**（原写「未提交」，被上述提交当场证伪）**自成 1 笔**，哈希见 `git log -1`。

**⚠️ 三笔均未推送**（`git log @{u}..HEAD` 实测 = offline **2 笔** / online **1 笔**；用户未授权 push）
⇒ **「避免丢失」尚未完成**：本机仓库一旦丢失，这几笔随之丢失。**最后一步是 push。**

> ⚠️ **两个仓的 pre-commit 都打印「检查通过」——但那是弱保证**：该 hook **不含 ruff**，
> 且**覆盖率门禁段对 `backend/` 布局的仓是死代码**（零测试执行）。**不得读作 lint 通过**；
> **offline 仓无任何 CI**，唯一 lint 门禁是手动借 online venv + online 配置扫改动文件。

### 6.2 现场残留（**等用户决定，本文件不处置**）

- `D:\study\aiprojcet\_t25drift\` — **9 个文件**：
  `cc_img_obs.py` / `cs_4dfad19.py` / `cs_img.py` / `gq_5bd4e9c.py` / `gq_img.py` / `probe_cs_cancel.py` / `sp_img_obs.py` / `sp_src.bak` / `sp_test.bak`
- `%TEMP%\t25*` — **11 项**：`t25_base.sh` / `t25_flake.sh` / `t25_mutate.sh` / `t25app1` / `t25app_base` / `t25app_mut` / `t25base` / `t25cs` / `t25drift` / `t25drift;D` / `t25sp`
  - 其中 `t25drift;D` 疑为**误用 `;` 造成的畸形目录名**，属过去某次命令的副产物。

> 按全局约定：**不主动 `rm`**。清理与否由用户逐项决定。

### 6.3 待裁项（**已登记，未拍板**）

| # | 待裁 | 出处 |
|---|---|---|
| C-1 | `T-3.17` 三条：① 何时重建四仓镜像 ② 重建后**是否重跑** `integration-report.md` §2 序号 10 ③ 是否加**部署后置检查** | online `task.md` T-3.17 |
| C-2 | `R-20` G0 的 **选项 A / 选项 B**（接受 81/85 已观测面放行 vs 先补跑 4 个未跑 case） | `error-backflow-pre-scan-report.md` §5 |
| C-3 | **lint 门禁缺口**：ruff 本机不可用（三处 venv 均无）；**offline 仓无任何 CI** ⇒ 唯一门禁是**手动**借 online venv + online 配置扫改动文件 | 长期未验收项 |

---

## 7. 维护约定（**防本文件自身腐**）

1. **本文件是派生视图**。任何明细项的状态变更，**先改来源台账，再回填本文件**；不得只改本文件。
2. **本文件的每个「已做/待做」都带「当时（2026-09-15）」框架**。过时后**要么重跑 §3.3 命令回填、要么显式标注过期**，不许把裸标记留在原地——裸标记是对「现在」的断言，**注定腐且腐后像真话**。
3. **引用 §3 的任何数字前，先跑 §3.3 的命令**。本文件的数字**不随命令存续**，不跑即不可复核。
4. **§4 未验清单只能缩短**（验一个删一条）。**新增未验项必须同时进 §4**，不许只写在 §1。
