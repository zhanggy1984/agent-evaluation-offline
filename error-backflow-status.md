# error-backflow 实施现状表（O-* 全条目 vs 代码）

> **这是什么**：`error-backflow-task.md` 的 35 个 O-* 条目（O-A.1 / O-B.1-5 / O-C.1-5 /
> O-D.1-5 / O-E.1-9 / O-F.4-9 / O-G.1-4）与**实际代码**的对齐结果。用途 = 开工前先对表，
> 取代「靠记忆或临场 grep 才发现某条没做/做不了」。
>
> **基线指纹**：核对于 **2026-09-14**，代码基线 = `dev` 的 `7691f72`（批 C6）。
> 本表是**当时的**快照，此后任何一批落地都可能使某些行过期 —— 引用前先看指纹。
>
> **已知过期行（2026-09-15 订正，当日即结清）**：`O-E.3` —— 原文只列了 §7.5 **四项中的一项**，
> 且批 C7（`705e6a6`）已落地其中三项、批 C8 落地第四项。该行现已移入「三、已落地」，
> 本条留存仅为记录这次漏判。
>
> **已知过期行（2026-09-15 第二批，O-E.9）**：`O-E.9`（R-12）—— 原判「未落地 + 未过 G0」，
> 两半**均已失效**：G0 于 2026-09-15 判「过」（选项 A，`error-backflow-pre-scan-report.md` §5.1），
> R-12 随之落地。该行已移入「三、已落地」。**顶上基线指纹未改**（本批只触及此一行、未重核全表）。
>
> **已知过期行（2026-09-15 第三批，O-F.8）**：`O-F.8`（出站非可重试失败不重试）—— 原判
> 「部分落地」，缺的**唯一子项**已落地。该行已移入「三、已落地」，汇总行同步（19/11/3/2）。
> **顶上基线指纹未改**（本批只触及此一行、未重核全表）。落地时另查出原判词的**范围偏宽**：
> 「重试」只存在于 push 路径，`pull_loop` 两处捕获本就无立即重试 —— 详见「三、已落地」的施行记录。
>
> **已知过期行（2026-09-15 第四批，O-F.4）**：`O-F.4` —— 本批落地其**「§6.5 写守卫 403」子项**，
> 并**订正原行的一处事实错误**（`pinned` 恒真）。**该行仍留「部分落地」**：规格点名的
> 「谓词常量三件套」经评估后**显式不做**（决策与代价见「三、已落地」的施行记录），
> 与 `O-C.3` 因同一常量项留「部分落地」的处理**同惯例**。汇总数字**未动**（本批无条目跨节移动）。
> **顶上基线指纹未改**（本批只触及此一行、未重核全表）。
>
> **已知过期行（2026-09-15 第五批，O-F.9）**：`O-F.9`（双 Host 头 ⇒ 容器名出站**整体不可用**）——
> 原判词记的是「**extra_hosts 绕过 + 回归护栏**；规格自陈『登记不修』，根因未动符合预期」。
> 本批**把根因修了**：`core/http.py::send()` 改为**摘掉原 Host 项再加**（而非叠加大小写不同的
> 第二个键），`core/backflow_client._client()` 的**绕过随之撤除**。该行**仍留在「三、已落地」**
> （它本就是「绕过已落地」的判词，不是缺口行），但**判词内写明的一处代价已不成立** ——
> 「绕过跳过 IP 解析校验」现**已恢复**。**汇总数字未动**（本批无条目跨节移动）。
> **顶上基线指纹未改**（本批只触及此一行、未重核全表）。**真机三判据见**
> `error-backflow-pending-phases.md` §5.5。
>
> **取证纪律（读之前必看）**：本表由三个只读取证 agent 分路核对（O-A/B/C · O-D/E1-4 ·
> O-E5-9/F/G），**只有 O-E.3 经过二次人工回查**——但那次回查**本身只覆盖了它的一个子项**，
> 漏掉另外三项，2026-09-15 由批 C7 开工前的复查才补齐。这说明「经过人工回查」这个标记
> **只保证该条被看过，不保证看全了**。其余条目按其给出的
> `文件:行号` 采信，**未逐条回查** —— 引用时请当线索，不要当定论。
>
> **过期扫描（2026-09-15，数目全部由命令产出）**：取基线之后的**全部**改动面 ——
> `7691f72..78f1345` = **7 笔提交 / 11 个文件**（建表 `bc03fab` + 批 C7 `143e5fe`/`705e6a6` +
> O-E.3 订正 `785ccde` + 批 C8 `de87a65`/`838ea66` + T-3.15 `78f1345`）—— 与下表 34 条的
> **证据文件求交：零相交**。（唯一命中的 `runs.py` 出现在本表**第 76 行的 O-E.3 施行记录**里，
> 那是本表自己写的内容，不是旧判词的证据。）⇒ **34 条判词无一被基线之后的改动碰过**。
> ⚠️ **但这只排除「被近批改动做掉/改掉」这一类过期，不证明判词本身正确** —— 那次核对仍是
> 「按 `文件:行号` 采信、未逐条回查」，当时判错的原样留着；**扫描范围之外的事（基线之前就判错、
> 或证据点在别处）它一律答不了**。
> **⚠️ 增补（2026-09-15 晚些时候）：上述扫描范围**已落后 HEAD 6 笔**，且已产生第一个反例 ——
> `78f1345..HEAD` = **6 笔 / 7 个文件**（`f46590e` 本表表头 / `e1d2ffe` scanner 短路 /
> `e7448a1` T-3.16 / `a945973` #260 / `09f5233` O-G.2 订正 / `5b01de5` 两条早退补测；
> 命令 `git rev-list --count 78f1345..HEAD`）。文件 = `models/misc.py`、`runner/orchestrator.py`、
> `runner/scanner.py`、`tests/integration/test_integration_scanner_error_run.py`、
> `tests/test_error_regression_run.py` + 本仓两份 md。
> **反例**：`5b01de5` **直接使 O-G.2 的判词失效**（原判「`_run_error` 两条早退均无测试」，
> 该笔补的正是这两条），而 `test_error_regression_run.py` **正在本表 O-G.2 的证据文件面内**。
> ⇒ 上方「**34 条判词无一被基线之后的改动碰过**」**只对 `7691f72..78f1345` 这个冻结区间成立**，
> **不得读作「至今无一条被碰过」**。**扫描范围必须随 HEAD 推移**——冻结区间本身就是新的腐源。
>
> ⚠️ **另一类腐已经出现**：online 侧同期改动**未改变**本表引用 online 的**事实**，但**行号已漂**
> （O-D.5 引的 `config.py:38` 现为 `config.py:54`）⇒ 引用时**以 grep 定位，勿信行号**
> （同 [[memory-status-markers-rot]]）。
>
> **判词口径**（五值，勿自创）：
> `已落地` 行为可在代码/测试找到实现点 · `部分落地` 有实现但缺规格点名的子项 ·
> `未落地` 找不到实现 · `前提不可达` 规格成立所依赖的上游条件不存在 ·
> `无法判定` 缺的是**产物**（报告/记录）而非代码。
>
> **本表不改写规格**：`error-backflow-solution_detail.md` / `error-backflow-phase2.md` 与
> `error-backflow-task.md` 的条目原文一律保持原样；实现现状以本表为准。

