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
| **P0-1** | `R-20` pre-scan 报告（G0 出口物） | offline `error-backflow-task.md` O-A.1 · `solution_detail.md` §9.4 | 交付物 | **已产出** = `error-backflow-pre-scan-report.md`（仓根；入库于 `0c34f19`，**提交状态以 `git log --oneline -- error-backflow-pre-scan-report.md` 现跑为准**） |
| **P0-2** | `R-12`：`KeywordNotContainsOp` 空答语义 PASS→FAIL | offline `error-backflow-task.md` O-E.9 | **A**（`text.py` 为 manual/held_out 与 error_regression **共享算子**） | ✅ **已落地（2026-09-15，批 2）** —— 见 §5.2。（原记「G0 已解锁，待做」；G0 于当日判过，见 `error-backflow-pre-scan-report.md` §5.1） |

### P1 — 出站与回收（**两条都跨仓**）

| 编号 | 对象 | 来源 | 级别 | 当时状态（2026-09-15） |
|---|---|---|---|---|
| **P1-1** | 出站 **401 不重试**（`O-F.8` 判据「secret 缺失/错误 → 出站被拒**且不重试**」） | offline `error-backflow-task.md` O-F.8 | **A**（要动 `BackflowClientError` 形态 = 跨模块签名） | ✅ **已落地（2026-09-15，批 3）** —— 见 §5.3。（原缺口确证：异常只带 message、**不携带状态码** ⇒ 调用方无法按码分流 ⇒ 401 照样重试 3 次后放弃。**⚠️ 该缺口描述的适用范围偏宽**：重试只存在于 push 路径，`pull_loop` 两处捕获本就无立即重试） |
| **P1-2** | `R-26` / `O-F.9` 双 `Host` 头（容器名出站整体不可用） | offline `error-backflow-task.md` O-F.9 | **A**（根因在 `core/http.py` 共享客户端，**影响面不限本特性**） | **✅ 已完成（2026-09-15）**，详见 §5.5：**根因已修**（`send()` 摘掉原 Host 项再加），**绕过已撤除**（`_client()` 不再显式入白名单）⇒ 恢复完整的「解析全部 IP 并校验在内网段内」。**提交与推送状态以 `git log @{u}..HEAD` 现跑为准** |

### P2 — 看板取数面（**两条不同链**）

| 编号 | 对象 | 来源 | 级别 | 当时状态（2026-09-15） |
|---|---|---|---|---|
| **P2-1** | `api/dashboard.py` 的 `/gate` + `/trend` 排除 `error_regression` | `O-F.4`（**部分**） | B（两行谓词） | **✅ 已完成（2026-09-15）**：口径/范围已拍（D3/D4），方案见 §5、验收结果见 §5.1。**提交与推送状态以 `git status --porcelain` / `git log @{u}..HEAD` 现跑为准**（本节不写待办态，见 §7 约定 2） |
| **P2-2** | `O-F.4` **余项**：写守卫谓词单一来源 + `cleanup` 豁免核对 + 403 全清单核对 | offline `error-backflow-task.md` O-F.4 | B | **✅ 已完成（2026-09-15）**，详见 §5.4。**范围两项被摸排推翻/砍掉**：① `cleanup` 豁免原判「`pinned` 恒真」是**事实错误**（`pinned` 是真实布尔列）⇒ 改为**订正文档**，未改代码；② **谓词常量三件套经评估显式不做**（决策非漏做，理由见 §5.4）。**提交与推送状态以 `git log @{u}..HEAD` 现跑为准**（本节不写待办态，见 §7 约定 2） |

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
| **批 2** | P0-2 | **存量回归 + 注入对照**（共享算子，须防误伤 manual/held_out） | G0 ✅ **→ ✅ 已完成（2026-09-15）**，详见 §5.2 |
| **批 3** | P1-1 | **出站异常分流**（可桩化到单测层） | ✅ **已完成（2026-09-15）**，详见 §5.3 |
| **批 4** | P1-2 | **`core/http.py` 传输层**（必须真机：双 Host 头只有实发才现形） | ✅ **已完成（2026-09-15）**，详见 §5.5 |
| **批 5** | P2-2 | **写端点 403 全清单** | ✅ **已完成（2026-09-15）**，详见 §5.4 |
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

