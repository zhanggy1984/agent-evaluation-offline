# error-backflow 实施现状表（O-* 全条目 vs 代码）

> **这是什么**：`error-backflow-task.md` 的 35 个 O-* 条目（O-A.1 / O-B.1-5 / O-C.1-5 /
> O-D.1-5 / O-E.1-9 / O-F.4-9 / O-G.1-4）与**实际代码**的对齐结果。用途 = 开工前先对表，
> 取代「靠记忆或临场 grep 才发现某条没做/做不了」。
>
> **基线指纹**：核对于 **2026-09-14**，代码基线 = `dev` 的 `7691f72`（批 C6）。
> 本表是**当时的**快照，此后任何一批落地都可能使某些行过期 —— 引用前先看指纹。
>
> **取证纪律（读之前必看）**：本表由三个只读取证 agent 分路核对（O-A/B/C · O-D/E1-4 ·
> O-E5-9/F/G），**只有 O-E.3 的「活跃计数」子项经过二次人工回查确认**。其余条目按其给出的
> `文件:行号` 采信，**未逐条回查** —— 引用时请当线索，不要当定论。
>
> **判词口径**（五值，勿自创）：
> `已落地` 行为可在代码/测试找到实现点 · `部分落地` 有实现但缺规格点名的子项 ·
> `未落地` 找不到实现 · `前提不可达` 规格成立所依赖的上游条件不存在 ·
> `无法判定` 缺的是**产物**（报告/记录）而非代码。
>
> **本表不改写规格**：`error-backflow-solution_detail.md` / `error-backflow-phase2.md` 与
> `error-backflow-task.md` 的条目原文一律保持原样；实现现状以本表为准。

## 汇总

**35 条 = 已落地 15 / 部分落地 13 / 未落地 5 / 无法判定 2 / 前提不可达 1（O-E.2 的 backfill 子项，主体已落地）**

---

## 一、未落地（5）

| 条 | 缺什么 | 证据 | 后果 / 性质 |
|---|---|---|---|
| **O-E.3**（api 配套子项）| `create_run` / `rerun` 的活跃计数未排除 error run | `api/runs.py:252`、`:378`（只有 `agent_id + status IN ('pending','running')`，无 `trigger_type` 过滤）| **有具体故障**：error run 停在 pending/running 即占该 agent 活跃名额 ⇒ 用户发 manual run 被 **409 误挡**。规格验证目标逐字「error run 不占 `_max_active_runs` 名额不堵 manual」。**已二次人工复核** |
| **O-E.9**（R-12）| 空/纯空白 answer 未改为 FAIL | `assertions/ops/text.py:83-89`（只有 `isinstance` 判断，**无 `not text.strip()`**）| 空答仍 PASS（leakage 空话术复发看不出来）。**且它 `未过 G0`** —— 见下「结构性问题 1」 |
| **O-B.5** | 写校验收口函数 `assert_normal_case_golden_fields` 全仓无定义 | `core/case_rules.py` 只有 4 个函数；`api/cases.py:229-235` 用 `body.expected or {}` 兜底、`:310-311` 通用 setattr | 普通 case 的 golden 三列可缺、不报错（`error 分支只允许 null` 的守卫无载体）|
| **O-G.1** | 无 `error_codes.py` 分类表，无 R-19 对齐单测 | `runner/executor.py:22-31` 有 `ERROR_*` 常量；全仓 grep `error_codes` 零命中 | 新增 `error_type` **漏分类不会红** —— 护栏缺失（不是功能缺失）|
| **O-D.2** | per-agent 拉取游标从不传 | `runner/pull_loop.py:94`（调用不传游标）；`core/backflow_client.py:66-72` 有 `since_ts/next_token` 形参但 docstring 自陈「游标持久化属调用方职责」；`core/config.py` 无相关键 | 每轮重复拉全量。正确性由 inbox 幂等兜住，代价在**量级** ⇒ **容量型**，不是故障 |

## 二、部分落地（13，缺的都是规格点名的子项）