## 汇总

**35 条 = 已落地 19 / 部分落地 11 / 未落地 3 / 无法判定 2**（*2026-09-15 第二批：O-E.9 由「未落地」移入「已落地」；第三批：O-F.8 由「部分落地」移入「已落地」。**两次都只动这几个数**；下方那条对不上账的告警**未被任何一次平账**，勿读作已平*），另 **前提不可达 1**（O-E.2 的
backfill **子项**，O-E.2 主体在「部分落地」——故最后一项不另计条目，五项相加为 36 是重复计数）

> ⚠️ **本汇总与其下四节表格对不上账（2026-09-15 发现，**先于本次核对存在**，未擅自平账）**：
> 逐条枚举四节实际行数 = 4 + 11 + 16 + 2 = **33**，而汇总写 35；且「部分落地」「已落地」
> 两节的表头各自也比表内行数多 1（12 vs 11、17 vs 16）。全文出现的编号共 34 个，多出的那个是
> `O-A.1`（R-20 pre-scan，判在「结构性问题 1」而不在四节内）。
> **平账需先查清是哪 2 条未落进表格，本轮不做** —— 本次核对未改动任何条目归属。
> ⚠️ 上段枚举的 `4 + 11 + 16` 是**写下这段时的**行数快照，此后 O-E.9（未落地→已落地）、
> O-F.8（部分落地→已落地）两次移动**均未同步它** —— 现在再照抄这三个数也是错的，**要自己重数**。

> ⚠️ **已知失真 2：`O-G.2` 的判词已被后续提交证伪（2026-09-15 登记）**——按本表 :55 的
> 「**保留原判词、不更新归属**」惯例，**其判词一字未改**，失实仅在**此处**登记：
> §二 `O-G.2` 行判「缺口 = **入口调用方未被驱动**：`_run_error` 的两条早退均无测试」；
> 而 offline `dev` `5b01de5` **补的正是这两条早退路径** ——
> `backend/tests/test_error_regression_run.py:443` `test_cancelled_at_takeover_skips_execution_and_finish`
> + `:457` `test_precheck_failure_marks_skipped_without_finish` + `:470` `class TestMarkErrorSkipped`（2 例）。
> 该笔**未同步回填本表三处判词**（本行、`error-backflow-task.md` O-G.2、横切表 R-22 行），
> 故上述「均无测试」的表述**已不成立**。**核实命令**：`git show 5b01de5 --stat`。
> **✅ 处置进度（2026-09-15 补注，同登记日）**：`error-backflow-task.md` 的两处**已就地订正** ——
> O-G.2 条目下加 `✅ 2026-09-15 补（订正上行 ❌ 判词）`、横切表 R-22 行给原判词加删除线并注失效。
> **本表本行按上方「全表暂停 / 保留原判词」惯例仍不改**（这是**有意的口径差异，不是漏做**：
> 本表停更、`task.md` 继续维护，两份的文件职责不同）。⇒ 「三处未回填」现只剩**本表这一处**。

> ⏸ **全表暂停（2026-09-15 拍板）**：**不再按本表推进**。原因 = 本表多数条目是
> **对着 online 源码/规格推断**出来的契约与护栏（O-G.1 / O-G.3 / O-G.4 为代表），
> 而**没有任何一条真实数据端到端走过** —— 推断出来的契约无法证明是对的，清完账也不等于链路通。
> **改为**：先把 1 个 agent 改造接入（T-2.5），追通一条真实数据，全程记录每一跳的真实载荷；
> 再按真实载荷**倒推**该写什么断言、该修什么。**本表保留原判词，不再更新「已落地/部分落地」
> 归属**，直到第一条数据打通。
> **例外（不属于「规格推断」类）**：表中**有具体故障面**的真实缺陷仍有效 —— 如 O-E.9（R-12
> 空答仍 PASS）、O-F.4 的 dashboard 混入 / §6.5 写守卫。它们**不是**按文档猜的产物，
> 但**同样不预先排期**：由数据流过时撞上的顺序决定先后。