- **改动**：`backend/app/api/dashboard.py` **两处**（`gate()` 与 `trend()` 两个端点各一处 SQL where），照邻行 `held_out` 既有写法加**字面量**谓词。（**不写行号**：本节第一版写的 `:43`/`:60` 被同批的插入动作当场挪走。）
- **不建共享常量**：`held_out` 在本文件有 7 处字面量，是既有惯例；只给 `error_regression` 建常量会造成不对称。「谓词单一来源」属 **P2-2**，不在本批。（*2026-09-15 补注：P2-2 已做完（§5.4），但该项**经评估显式不做** —— 故此处「属 P2-2」**不等于**「后来会建常量」。*）
- **不动 `build_gate_cards`**：`held_out` 的排除**只在 SQL 层**（`dashboard_rules.py:27-28` 只过滤 status）⇒ 与 `held_out` 同型。
- **验收**：A 真机探针（异步直调 `gate()`/`trend()`，断言 `total_case` **等于**「同 version 下 manual run 之和」——写成**等于谁**）；B **反事实对照**（去掉谓词必红）；C 全量单测；D ruff；E 文档回填。
- **不做**：`/perf`、`/cost`、`/compare`、`_agent_dim_series`、纯函数层、共享谓词常量、error 通道 usage 采集。

### 5.1 验收结果（2026-09-15，全项已跑）

| 项 | 结论 | 证据 |
|---|---|---|
| A 真机探针 | ✅ PASS | `backend/tests/integration/probe_dashboard_error_run_exclusion.py`，容器内跑；**连跑两遍输出逐字一致**。gate 59 张卡 / 6 张有 version / **3 张判别力生效**；trend **21/21 个 agent 判别力生效** |
| B 反事实对照 | ✅ 红 | 摘掉两处谓词 ⇒ **37 条 FAIL**、探针退出码 1，gate 与 trend 两侧都咬；还原后复绿（`grep` 残留 0、`git diff` = 5 增 0 删） |
| C 全量回归 | ✅ | `cd backend && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest -q` ⇒ **937 passed / 100 skipped** |
| D ruff | ✅ 零新增 | 借 online venv + online `pyproject.toml`（`E,W,F,I`，line-length 100）扫两文件；**HEAD 4 条 → 改后 4 条**，逐规则统计一致（E712×2 / E501×1 / I001×1，全在既有行）。**首跑 5 条，其中 F541 由本批新文件引入，已修** |
| E 文档回填 | ✅ | `error-backflow-status.md` O-F.4 行 · `error-backflow-task.md` O-F.4 验证目标 |

**两条副产品（比原判断更重，已回填 status.md）**：

1. **幻影 version 桶**：门禁墙「有 version 的卡」**11 张 → 排除后 6 张** —— 那 5 张**纯由 error run 撑起**（agent 2816 桶内 `total_case=32` 全部来自 error run）。即：这些 agent 原本**拿 error run 当「最新版本成绩」在门禁墙上展示**。旧登记把后果写成「聚合含 error run 行」，实际是**分母做大 + 幻影桶**，**不是**「均值被污染」（`agent_score` 恒 NULL 不进均值）。
2. **基线数字订正**：本文档原记的 `933 passed / 96 skipped` **是陈旧值**，实测 937/100。**与本批无关**：本批未动任何 `test_*.py`（`git status --porcelain -- 'backend/tests/**/test_*.py'` 为空），改动只在 `api/dashboard.py` 源码；`--collect-only` = 1037 = 937+100 自洽。**产出命令见上表 C 行**。

**这批的绿不能证明什么**：① 不证明其余 5 个面**将来**不会出现 error run 的可污染量（本批只证明**当前**无对象）；② 不证明前端 `Dashboard.vue` 不画 error run —— 前端若另有取数路径，不在本批探针的观测面内；③ 不证明 online 侧 422 那条链被修（见 §1 的 P1/P3）。

---

### 5.2 验收结果（批 2 / P0-2 / R-12，2026-09-15）

**明细项**（本节即用户要求的「明细任务」载体，**不依赖任务清单**）：