| 条 | 已落地部分 | 缺什么 |
|---|---|---|
| O-C.3 | `pull_loop.py:267-277` `_mark` 集中迁移入口 | 非法组合（状态/ack_status/字段）校验；三谓词常量 `NEEDS_ACK/STUCK_NEW/CAP_GAP_PROBE`（**只活在注释里**）|
| O-D.1 | `pull_loop.py:68/:79` GET_LOCK 单飞 + payload_id 幂等 + 一步炸不退出 | 规格「五步单轮」的 ①ack 对账 ②inbox 卡住重扫 ④低频自愈（**只落了 ③增量拉取**）|
| O-D.4 | `pull_loop.py:163-179/:248-264` ack 双态 + pending 幂等重放 | R-8 cap_gap 每小时探测节流；`backflow_client.ack` 的 404/400 错误码分支（非 200 一律抛，无 blocked/manual_invalidate 分支）|
| O-D.5 | `tests/test_backflow_client.py:37-46` MockTransport 回放 + 请求形状断言 | 入站鉴权名与规格「pull_token」不符（实为 `evaluator_service_secret`，`config.py:38`）；无 happy-path 建 case 全链单测 |
| O-E.2 | `runner/reconcile_loop.py` 差集对账主体（批 C5，探针 18/18）| backfill 清零对账子项 —— 已判**前提不可达**（见 `error-backflow-task.md` O-E.2 处置裁定注）|
| O-E.7 | `orchestrator.py:790-851` 独立收尾 + R-22 回填 | **scanner 短路**：`scanner.py:112/115` 两处 `score_run_salvage` 无 `trigger_type` 守卫（已登记，判据 §12.1 #16）|
| O-E.8 | `_error_verdict` → `run_assertions` 判定链 + 空断言不判 pass | 运行期 na 兜底源 `missing_assertion`/`assertion_shape`（grep 零命中；现靠加载即剔除替代）|
| O-F.4 | 数据面隔离（O-E.5）+ cleanup 豁免（`pinned` 恒真）| **§6.5 写守卫 403**（`cases.py:310-311` 通用 setattr、`:321` invalidate 无守卫）；谓词常量三件套；dashboard 排除（`api/dashboard.py:43/60/85-87` 只排 held_out）+ **active-count 排除（同 O-E.3）** |
| O-F.5 | `config.py:38-43` 三键走 env + `main.py:155/157` 启动挂接 + 门控在 loop 内 | `seed.py` 无任何 `backflow_*` 键；启动无迁移 fail-fast 检查；无 cap_gap probe task |
| O-F.8 | 静态预共享 secret + Bearer + 不进日志（`test_backflow_client.py:95-112` 钉死）| **secret 缺失/错误 → fail-fast**：`push_results` 对 401 也抛 `BackflowClientError`，`_push_one` 对一切异常重试 3 次 ⇒ **401 被重试**而非快速失败 |
| O-G.2 | orchestrator 收尾路径的 R-22 回填单测 | scanner 回收 / run 级超时两路径无测试（受 O-E.7 短路未落地阻塞）；未抽为独立护栏文件；「已终值行不改写」无独立断言 |
| O-G.4 | 载荷序列化方向 + `schema_version` + self_check + 两水位口径 | `run_status` 四值 Literal、`cases[].pass_fail` 三值、字段长度上限 64/48、10 字段必填性枚举；「两水位不得按 trigger_type 收窄」的显式钉死 |

## 三、已落地（15）

`O-B.1`（模型扩列）· `O-B.2`（run 三列）· `O-B.3`（inbox 模型）· `O-B.4`（含四段 DDL 合并为单迁移 `c3d4e5f6a7b8`）· `O-C.1`（信封校验三分支）· `O-C.2`（resolve_agent/interface，*标签偏差：实为 async 查库，非「纯函数」*）· `O-C.4`（`sanitize_words` + 净化空 fail-closed）· `O-C.5`（算子三处登记）· `O-E.1`（终态 fire-and-forget + `maybe_auto_schedule`，*落点在 `orchestrator.py` 而非规格写的 `auto_schedule.py`*）· `O-E.4`（共享 per-agent 槽池，*实为 `core/limiter.py` 非 `runner/limiter.py`*）· `O-E.5`（case_loader error 分支 + 形态过滤）· `O-E.6`（`_run_error` + 熔断域隔离）· `O-F.7`（`error_push.py` 出站推送 10 字段）· `O-F.9`（双 Host 头缺陷的 extra_hosts 绕过 + 回归护栏；*规格自陈「登记不修」，根因未动符合预期*）

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
2. **「写守卫」是一族缺口，不是一条**。§6.5 的 403、谓词常量三件套、dashboard 排除、active-count
   排除 —— 四处同一语义（error run/case 不得混进人工面色），规格本就要求「集中定义防漂移」，
   现在**四处各缺各的**。其中 **active-count 那条会咬人**（409 误挡 manual run）。
3. **本表第一次出现「规格点名 + 代码没做 + 有具体故障」型**（O-E.3 子项、O-F.8）。它与前几批
   遇到的 **「前提不可达」**（§6.3 backfill、T-3.15）和 **「容量型」**（O-D.2 游标、洪峰量级）
   性质不同，**不能套同一套处置措辞**。

## 维护约定

- 每批落地后**改本表对应行**（判词 + 证据行号），并**更新顶部基线指纹**；
- 条目原文仍在 `error-backflow-task.md`，本表**不复制条目全文**，只记判词与缺口 —— 避免两处
  描述同一件事而漂移；
- 判词口径新增值时，须同时更新本文件头部口径块。