> 🔔 **2026-09-15 补（6b-1 真机打通 · 事实记录）**：上文「**没有任何一条真实数据端到端走过**」
> 与解除条件「**直到第一条数据打通**」—— **②③ 两环已于当日由真实数据走通**
> （cluster 3856 → link 2238 → inbox 7 → test_case 4072，`payload_id` 三处一致；验收表见
> `error-backflow-pending-phases.md` §5.6）。
> **但「全表暂停是否就此解除」= 待裁项，本补注不擅自改判**：④⑤⑥ 三环（error run 建单与判定 /
> 结果回传 / online 收敛）**仍未跑**，而本条立论针对的是**对着 online 源码推断出的契约与护栏**
> （O-G.1 / O-G.3 / O-G.4 为代表）—— 这些条目要的恰好是③④环跑出来的**真实载荷**，
> ②③环走通只证明「拉取-建 case-ack 这条搬运链没错」。⇒ **解除与否的判据应落在④环，不是②③环。**

> 2026-09-15 批 C7 后（**当时状态**）：`O-E.3` 由「未落地」移入「部分落地」（§7.5 项 2/3/4 已落地，
> 项 1 **当时**未落地 —— 同日批 C8 已补上，见下行）。
> 2026-09-15 批 C8 后：`O-E.3` 由「部分落地」移入「已落地」（项 1 落地，**手段有意偏离规格字面**，
> 见「三、已落地」中该行的说明）。
> 2026-09-15（scanner 短路批）后：**`O-E.7` 由「部分落地」移入「已落地」**（唯一的子项 scanner
> 短路已落地，含偏离登记）；**`O-G.2` 仍留「部分落地」但缺口收窄** —— scanner 侧两入口护栏与
> 「已终值行不改写」独立断言已补，**run 级超时路径仍无测试**。
> 2026-09-15（#260 尾项）后：`O-G.2` 缺口**再收窄一处** —— 曾登记的「`total_case=0` 场景下 na
> 回填无断言」随该缺口修复而消失（用例② 改为断言回填），**run 级超时路径仍无测试**，状态仍为
> 「部分落地」。
> 2026-09-15（orchestrator 收尾批，**该批已撤除，见下**）：无条目状态变更。
> 其余 32 条**状态未变**，判词仍以 2026-09-14 / `7691f72` 那次核对为准。
>
> ❌ **已撤除的工作（留痕，勿重复）**：曾据「`if not external_terminal:` 整块在 pytest 套件里
> **零覆盖**」立项，新增 `tests/integration/test_integration_error_finish.py`（5 条真库用例）。
> **该前提为假** —— 立项时只扫了 `tests/integration/`，未扫单测目录；实际
> `tests/test_error_regression_run.py::TestFinishErrorRegression` 已有 **7 条无 DB 单测**覆盖同一批
> 语义（completed / ≥1 na→partial_failed / cancelled 压过 na / 空集→cancelled / error_case 与
> agent_score / 外部终态不覆盖 / 回填 na+scheduler_unexecuted）。新文件经验证后**整体撤除**
> （不是因为它错，而是它只是把**单测级**覆盖换成**真库级**，答不上「不做会出什么具体故障」）。
> **教训**（与「N/N 全绿只证明我写的断言成立」同族）：「某分支无覆盖」是可证伪的断言，
> 下结论前必须先定死**扫过的测试面**，否则它会被当成立项理由。
>
> ✅ **同批暴露的独立缺口（不在本表 35 条内）已于 2026-09-15 修复（#260）**：`run.total_case`
> 原只在 pending→running 的接管 UPDATE 里被赋值、**建单处不写** ⇒ 从未被接管的 pending error run
> 的 `total_case` 恒为 0，`_finish_error_regression` 的 `len(results) < run.total_case` 判据为假
> ⇒ **§6.6 的「未完成 case 回填 na」在租约路径下不发生**（硬超时路径正常）。修法 = 在建单处
> `_create_error_regression_run_locked` 补写 `total_case=len(selected)`（**过滤前计划数**，故意可能
> 大于接管处写入的加载数；判据只决定「要不要对账」、补行循环仍遍历已过滤加载集 ⇒ 不产多余结果行）。
> 判别力对照实测：去掉该行 ⇒ 用例② 的 na 回填断言红。

---

## 一、未落地（3）

| 条 | 缺什么 | 证据 | 后果 / 性质 |
|---|---|---|---|
| ~~**O-E.9**（R-12）~~ **✅ 已落地（2026-09-15）⇒ 移入「三、已落地」** | ~~空/纯空白 answer 未改为 FAIL~~ 【原判词留存】`assertions/ops/text.py:83-89`（只有 `isinstance` 判断，**无 `not text.strip()`**）| ~~空答仍 PASS~~ 【原后果留存】(leakage 空话术复发看不出来)；~~且它 `未过 G0`~~（G0 已于 2026-09-15 判过，见报告 §5.1）|
| **O-B.5** | 写校验收口函数 `assert_normal_case_golden_fields` 全仓无定义 | `core/case_rules.py` 只有 4 个函数；`api/cases.py:229-235` 用 `body.expected or {}` 兜底、`:310-311` 通用 setattr | 普通 case 的 golden 三列可缺、不报错（`error 分支只允许 null` 的守卫无载体）|
| **O-G.1** | 无 `error_codes.py` 分类表，无 R-19 对齐单测 | `runner/executor.py:22-31` 有 `ERROR_*` 常量；全仓 grep `error_codes` 零命中 | 新增 `error_type` **漏分类不会红** —— 护栏缺失（不是功能缺失）|
| **O-D.2** | per-agent 拉取游标从不传 | `runner/pull_loop.py:94`（调用不传游标）；`core/backflow_client.py:66-72` 有 `since_ts/next_token` 形参但 docstring 自陈「游标持久化属调用方职责」；`core/config.py` 无相关键 | 每轮重复拉全量。正确性由 inbox 幂等兜住，代价在**量级** ⇒ **容量型**，不是故障 |

## 二、部分落地（11，缺的都是规格点名的子项）