| 项 | 内容 | 状态 |
|---|---|---|
| 2.1 | `text.py` 空/纯空白守卫（`KeywordNotContainsOp.run()`，5 增 0 删） | ✅ |
| 2.2 | 单测两条：空答三形态 FAIL + **非空不命中仍 PASS**（防误伤正面判据） | ✅ |
| 2.3 | **判别力反事实对照**（摘守卫 ⇒ 必红） | ✅ |
| 2.4 | 存量回归真库探针（**固化**，只读） | ✅ |
| 2.5 | 全量回归 | ✅ |
| 2.6 | ruff 逐规则对照 HEAD | ✅ |
| 2.7 | 文档回填（status / task / 本文件） | ✅ |
| 2.8 | 连带订正：pre-scan 报告判别力基数 | ✅ |
| 2.9 | 提交 + 推送 | ✅ **已推送** `dd62c19..25e9d19`（`dev -> dev`，**快进非 force**）。（**不写「待授权/未推送」裸标记**：本行初版写的就是「⏳ 待授权」，**紧接着的 push 当场把它证伪** —— 见 §7 约定 2；此后状态一律以 `git log @{u}..HEAD` 现跑为准） |

| 项 | 结论 | 证据 |
|---|---|---|
| A 单测 | ✅ 80 passed / 3 subtests | `pytest tests/test_assertions.py -q` |
| B 反事实对照 | ✅ 红 | 摘掉守卫 ⇒ `-k "blank or nonempty"` = **3 failed / 7 passed / 73 deselected**，红在 `AssertionError: True is not false`（= 改前 PASS 的原形）；**同时「非空不命中仍 PASS」那条依然绿** ⇒ 两条判据互相独立，不是同一条断言的两种写法。还原后残留 0 |
| C1 存量回归探针 | ✅ PASS（**连跑三遍一致**） | `tests/integration/r12_empty_answer_probe.py`。带该断言的结果行 = **108**；判据①「空 `answer` 且带该断言」= **0**；判据② 对照 = **108**（非空，判别力成立）。**口径 = 被翻转的量 `answer`，不是 `actual`**（`scorer._unified` 的 `r.answer or ""` 会把 NULL 喂成空串） |
| C2 全量回归 | ✅ | **939 passed / 100 skipped / 14 subtests passed** = 批 1 同期基线 **937/100 + 本批 2 条**，自洽 |
| D ruff | ✅ 零新增 | 借 online venv + online `pyproject.toml`。`text.py` **0→0**；`test_assertions.py` **HEAD `1 E501 + 1 I001` → 改后同**（逐规则一致）。**首跑多出 1 条 E501，由本批新建的探针文件引入，已修** |
| E 文档回填 | ✅ | `status.md`（O-E.9 移入「已落地」+ 头部「已知过期行」+ 结构性 1 标已消解）· `task.md`（O-E.9 施行记录 + 横切表 R-12 行）· `pre-scan-report.md`（判别力基数订正）· 本文件 §1/§2/§5.2 |

**⚠️ 本批查出的两条「比原判断更重」的东西（均已回填，勿当已完成）**：

1. **判别力基数错了，且结论部分反转**。pre-scan 报告写「28 条 keyword 断言是 fail 的 ⇒ 断言有判别力」——
   逐条回查 `actual` 后，其中 **16 条是 `actual='<断言算子未注册: keyword_not_contains>'`**（O-C.5
   登记该算子之前的**历史行**，`pass=False` 来自「算子不存在」而非「判定命中」）。真判定失败只有
   **12 条，且全是 `keyword_contains`**。⇒ **对 `keyword_not_contains` 本身，存量提供不了判别力证据**
   （它跑过 92 次、**真判负 0 次**）。这**不削弱** Q①「受影响面 = 0」（那是「没出现过」的观测），
   但原报告那句「「空答 0」不是「断言从不判负」造成的」在该算子上**恰好落空**。**判别力改由本批的
   注入式反事实对照提供**（= 原报告 §4 第 3 条自陈欠的那一块）。
2. **R-12 的目标受益面在生产库中零样本（新缺口，本批只登记、不改码）**。探针按 `trigger_type`
   拆分显示：那 108 条**全部来自 `manual`，`error_regression` 零条** —— 因为
   `eval_result.assertion_results` 在 error 路径 **84/84 全 NULL**。根因是**代码设计**：
   `runner/orchestrator.py:582` 只落 `verdict_fn(...)` 的**终值字符串**，而 `_error_verdict`
   （同文件 `:136-144`）内部 `run_assertions` 算出的**逐条 `results` 被丢弃**。
   ⇒ **R-12 修正的那条路径，在落库面上本来就没有可视化证据**（`actual` 串不落任何列）；
   「空话术证据」目前**无处可读**。**是否补「error 路径逐条断言证据落库」需另立条目裁**
   （按「批内不掺下一批」，本批不动）。

**这批的绿不能证明什么**：① **不证明纯空白 answer 在真实 pipeline 里会出现** —— 本批只证明算子层
**判得对**；存量 108 条**全部非空** ⇒ 该分支在存量上**一次都没被走到**（这正是「受影响面 = 0」的含义）；
② **不证明 R-12 在 error 链上真的生效过** —— 见上述第 2 条，那条链**零落库样本**，本批的绿来自单测与
反事实，不来自 error 路径的实跑；③ 不证明 `KeywordContainsOp` 也需收紧（它空答已天然 FAIL，本批未动未验）；
④ 不证明白名单豁免机制有用武之地（Q② 判空集，该分支**保留为机制、当前无输入**）。

### 5.3 验收结果（批 3 / P1-1 / O-F.8，2026-09-15）

**改动**：`core/backflow_client.py`（`BackflowClientError` 增 `status_code: int | None = None` +
6 处 raise 补参）、`runner/error_push.py`（`_RETRYABLE_STATUS` + `_is_retryable` + `_push_one` 分流）、
`tests/test_error_push.py`（+6 条）。

| 项 | 怎么验 | 结果 |
|---|---|---|
| 3.1 单测：401 只发一次 | `test_auth_rejection_sends_once` | ✅ 调用次数 == 1 |
| 3.2 单测：其余 4xx 同理 | `test_other_4xx_also_sends_once`（403/400/422） | ✅ 各 == 1（判据是**类别**，非 401 特判） |
| 3.3 单测：5xx / 408 / 429 / 网络层**仍重试** | 三条正向对照 | ✅ 各 == 4（**未被误伤**） |
| 3.4 单测：不可重试时**不进退避** | `test_one_shot_rejection_does_not_delay_finish` 计数 `sleep` | ✅ `sleeps == []` |
| 3.5 既有 `_push_one` 用例不回归 | 同文件旧 3 条（含无 `status_code` 的 `Exception`） | ✅ 全绿 |
| 3.6 反事实对照 | `_is_retryable` 临时恒 `True` | ✅ **恰好 3 条红**（全是「不重试」判据），5xx/网络正向对照**仍绿** ⇒ 有判别力；还原后复绿 |
| 3.7 全量回归 | `cd backend && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest -q` | ✅ **945 passed / 100 skipped / 14 subtests**（批 2 基线 939/100 + 本批 6 条，自洽） |
| 3.8 ruff 逐规则对照 HEAD | 借 online venv + online `pyproject.toml` | ✅ 三个文件**零新增**（E501/F401/F841 的规则构成与条数同 HEAD） |
| 3.9 判据第二条（secret 不泄漏）静态核对 | 读码：6 处 raise / `_headers()` / 日志格式串 | ✅ 只带 `type(exc).__name__` 与状态码；secret 唯一去向是 Authorization 头，全仓 logger 不触碰 |

**两处比原登记更准的判断**（订正，非新增缺口）：
1. **范围比判词窄**：`pull_loop.py:174/:257` 两处捕获**本就无立即重试**（记日志 + 留 pending + 下轮幂等重放）
   ⇒「401 不重试」**只对 push 路径有意义**。本批未动 pull/ack。
2. **判据按码的类别**：不写 401 特判 ⇒ 不必先取证「online 对错误 secret 回 401 还是 403」这个跨仓未知
   —— 它**不落在本批正确性路径上**。