| 条 | 已落地部分 | 缺什么 |
|---|---|---|
| O-C.3 | `pull_loop.py:267-277` `_mark` 集中迁移入口 | 非法组合（状态/ack_status/字段）校验；三谓词常量 `NEEDS_ACK/STUCK_NEW/CAP_GAP_PROBE`（**当时只活在注释里**；`CAP_GAP_PROBE` 已于 2026-09-16 在 `runner/cap_gap_probe.py` 落码，另两枚仍未落码）|
| O-D.1 | `pull_loop.py:68/:79` GET_LOCK 单飞 + payload_id 幂等 + 一步炸不退出 | 规格「五步单轮」的 ①ack 对账 ②inbox 卡住重扫 ④低频自愈（**只落了 ③增量拉取**）|
| O-D.4 | `pull_loop.py:163-179/:248-264` ack 双态 + pending 幂等重放 | R-8 cap_gap 每小时探测节流；`backflow_client.ack` 的 404/400 错误码分支（非 200 一律抛，无 blocked/manual_invalidate 分支）。**⚠️ 2026-09-16 复核确认（非新增，是把这条钉上时刻）**：经全包 grep 核实并**比原措辞更彻底** —— 不只是「缺 blocked/manual_invalidate 分支」，是 **ack 非 200 分支整体缺失**：`backflow_client.py:118-119` 抛异常时响应体不解析、`pull_loop.py:260/:343` 捕获后只 log + return、该文件全文零 `status_code`；`ACK_STATUS` 第四值 `blocked` 全 `app/` 无赋值点、`ERR_CLUSTER_0003` 全 `backend/` 零命中。复核 = 本仓 `backend/` 下 `grep -rn "ERR_CLUSTER_0003" .` → 0；`grep -n "status_code" app/runner/pull_loop.py` → 0；`grep -rn "blocked" app/` → 仅 `models/error_backflow_inbox.py:10,23`。**本行长期被 `-pending-phases.md` §5.6 ⑤ 的「本批未真机触发」盖住**（该句已于同日订正为「未实现」）。**➜ 处置裁决（2026-09-16，用户拍板选 A）= 不补，登记为「未实现 + 发生率经观测为零」**：触发条件 = admin 人工 invalidate 一条已建 case 的 link，而两侧实测**均为零次** —— online `error_case_link` 21 行中 `invalidated_by` 非空 **0** 行、`invalidate_reason` 无 `manual_invalidate`（= None 15 / online_content_gap 4 / offline_cap_gap 2）；offline inbox 12 行中 `ack_status` **12/12 = `acked`**、`last_error` **全 NULL**（⇒ ack 从未失败、空转重放从未发生）、`blocked` **0 行**、`manual_invalidate` **0 行**。**不补的理由 = 按「不做会出什么**具体**故障」这条尺子答不上来，且观测发生率为零**（再做即过度设计）。**⚠️ 失效条件（任一命中即回头重开本条，不靠人记得）**：① online 出现任一 `invalidated_by` 非空的行；② offline inbox 出现 `ack_status != 'acked'` 或 `last_error` 非空的行。**复核命令**（只读，任意时刻可重跑）：offline 容器内 `SELECT ack_status, COUNT(*) FROM error_backflow_inbox GROUP BY ack_status` + `SELECT COUNT(*) FROM error_backflow_inbox WHERE last_error IS NOT NULL`；online 容器内 `SELECT COUNT(*) FROM error_case_link WHERE invalidated_by IS NOT NULL`。**⚠️ 未验边界**：本次只证「发生率为 0」，**未证「真撞上时会不会产出脏评测数据」**（零样本，无法用真实数据验）—— **不得**把本行读作「该缺口无害」** **➜ 2026-09-17 T-5.5 批 1 落地 = 本行失效条件 ② 的自动出口**：本行要求「任一命中即回头重开、**不靠人记得**」，而「人记得」到此为止 —— 改由 `runner/ack_stale_probe.py` 每 60s 自检，判据 `ack_status <> 'acked' AND updated_at < NOW() - INTERVAL 10 MINUTE`（**不排除 `blocked`**，因 `none`/`pending`/`blocked` 三者语义都是「回写没成功」）。落码 = `app/core/alert.py`（通道；未配 `ALERT_WEBHOOK_URL` ⇒ 降级 `logger.warning`）+ `app/runner/ack_stale_probe.py` + `main.py:164-166` 注册 + `tests/test_ack_stale_probe.py` + `tests/integration/ack_stale_probe_probe.py`。**验收（2026-09-17 实跑）= 单测 7 passed；真库探针连跑两遍各 15 passed / 0 failed（含负对照「同样回拨 11min、只差 `ack_status` 一行」与「`probe_once` 结果 == 独立直查期望集」两写法互证）；撤销回读 `probe_residue = 0`**。**⚠️ 未验边界（勿被绿盖过）**：真实 webhook HTTP 出站（未配 URL ⇒ 走降级分支，只断言 `notify` **被调用**）、`_INTERVAL` 真每 60s、多 worker `GET_LOCK` 互斥 —— **三者均无真机证据**。**⚠️ 本行上文「inbox 12 行、12/12 `acked`」已过期**：2026-09-17 实测 **25 行、25/25 `acked`**（探针零残留；多出的 13 行是真实业务行）。**本行「不补」的裁决不变** —— T-5.5 批 1 补的是**告警雷达**，不是 ack 非 200 分流本身。|
| O-D.5 | `tests/test_backflow_client.py:37-46` MockTransport 回放 + 请求形状断言 | 入站鉴权名与规格「pull_token」不符（实为 `evaluator_service_secret`，`config.py:38`）；无 happy-path 建 case 全链单测 |
| O-E.2 | `runner/reconcile_loop.py` 差集对账主体（批 C5，探针 18/18）| backfill 清零对账子项 —— 已判**前提不可达**（见 `error-backflow-task.md` O-E.2 处置裁定注）|
| O-E.8 | `_error_verdict` → `run_assertions` 判定链 + 空断言不判 pass | 运行期 na 兜底源 `missing_assertion`/`assertion_shape`（grep 零命中；现靠加载即剔除替代）|
| O-F.4 | 数据面隔离（O-E.5）+ cleanup 豁免（~~`pinned` 恒真~~ **⚠️ 原判词事实错误，见本行末**）| ~~**§6.5 写守卫 403**（`cases.py:310-311` 通用 setattr、`:321` invalidate 无守卫）~~ **✅ 已落地（2026-09-15 第四批，批 5）**：六个写端点全部 403，见「三、已落地」O-F.4 施行记录；**谓词常量三件套 —— 经评估显式不做（决策非漏做）**；dashboard 排除 —— **部分落地（2026-09-15，P2-1）**：`/gate` 与 `/trend` 两条聚合已排 `error_regression`；其余 5 处 held_out-only 过滤（`/compare`、`_agent_dim_series`、`/perf`、`/cost`、`/baseline`）**经真机实证「无对象」故不改**（error run 的 `agent_score`/`ttft`/`e2e`/`score_per_dimension` 恒空、`total_cost` 全 NULL ⇒ 无可污染量）。**⚠️ 判据纠正**：旧登记把后果写成「聚合含 error run 行」，实测后果是**分母做大 + 幻影 version 桶**（门禁墙上 11 张有 version 的卡 → 排除后 6 张，那 5 张**纯由 error run 撑起**，如 agent 2816 桶内 `total_case=32` 全部来自 error run）——**不是**「均值被污染」（`agent_score` 恒 NULL 不进均值）。+ ~~active-count 排除~~（**批 C7 已落地**，见「三、已落地」的 O-E.3 施行记录 —— 本行原文把它挂在这里是 2026-09-14 的旧状态）。**⚠️ 末尾补注（2026-09-15 第四批）：「cleanup 豁免（`pinned` 恒真）」是原行的事实错误** —— `pinned` 是 `models/run.py` 的真实布尔列（`default=False`），`core/cleanup_rules.py` 读的是**实际值**，豁免靠建单时显式 `pinned=True`（`runner/orchestrator.py`）+ 一条专查它的前置校验。⇒ 属**「文档错、实现对」**，本批只订正措辞、**未改 `cleanup_rules.py`**（它本就工作正常）。 |
| O-F.5 | `config.py:38-43` 三键走 env + `main.py:155/157` 启动挂接 + 门控在 loop 内 | `seed.py` 无任何 `backflow_*` 键；启动无迁移 fail-fast 检查；无 cap_gap probe task |
| ~~**O-F.8**~~ **✅ 已落地（2026-09-15）⇒ 移入「三、已落地」** | 静态预共享 secret + Bearer + 不进日志（`test_backflow_client.py:95-112` 钉死）| ~~**secret 缺失/错误 → fail-fast**~~ 【原判词留存】：`push_results` 对 401 也抛 `BackflowClientError`，`_push_one` 对一切异常重试 3 次 ⇒ **401 被重试**而非快速失败 |
| O-G.2 | `tests/integration/test_integration_scanner_error_run.py` 四条（scanner 租约/硬超时两入口 + 计划数>加载数 + 「已终值行不改写」独立断言），判别力经反向对照实测；**两条回收路径均断言「未完成 case 回填 na」**（#260 后租约路径的判据才成立）。另 `tests/test_error_regression_run.py::TestFinishErrorRegression` **7 条无 DB 单测**已覆盖收尾四态 + 回填 + 外部终态不覆盖 | **收尾语义已有覆盖，缺的是「入口调用方」**：`_run_error` 的两条早退（`:379-382` 前置校验不过 → `_mark_error_skipped`；`:384-386` 接管时已取消 → **直接 return、完全不调收尾 ⇒ link 挂到 scanner 回收**）均无测试；R-22 另两条路径（orchestrator cancel / run 级超时）**未逐字复刻**其入口 —— 三者是同一件事的三个面：**被调函数已验，调它的入口未被驱动** |
| O-G.4 | 载荷序列化方向 + `schema_version` + self_check + 两水位口径 | `run_status` 四值 Literal、`cases[].pass_fail` 三值、字段长度上限 64/48、10 字段必填性枚举；「两水位不得按 trigger_type 收窄」的显式钉死 |