**这批的绿不能证明什么**：① **不证明 online 对错误 secret 真回 401**（跨仓，本批未取证；按类别设计使
该未知不影响正确性，但它也**不是**本批能证的东西）；② **不证明 pull/ack 路径的重试语义**（本批未动），
其「下轮重放会不会无限撞 401」不在观测面内；③ **不证明 secret 从不泄漏** —— 第 3.9 项只做**静态核对**
（读代码），不等于运行时取证；④ **不证明 `408/429` 该归可重试**是实测结论 —— 那是按 HTTP 语义定的，
**无真机证据**。

### 5.4 验收结果（批 5 / P2-2 / O-F.4 余项，2026-09-15）

**改动**：`api/deps.py`（+`require_normal_case` / `require_normal_suite`）、`api/cases.py`（四处落点）、
`api/annotations.py`（`add_annotation`）、`api/scaffold.py`（`add_case_scenes`）、
`tests/test_case_write_guards.py`（新建，12 条）。

| 项 | 怎么验 | 结果 |
|---|---|---|
| 5.1 六个写端点各一条 403 判据 | 断言 `status_code == 403` **且** `code == E_NO_PERMISSION` | ✅ 6/6 |
| 5.2 六条反向对照（普通 case/suite） | 同端点仍走各自原有分支（200 或既有 400） | ✅ 6/6（`add_annotation` 停在「维度未注册」400、`create_case` 停在「接口不存在」400 —— **正是分流而非打死**） |
| 5.3 反事实对照 | 两 helper 临时改空实现 | ✅ **恰好 6 红 / 6 绿**（红的**全是**正向组）⇒ 有判别力；还原后复绿、`grep COUNTERFACTUAL` 零命中 |
| 5.4 全量回归 | `cd backend && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest -q` | ✅ **957 passed / 100 skipped / 14 subtests**（批 3 基线 945/100 + 本批 12 条，自洽） |
| 5.5 ruff 逐规则对照 HEAD | 借 online venv + online `pyproject.toml`，按规则计数比对 | ✅ 四个**改动**文件零新增（E501/F401/I001/E712 条数与 HEAD 一致）；**新建**测试文件 3 条（2 E501 + 1 I001）已修至 0 |
| 5.6「无内部调用方」核对 | 全仓 grep 四个写函数 | ⚠️ **仅静态**：除 integration 测试（且只对普通 case）外无非测试调用方 —— **不等于运行时取证** |

**范围被推翻/砍掉的两项**（本批与原登记不同的原因）：

1. **`cleanup` 豁免核对 ⇒ 查出原判是事实错误，改为订正文档**。status.md 原写「cleanup 豁免
   （`pinned` 恒真）」—— 实测 `pinned` 是 `models/run.py` 的**真实布尔列**（`default=False`），
   `core/cleanup_rules.py` 读的是**实际值**，豁免靠建单时显式 `pinned=True`（`runner/orchestrator.py`）
   + 一条专查它的前置校验。⇒ **「文档错、实现对」**（code-doc-gap 第四型），**未改 `cleanup_rules.py`**。
2. **谓词常量三件套 ⇒ 经评估显式不做**（**决策，非漏做**）。规格给常量的用途是「防 api 挡了、
   loader 没挡」，但实测缺口是 **api 层一条都没挡** —— 共享常量只能防「写法不一致」，
   **救不了「根本没写」**；且 20+ 字面量站点横跨 `case_loader`/`orchestrator`/`scanner`/
   `reconcile_loop`/`dashboard`/`pull_loop`，是零行为收益的纯重构、回归面却铺满整条 runner 链。
   **代价（承认）**：今后「只加 api 守卫、漏加 loader 守卫」**没有机制拦住**。

**另两项显式不做**：① **B7**（禁置 `golden`/`held_out`）**由 `create_case` 守卫吞掉** —— error suite 上
人工建 case 已整体 403，再单判是**不可达代码**；② **B8**（`create_suite` 禁置 `is_error_suite`）——
`SuiteCreate`/`SuiteUpdate` **不含该字段**，Pydantic 默认忽略多余字段 ⇒ 功能上已不可置位，
加显式守卫**无可观测差异**（「机制不同、已满足」，不是缺口）。③ **B10**（`list_suites` 透出
`is_error_suite`）—— 规格标 D-15 但**无任何消费方**，按「答不出谁用就不做」否掉，登记为已知偏离。