## 三、已落地（19）

`O-B.1`（模型扩列）· `O-B.2`（run 三列）· `O-B.3`（inbox 模型）· `O-B.4`（含四段 DDL 合并为单迁移 `c3d4e5f6a7b8`）· `O-C.1`（信封校验三分支）· `O-C.2`（resolve_agent/interface，*标签偏差：实为 async 查库，非「纯函数」*）· `O-C.4`（`sanitize_words` + 净化空 fail-closed）· `O-C.5`（算子三处登记）· `O-E.1`（终态 fire-and-forget + `maybe_auto_schedule`，*落点在 `orchestrator.py` 而非规格写的 `auto_schedule.py`*）· `O-E.4`（共享 per-agent 槽池，*实为 `core/limiter.py` 非 `runner/limiter.py`*）· `O-E.5`（case_loader error 分支 + 形态过滤）· `O-E.6`（`_run_error` + 熔断域隔离）· `O-F.7`（`error_push.py` 出站推送 10 字段）· **`O-F.9`**（~~双 Host 头缺陷的 extra_hosts 绕过 + 回归护栏；规格自陈「登记不修」，根因未动符合预期~~ —— **✅ 根因已修、绕过已撤除（2026-09-15 第五批，批 4）**，见下方施行记录）· **O-E.3**（§7.5 api 配套四项，*批 C7 落地项 2/3/4 + 批 C8 落地项 1*）· **O-E.7**（独立收尾 + R-22 回填 + **scanner 短路**，*短路子项 2026-09-15 落地*）· **O-E.9**（R-12 空答 FAIL，*2026-09-15 落地；G0 前置已于当日判过*）· **O-F.8**（出站**非可重试**失败不重试，*2026-09-15 落地*）

**O-F.4 施行记录（2026-09-15 第四批 / 批 5）**——落地「§6.5 写守卫 403」，同时订正原行一处事实错误、
显式砍掉一项规格子项：

- **六个写守卫点全部落地**（`api/deps.py` 两个 helper `require_normal_case` / `require_normal_suite`，
  落点 = `cases.py` 的 `update_case` / `invalidate_case` / `create_case` / `delete_suite`、
  `annotations.py::add_annotation`、`scaffold.py::add_case_scenes`）。守卫位置**在取到对象之后、
  其余校验之前**。**读面一律不动**（`GET /annotations/cases/{id}`、`GET /cases/{id}/scenes` 等）。
- **规格的 B7（禁置 golden/held_out）由 `create_case` 的守卫吞掉**：error suite 上一切人工建 case
  已整体 403 ⇒ 再单判 `is_gold`/`is_held_out` 是**不可达代码**。**B8**（`create_suite` 禁置
  `is_error_suite`）**不做**：`SuiteCreate`/`SuiteUpdate` 都不含该字段，Pydantic 默认忽略多余字段
  ⇒ 功能上已不可置位，加显式守卫**无可观测差异**（是「机制不同、已满足」，不是缺口）。
- **`create_run`/`rerun_run` 的 400 不改 403**：`solution_detail.md` 明文要求 400，改了会打红既有测试。
- **谓词常量三件套 = 经评估显式不做**（**决策，非漏做**）。规格给常量的用途是「防 api 挡了、loader
  没挡」，但实测缺口是 **api 层一条都没挡** —— 共享常量只能防「写法不一致」，**救不了「根本没写」**；
  且 20+ 字面量站点横跨 `case_loader`/`orchestrator`/`scanner`/`reconcile_loop`/`dashboard`/`pull_loop`，
  是零行为收益的纯重构、回归面却铺满整条 runner 链。**代价（承认）**：日后若有人只加了 api 守卫而漏加
  loader 守卫，本表**没有**任何机制拦住他。
- **订正原行事实错误**：见上表 O-F.4 行末补注（`pinned` 恒真 → 真实布尔列 + 建单时显式置位）。
- **辅助证据**：单测 `tests/test_case_write_guards.py` 12 条（6 正向 403 + 6 反向对照）；
  反事实对照实测（两 helper 改空实现 ⇒ **恰好 6 条红、6 条绿**，红的是正向组）；全量回归
  **957 passed / 100 skipped**（= 批 3 基线 945/100 + 本批 12 条，自洽）；ruff 逐规则对照 HEAD
  四个改动文件**零新增**（新建测试文件 3 条已修至 0）。

**O-F.8 施行记录（2026-09-15）**——原判词只说「401 被重试 3 次」，
落地时发现**范围比判词窄**且**判据该按类别而非按 401**：

- **「重试」只存在于 push 路径**：`pull_loop.py` 的两处 `BackflowClientError` 捕获本就只是
  「记日志 + 留 pending + 下轮幂等重放」，**无任何立即重试循环** ⇒ 本批**未动 pull/ack**。
- **判据按码的类别**：`BackflowClientError` 增 `status_code: int | None`（网络层= `None`），
  `error_push._is_retryable` 判「`None` 或 ≥500 或 ∈{408,429} ⇒ 可重试」，其余 4xx ⇒ 一次即止。
  **未写 401 特判** ⇒ 不必先取证「online 对错误 secret 究竟回 401 还是 403」这个跨仓未知，
  正确性不依赖它。**代价（承认）**：408/429 归可重试是**按 HTTP 语义定、无真机证据**。
- **`status_code` 必须是可选参数**：`tests/test_pull_loop_reject.py:172` 以
  `BackflowClientError("400")` **单参位置**构造 ⇒ 设成必填会当场打红既有测试。
- **观测量取调用次数而非日志文案**：反事实对照（`_is_retryable` 临时恒 `True`）下**恰好 3 条**
  新用例红、而 5xx/网络/429 那几条正向对照仍绿 ⇒ 判据有判别力，不是整片红。
- **判据另一半（secret 不进日志/异常消息）为静态核对**：6 处 raise 只带 `type(exc).__name__`
  与状态码、`_headers()` 的 secret 只进 Authorization 头、全仓无 logger 触碰该字段。**不等于运行时取证**。

**O-F.9 施行记录（2026-09-15 第五批 / 批 4）**——**根因已修、绕过已撤除**（原判词记的是
「绕过 + 护栏」，本批把根因修了）：

- **根因（实测，非读码推断）**：`AllowlistAsyncClient.send()` 旧写法 `dict(request.headers)` 的键已被
  httpx **小写**为 `host`，随后 `["Host"] = host` 又加了**一个大写 H** 的键 —— 两键在 dict 里并存、
  进 `httpx.Headers` 后归一为同名 ⇒ 实发**两个** Host，h11 抛 `LocalProtocolError`。另两处**同源**
  缺陷：新造的 Host 是**裸 host（丢端口）**、`dict(Headers)` 把同名多值头**折叠**成 `"a, b"`。
  修法 = 摘掉原 Host 项再加 + 复用**原 Host 项**（含端口）+ 改 `multi_items()`。
- **绕过撤除**：`core/backflow_client._client()` 不再把基址 hostname 塞进 `extra_hosts` ⇒
  本集成恢复完整的「**解析全部 IP 并校验在内网段内**」这一层 —— 原判词写明的代价
  「该分支**跳过 IP 解析校验**」**已不成立**。
- **护栏方向反转**：`test_base_host_passed_to_allowlist`（**护着绕过本身**）→
  `test_base_host_not_special_cased`（**再塞白名单即变红**）。「容器名发得出去」这一属性改由
  `tests/test_security.py` 的 Host 去重用例保证（**判据写成「等于谁」**：Host 值也须含端口）。