**这批的绿不能证明什么**：① **不证明前端收到 403 后表现正确** —— 本批只验后端返回值；
② **不证明没有内部代码依赖这些写路径** —— 只做了静态 grep（见 5.6），**不等于运行时取证**；
③ **不证明常量重构该砍** —— 那是**决策**（基于「常量救不了没写的站点」这一论证），不是本批能验的东西；
④ **不证明 B8 的「天然屏蔽」在 OpenAPI 契约层也无泄漏** —— 只核了 Pydantic 模型定义。

> **⚠️ 判词口径差异（有意，非漏做）**：`error-backflow-status.md` 的 O-F.4 行**仍留「部分落地」**
> （因规格点名的常量项未做，与 `O-C.3` 同惯例），**汇总数字未动**；而本节按「批 5 已完成」记。
> 两者口径不同源于**文件职责不同**（status 表判「规格条目是否全落地」，本文件判「排期批次是否做完」）。

### 5.5 验收结果（批 4 / P1-2 / R-26，2026-09-15）

**改动**：`core/http.py::AllowlistAsyncClient.send()`（摘掉原 Host 项再加、`multi_items()` 取代
`dict()`、复用**原 Host 项**保住端口）、`core/backflow_client.py::_client()`（撤除 `extra_hosts`
绕过 + 删 `urlparse` 死导入）、`tests/test_security.py`（+3 条 R-26 用例）、
`tests/test_backflow_client.py`（护栏**方向反转**：`test_base_host_passed_to_allowlist`
→ `test_base_host_not_special_cased`）。

**根因（实测，非读码推断）**：旧写法 `dict(request.headers)` 的键已被 httpx **小写**为 `host`，
随后 `["Host"] = host` 又加了**一个大写 H** 的键 —— 两键在 dict 里并存、进 `httpx.Headers` 后
归一为同名 ⇒ 实发**两个** Host。另两处同源缺陷：新造的 Host 是**裸 host（丢端口）**、
`dict(Headers)` 会把同名多值头**折叠**成 `"a, b"`。

| 项 | 怎么验 | 结果 |
|---|---|---|
| 5.1 单测（请求形状） | `pytest tests/test_security.py tests/test_backflow_client.py -q` | ✅ 39 passed |
| 5.2 反事实对照 | `send()` 还原为 `dict()` 写法 | ✅ **恰好 2 红**（Host 去重 + Cookie 保留）、37 绿 ⇒ 有判别力；还原后复绿、残留 0 |
| 5.3 全量回归 | `cd backend && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest -q` | ✅ **960 passed / 100 skipped**（批 5 基线 957/100 + 本批 3 条，自洽） |
| 5.4 ruff 逐规则对照 HEAD | 借 online venv + online `pyproject.toml` | ✅ 四个文件**逐规则计数与 HEAD 完全一致**（首轮**非零新增**：`http.py` +1 E501、`test_security.py` +4 E402 / +1 E501 / **+3 F811** / +8 I001 —— 全是我引入的重复 import 与超长行，已修） |
| 5.5 真机判据①（DNS） | `ai-eval-backend` 内 `getaddrinfo("obs-backend", 8000)` | ✅ `172.23.0.4` ⇒ **排除「解析不出」这一误判源** |
| 5.6 真机判据②（新码） | 同上，走**真实客户端**打 `obs-backend` | ✅ `/openapi.json` 200、`/docs` 200、真实端点 `/api/v1/pull/payloads` 405、**`backflow_client.pull_payloads()` 返回 200** |
| 5.7 真机判据③（旧形对照） | 同一真实传输层发**两条 Host** 的请求 | ✅ `LocalProtocolError: Found multiple Host: headers` —— 与 O-F.9 登记症状**逐字吻合**，因果链闭环 |
| 5.8 影响面 | 全仓查 `build_agent_client` 调用点 | 7 处（`adapters/engine` ×2、`api/agents`、`api/scaffold`、`core/backflow_client`、`runner/orchestrator` ×2）+ `judge/client.py` 直接构造 —— 单个 `send()` 覆写是**收敛点** |