- **真机三判据**（`ai-eval-backend` 容器内）：① `obs-backend` 解析 → `172.23.0.4`（**排除
  「解析不出」这一误判源**）；② 新码真实 `backflow_client.pull_payloads()` **返回 200**；
  ③ 旧形状（两条 Host）走同一真实传输层 → `LocalProtocolError`，与登记症状**逐字吻合**。
  完整验收表见 `error-backflow-pending-phases.md` §5.5。
- **登记不修项**：HTTPS 的 SNI（`copy_with(host=IP)` 后 TLS SNI 变 IP、按 IP 校验证书）与本缺陷
  **同源但不同层**，当前**无已知故障面**，**不改**。

> **O-E.9（R-12）的落地证据与两条连带**（2026-09-15）：
> - **改动点**：`assertions/ops/text.py` 的 `KeywordNotContainsOp.run()` 内、`isinstance` 分支**之后**插入
>   空/纯空白守卫（`git diff --stat` = **5 增 0 删**，既有行未动）。**规格伪码与真实签名不符**：
>   §9.3 写 `return OpResult(False, ...)`，真实基类返回 `tuple[bool, Any]` ⇒ 按真实签名落地。
> - **验证**：单测两条（空答三形态 FAIL + 非空不命中仍 PASS）；**判别力对照实测**（摘掉守卫 ⇒ 用例红在
>   「期望 False 实得 True」）；存量回归探针 `tests/integration/r12_empty_answer_probe.py`（连跑三遍
>   一致：判据① 空 `answer` 且带该断言 = **0**；判据② 对照 = **108** 全部非空）；全量回归 **939 passed /
>   100 skipped**（= 批 1 基线 937/100 + 本批新增 2 条，自洽）。
> - **⚠️ 连带发现（新，未修，属下一批的候选）**：探针按 `trigger_type` 拆分显示那 108 条**全部来自
>   `manual`**，`error_regression` **零条** —— 因 `eval_result.assertion_results` 在 error 路径
>   **84/84 全 NULL**。根因是**代码设计**：`orchestrator.py:582` 只落 `verdict_fn(...)` 的**终值字符串**，
>   `_error_verdict`（`:136-144`）内部算出的逐条 `results` **被丢弃**。⇒ R-12 的**目标受益面在生产库中零样本**，
>   且「空话术证据」（`actual` 串）**在任何落库面上都不可见**。**本批不修**（批内不掺下一批），只登记。
>   ⇒ **2026-09-16 已按 C-4 裁定「补落库」并落码**（`_error_verdict` 返 `(终值, results)` + `_save_result`
>   增 `assertion_results` 口子）；**真机已验**（`error_run_probe` 容器内真库，连跑 2 遍 + 回滚复跑各
>   25/25，库内原文回查 fail 行确有 `pass:False` + `actual`），存量 84 行 NULL 不回填。
>   结清记录见 `error-backflow-pending-phases.md` C-4 行。

> **O-E.3 的两条偏离（施行记录，非口径变更）**：
> 1. **项 1 未照规格字面实施**。规格（`solution_detail.md:524` + `:170`）写「→ 域校验（非空 ≤64
>    无空白/控制字符）」；照抄会放行 `1.2.3<script>` 与 `<script>alert(1)</script>`（**两者都不含
>    空白**），等于撤掉 P2-C4 用 `\Z` 修好的入库闸（原 `^\d+\.\d+\.\d+` 只验前缀，`1.2.3<script>`
>    入库后被 Dashboard tooltip 当 HTML 渲染成 XSS）。故改为**白名单字符集**
>    `^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}\Z`（`runs.py:42`，校验点 `:236`）：**放开版本形态、
>    语义不变地保住「拒绝任何 HTML 注入形态」**。判别力经实测（换回规格字面版 ⇒ 断言变红）。
>    *前提（已写进代码注释）：全前端零 `v-html`，唯一 HTML 注入点 `Dashboard.vue:843` 已做
>    `esc()` 全量转义 —— 日后新增 v-html 渲染点，本层白名单是唯一回退。*
> 2. **规格「rerun 同」不成立** —— `RerunBody` 只有 `case_ids`、**不含 version**，
>    `rerun_run` 是 `version=src.version` 直接继承 ⇒ **rerun 从来没有过 semver 校验点**，
>    本项无 rerun 侧可改。（属规格与实现不符，登记备查，不改规格原文。）



## 四、无法判定（2）—— 缺的是产物

| 条 | 缺什么产物 | 说明 |
|---|---|---|
| O-F.6 | 22 场景走查报告 / online fake 载荷逐字段对拍记录 | 仓内只有真机探针（`push_probe.py` 等），无走查产物 ⇒ 无法从代码判定 |
| O-G.3 | §12.1 矩阵逐条登记 | 仓内无该登记文件 ⇒ **G6 出口是否达成无法判定** |

---

## 三条结构性问题（比单条缺口更值得注意）

1. **G0 卡成死环**。`O-A.1` 的 R-20 pre-scan 报告不存在（全仓 `*pre-scan*` 零命中）⇒ **G0 未过**；
   而 O-E.9（R-12）的开工前提正是 G0 的结论。好消息：O-E.9 确判「未落地」，**没有违反「未过 G0
   不开工」**；坏消息：这条链上谁都动不了。
   > ✅ **本条已消解（2026-09-15）**，原判词留存以记录当时的卡点：`error-backflow-pre-scan-report.md`
   > 已产出（R-20 出口物），G0 于当日判「过」（选项 A，报告 §5.1），`O-A.1` 结清、`O-E.9`（R-12）
   > 随本批落地。**该环是靠「补齐产物」解开的，不是靠放宽门禁** —— 判据本身未动。
2. **「写守卫」是一族缺口，不是一条**。§6.5 的 403、谓词常量三件套、dashboard 排除、active-count
   排除 —— 四处同一语义（error run/case 不得混进人工面色），规格本就要求「集中定义防漂移」，
   现在**四处各缺各的**。其中 **active-count 那条会咬人**（409 误挡 manual run）——**该条已由
   批 C7 单独修掉**（2026-09-15），但**另三处仍未动**，且本表判词显示这种「同一语义散在四处」
   的结构**没有被收敛**，只是少了一处。
3. **本表第一次出现「规格点名 + 代码没做 + 有具体故障」型**（O-E.3 子项、O-F.8）。它与前几批
   遇到的 **「前提不可达」**（§6.3 backfill）和 **「容量型」**（O-D.2 游标、洪峰量级）
   性质不同，**不能套同一套处置措辞**。

   > ⚠️ 2026-09-15 补：O-E.3 那部分已由批 C7 修掉，此型现只剩 **O-F.8**。**且这条观察本身
   > 就是下一处坑的现场**：2026-09-14 的回查只写了 O-E.3 点名的**那一个**子项，而四项里的另
   > 三项同样是「规格点名 + 有具体故障」（项 3 建出 0-case 空转 run、项 4 能对 error run 发
   > rerun 再产一条），却因为**只回查了被点名的子项**而漏在表外。**「按条目回查」要连它点名
   > 的子项集一起核 —— 否则回查这个动作本身就会制造盲区。**
   >
   > ⚠️ 2026-09-15 再补：O-E.3 的**第四项**（version 校验）已由批 C8 落地，该条**已全部结清**；
   > 此型现只剩 **O-F.8**。⚠️ 但批 C8 又添了一条**新形态**的登记项：**规格与实现不符**
   > ——规格写「rerun 同（semver 校验）」，实际 `RerunBody` 无 version 入参（见 O-E.3 施行记录 2）。
   > 它与本条讨论的「规格点名 + 代码没做」**不同**：**没有故障面**，只是规格描述与代码结构对不上，
   > 处置也不同（登记备查，不产生开工项）。

## 维护约定

- 每批落地后**改本表对应行**（判词 + 证据行号），并**更新顶部基线指纹**；
  **例外**：若本批只触及少数几行、未重核其余条目，则**不动指纹**（指纹代表全表核对范围，
  改了会假装全表都重核过），改为在头部加一条「已知过期行」—— 2026-09-15 批 C7 即按此办理；
- 条目原文仍在 `error-backflow-task.md`，本表**不复制条目全文**，只记判词与缺口 —— 避免两处
  描述同一件事而漂移；
- 判词口径新增值时，须同时更新本文件头部口径块。
- **lint（ruff）复核命令**（2026-09-16 固化；此前**本仓无任何记载**，仅在各批施行记录里
  零散写「ruff 逐规则对照 HEAD 零新增」而不写怎么跑）：
  ```bash
  # ruff 既不在宿主 PATH、也不是本仓依赖；唯一载体 = 借 online 仓 venv 里的 ruff（当时 0.16.6）。
  R="D:/study/aiprojcet/agent-evaluation-online/backend/.venv/Scripts/ruff.exe"
  cd D:/study/aiprojcet/agent-evaluation-offline/backend
  "$R" check app tests alembic --statistics     # 全量存量
  ```
  - **口径 = 「零新增」不是「清零」** —— 存量告警**未清理也不打算清理**，判据是**相对 HEAD 的
    增量对照**（各批施行记录里的「ruff 逐规则对照 HEAD … 零新增」即此意）。
  - **存量基线（2026-09-16 实测，`backend/ruff.toml` 生效后）**：`app` **260** / `app tests alembic`
    **594**。⚠️ 这两个数是**「当时」框架**，改代码即变，**引用前重跑**。
  - ⚠️ **新建/转正文件时必须显式带上 `tests/`**：只扫 `app` 会漏。实证 = online 仓
    `tests/integration/backflow_allow_probe.py` 转正（其 CI 原文命令是 `ruff check app tests alembic`）
    漏出 **4 条 E501**，按其自身配置现为**红**。
- **lint 判据此前不随仓走（2026-09-16 修复）**：本仓原**无任何 ruff 配置**
  （无 `pyproject.toml` / `ruff.toml` / `setup.cfg`），规则集完全等于「**借来的那个 ruff 版本的默认集**」。
  实测同一把 ruff 0.16.6：**无配置**时 `backend/app` 报 **238** 条（B008/BLE001/DTZ003/TRY401… 皆为
  默认集），而 **online** 因 `backend/pyproject.toml` 写明 `select=[E,W,F,I]` 报 **0** 条
  ⇒ 同一仓、同一命令，**换台机器/换个 ruff 版本就可能得到另一个判据**，而台账照记「零新增」。
  **现固化 = `backend/ruff.toml`**（照 online 口径：`py311` / `line-length 100` /
  `select=[E,W,F,I]` + `known-first-party=["app"]`）。**该文件只声明 lint、不声明依赖**
  —— 本仓依赖仍以 `requirements.txt` / `requirements.lock` 为准；**刻意不放 `pyproject.toml`**，
  否则凭空多出第二个依赖声明源（online 那份 pyproject 是带 `[project] dependencies` 的完整声明）。
  **已验证**：`pytest` 配置源仍为 `pytest.ini`（`configfile: pytest.ini`），未受影响；
  全局 pre-commit hook 的覆盖率门禁**仍是死代码**（其判据 `[ -d tests/unit ]` 判**仓根**，
  本仓 tests 在 `backend/tests`）。
- **仍未做（承认）**：本仓**无 CI**（无 `.github/workflows/`、`error-backflow-task.md` 阶段 G6
  「护栏并入 CI」暂无可挂载体）。故以上全部是**手动门禁** —— 「跑不跑」取决于执行者记不记得，
  **没有任何机制强制**。本次**不建 CI**：offline 侧 pytest 依赖真库/MySQL，起流水线属重工程，
  且本仓单人直推、无 PR 流程（文档承诺 ≠ 需求）。**此项留作可选项，未立项。**