> **⚠️ 判据②的首轮预期值是我自己定错的，不是修复失效**：方案写的是「摘后 HTTP 200」，真机拿到的
> 却是 **404** —— 根路径 `/` 在 online 服务上**没有路由**。404 同样证明「请求发出去了、拿到了
> HTTP 响应」（对照判据③是**发不出去**），但**预期值本身定错**，故补打真实业务路径
> （`/openapi.json`、`/docs`、真实端点、真实 `pull_payloads()`）后才算验收。

> **摘绕过使回流出站从「永不失败」变成「依赖一次真实 DNS 解析」** —— 故判据①必须与判据②**同批打**：
> 否则「DNS 解析不出」与「修复失效」**表象相同**（都是 `SSRF: 无法解析`），必误判成「批 4 改坏了」。

**这批的绿不能证明什么**：① **不证明 HTTPS 的 SNI 正确** —— `copy_with(host=IP)` 后 TLS SNI 变 IP、
按 IP 校验证书，与本缺陷**同源但不同层**，当前无已知故障面，**登记不修**；② **不证明各调用点自己的
base_url 组合** —— 7 处调用方共享同一个 `send()`（故单测/真机各打一处即覆盖该层），但各调用点的
入参组合不在观测面内；③ **不证明 online 侧视角正常** —— 只验了客户端不再抛错且对端有应答，
**未核对端日志**；④ **不证明 `pool_error` 分类**（与本批无关，属批 3 面）。

---

## 6. 现场状态（提交/推送 · 残留 · 待裁）

> 本节是「会丢的东西」的清单。**数字由 `git status --porcelain` 与 `ls` 当场产出**（2026-09-15），
> 引用前请重跑，**不要凭印象**。

### 6.1 提交与推送状态（2026-09-15）

本会话成果已分 **3 笔**提交（两仓工作区**已干净**，`git status --porcelain` 空）：

| 仓 | 提交 | 内容 |
|---|---|---|
| offline | `1ab233f` | 台账对账订正（`error-backflow-status.md` + `error-backflow-task.md`，31 insertions） |
| offline | `0c34f19` | 新增 `error-backflow-pre-scan-report.md` + `error-backflow-pending-phases.md`（本文件） |
| online | `b66af1a` | 台账对账订正（`docs/integration-report.md` + `revision-design-register.md` + `task.md`） |

> 上述 3 笔的**内容** = 本会话的 **20 处 C 类台账对账订正**（online `task.md` 1 + `integration-report.md` 13 +
> `register` 1 + offline `status.md` 3 + offline `task.md` 2）+ 两份新报告。
> 另：**本节自身的自纠**（原写「未提交」，被上述提交当场证伪）**自成 1 笔**，哈希见 `git log -1`。

**推送状态（2026-09-15）**：offline `5b01de5..a7ae54e`（3 笔）/ online `2ac5624..b66af1a`（1 笔）
**均已推送**，且均为**快进、非 force** ⇒ 本会话成果**已离开本机**。

**⚠️ 该行是 2026-09-15 早段的快照，此后又推进了两批 —— 已完成的推送范围（同一 2026-09-15 晚些时候）**：
- offline `4641ff0..dd62c19` —— **批 1（P2-1）**：`api/dashboard.py` 两处排除 error_regression
  + 真机探针 + 台账回填（3 文件）。
- offline `dd62c19..25e9d19` —— **批 2（P0-2 / R-12）**：`assertions/ops/text.py` 空答守卫
  + 单测两条 + 存量回归探针 + 四处文档回填（**7 文件 / 235 增 9 删**）。
- offline `dee1890..8385372` —— **批 3（P1-1 / O-F.8）**：`core/backflow_client.py` 状态码
  + `runner/error_push.py` 分流 + `tests/test_error_push.py` +6 条 + 三处台账回填
  （**6 文件 / 183 增 16 删**）。
- offline `f138f2c..31dc4c4` —— **批 5（P2-2 / O-F.4 余项）**：`api/deps.py` 两 helper
  + 四处写端点守卫 + 新建 `tests/test_case_write_guards.py`（12 条）+ 三处台账回填
  （**8 文件 / 315 增 7 删**，含新文件 187 行）。
- offline `42655e2..15ed376` —— **批 4（P1-2 / R-26）**：`core/http.py::send()` 根因修复
  + `core/backflow_client.py` 撤除 `extra_hosts` 绕过 + `tests/test_security.py`（+3 条 R-26 用例）
  + `tests/test_backflow_client.py`（护栏方向反转）+ 三处台账回填（**7 文件 / 182 增 35 删**）。
- 五笔均**快进、非 force**。**此后新增提交是否已推送，一律以 `git log @{u}..HEAD` 现跑为准。**

> ⚠️ **本节不写「已/未推送」的裸标记，只记「已完成的推送范围 + 指向命令」**。
> 理由（**实证，非预防性**）：本节**前后两版都栽在同一机制上**——第 1 版写「未提交 7 文件」，
> 被紧接着的 **commit** 证伪；第 2 版写「三笔均未推送」，被紧接着的 **push** 证伪。
> ⇒ **当「下一动作」正是被描述的对象时，写「待办态」必然当场腐**。
> **本文件此后新增的提交是否已推送，一律以 `git log @{u}..HEAD` 现跑为准。**

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
| ~~C-2~~ | ~~`R-20` G0 的 **选项 A / 选项 B**~~ **✅ 已裁（2026-09-15）= A**：G0 判过、`R-12` 解锁。裁决时查得**订正三条**（4 个未跑 case 里 2 个是 error suite、不属问题域；真未观测的只有 suite 2733 的 2 个普通 case；B 的代价被低估）—— 见 `error-backflow-pre-scan-report.md` **§5.1**；严格表述由「81/85」订正为「**81/83 问题域相关面**」 | 同上 §5.1 |
| C-3 | **lint 门禁缺口**：ruff 本机不可用（三处 venv 均无）；**offline 仓无任何 CI** ⇒ 唯一门禁是**手动**借 online venv + online 配置扫改动文件 | 长期未验收项 |
| **C-4** | **error 路径的逐条断言证据不落库**（批 2 探针连带查出）：`eval_result.assertion_results` 在 `error_regression` 上 **84/84 全 NULL**，因 `orchestrator.py:582` 只落终值字符串、`_error_verdict` 的逐条 `results` 被丢弃。⇒ R-12 修正的**目标路径在落库面上零证据**，「空话术证据」无处可读。**待裁 = 补落库 / 判「设计如此、不需落库」** | 本文件 §5.2 · `task.md` O-E.9 施行记录 |
| **C-5** | **规格引 <code>assertions/ops/text.py</code> 行号已漂 + `_ASSERTION_OPS`/`ASSERTION_OP_CLASS` 声明过时**（§9.3 写 `L33-62`，现类在 `L61-95`；§3 称算子常量缺 `keyword_not_contains`，而 `core/constants.py` 实已包含）—— **文档旧、实现对**，判 P3 登记项、**只登记不改** | 批 2 勘察 |

---

## 7. 维护约定（**防本文件自身腐**）

1. **本文件是派生视图**。任何明细项的状态变更，**先改来源台账，再回填本文件**；不得只改本文件。
2. **本文件的每个「已做/待做」都带「当时（2026-09-15）」框架**。过时后**要么重跑 §3.3 命令回填、要么显式标注过期**，不许把裸标记留在原地——裸标记是对「现在」的断言，**注定腐且腐后像真话**。
   **尤禁**：把「**紧接的下一动作**」写成待办态（写「未提交」然后提交、写「未推送」然后推送）——
   **必然当场腐**，且腐点与写入点相隔只有一次工具调用、最容易被自己漏掉。
   此类场景**直接写「已完成的范围（含哈希区间）+ 复核命令」**，不写待办态。
3. **引用 §3 的任何数字前，先跑 §3.3 的命令**。本文件的数字**不随命令存续**，不跑即不可复核。
4. **§4 未验清单只能缩短**（验一个删一条）。**新增未验项必须同时进 §4**，不许只写在 §1。
