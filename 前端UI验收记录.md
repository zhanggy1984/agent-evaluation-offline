# 前端 UI 验收记录

> **本文件管什么**：浏览器端打开 offline 前端 → 页面呈现的值，是否与**本端库**逐值相符（含发现的展示层缺陷）。
> **本文件不管什么**：生产流量、真实用户路径、回流主链路（那些见 `error-backflow-task.md` / `error-backflow-status.md`）。
> 每批一节，新批次往下追加，不回改旧节（旧节结论若被推翻，新开一节订正）。

---

## 2026-09-17：offline 内容页 5 页 + 登录，全部验完

**触发**：online 仓主导的「4 个 agent 浏览器端功能与数据闭环」验收，offline 侧由本次补齐。
**口径**：判据不是「UI 值 == 本端库值」就够，**跨端同一批 agent 数据还要在 online/offline 两端对得上** ——
跨端那半边记在 online 仓 `task.md`「浏览器端 UI 验收（2026-09-17）」§3.4，本文件只记 offline 本端。

### 一、逐页结果

| 页面 | 结果 |
|---|---|
| 看板 `/dashboard` | 门禁墙 6 张有分卡逐值相符（`probe-c3-auto` 60·1/1、`probe-c4a-pool` 60·5/5 为本次新验）；评测记录 **200 条** + 「超 200 条仅展示最新」横幅（库内 `eval_run` 实为 **347**）✓；最新 5 行 `3713–3717` 逐字段相符 |
| 用例管理 `/cases` | suite 内 **2** 条 == 库；标注待办 **149** 条 == `test_case` 全表 149（全部 `annotation_status='draft'`、`needs_review=0`、`case_annotation` 0 行），自洽 |
| 配置中心 `/config` | 3 个 tab 键集**全等**（运行期 16 / 进程级 20 / 注册期 2 = **38** 项）；`is_hot`（热生效列）与 `updated_at` 逐行相符。**但 3 项只读数值显示错**（见下） |
| Agent 管理 `/agents` | **59/59 行**；启用开关 59/59 全开 == 库 `enabled` 59/59；契约版本 `2.0` 恰 4 家、凭证「已配置」恰 4 家，与库内同一批；`owner_id` 全 NULL ⇒ 全「未指派」；4 家真实 agent 的 `base_url` 逐字符相符 |
| 用户管理 `/users` | **5** 行，`id` / 用户名 / 角色 / 启用状态全等（`542`、`521` 库内 `enabled=0`，UI 开关恰为关） |
| 登录 `/login` | 可达且能登录（口令见 online 仓运维手册，勿入库） |

**唯一未验页 = 修改密码 `/change-password`**（未列入本批范围，不是「验过没事」）。

### 二、缺陷：配置中心 3 项只读数值显示失真

| key | 库值 | UI 显示 |
|---|---|---|
| `judge_review_confidence` | 0.7 | **1** |
| `alarm.error_ratio` | 0.5 | **1** |
| `judge_drift_consistency_threshold` | 0.8 | **1** |

- **根因（两侧均已落实，非猜测）**：`frontend/src/views/Config.vue:37` 写
  `:precision="row._meta?.precision ?? 0"`，而 `frontend/src/constants/configMeta.js` 里
  **这三个 key 确实不存在** ⇒ `_meta` 为 null ⇒ precision 取 0 ⇒ 四舍五入到整数。
- **对照组（证明就是这条链）**：`judge_na_threshold` 在 `configMeta.js:14` 有 `precision: 2`，
  UI 就正确显示 `0.30`。
- **影响面**：三项均只读（`editable()` 要求 `CONFIG_META[row.key]` 存在，为 false），
  即**展示了与库不符的只读数值，用户改不了数**。属展示层缺陷，不是数据损坏。
- **状态**：**未修**。修法二选一（补 `CONFIG_META` 条目 / 无契约 number 项按值推断精度），
  未定；改动属另一批，不在本次验收内顺手做。

### 三、两处观察（**不判为缺陷**，仅登记）

1. 用户管理里**已禁用**用户的操作列仍写「禁用」——`Users.vue:46` 标签写死，无「启用」反向操作。
   点了会对已禁用用户再发一次禁用请求。
2. 配置中心无契约的 number 型项会**多渲染一个空 JSON 文本域** —— `Config.vue:40` 的 `v-if`
   与 `:41` 的 `v-else-if` 串成同一条链，number 且无 `unit` 时落到最后的 `v-else`。

### 四、探针坑（差点写成缺陷，供后人避雷）

`el-switch` 的 `<input>.value` **恒为 `"on"`，不反映开关状态**。
首版取 `value` 得出「`alarm.enabled` 库内 `false`、UI 显示 on」的假差异，
改用 `is-checked` / `checked` 后证实开关是关的，与库一致。
**凡 switch / checkbox，一律读 `checked` 类属性，绝不读 `value`。**
另：`el-tabs` 的三个 pane **同时挂在 DOM 里**（非激活 pane 也 `querySelector` 得到），
`document.querySelector('.el-table__body')` 只会拿到**第一个** pane 的表 ——
必须按 `offsetParent !== null` 筛可见 pane，否则会把同一张表读三遍、得出「三个 tab 内容相同」的假结论。

### 五、复核命令（**结论数字必须连同产出命令一起引用**，勿只搬数字）

```bash
# offline 前端 = ai-eval-frontend:8180；库 = ai_evaluation（与 online 同实例 shared-mysql）
docker exec -i shared-mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N' <<'SQL'
# 配置中心：38 项，scope 三分；key 是保留字，必须反引号
select scope, `key`, is_hot, json_unquote(value), updated_at
from ai_evaluation.system_config order by scope, `key`;
# Agent 管理：59 行 / 启用 59 / 有凭证 4 / 契约 2.0 恰 4
select count(*) as total, sum(enabled=1) as en, sum(auth_config is not null) as cred,
       sum(contract_version='2.0') as cv2 from ai_evaluation.agent;
# 用户管理：5 行
select id, username, role, enabled from ai_evaluation.user order by id desc;
# 看板：总 run 数（UI 只展示最新 200）；最新 5 行
select count(*) from ai_evaluation.eval_run;
select r.id, a.name, r.version, r.status, r.agent_score, r.total_case, r.pass_case, r.started_at
from ai_evaluation.eval_run r join ai_evaluation.agent a on a.id=r.agent_id
order by r.id desc limit 5;
SQL
```

注：`system_config.value` 是 json 列；`json_unquote` 后小数直接可比（`0.7` 会原样打出，
**不会**变成 UI 上那个 `1` —— 两者不等才是缺陷的证据）。

---

## 2026-09-17（当日追加）：上一节 §二 缺陷已修并真机复验

> 按本文件规矩，旧节不回改，订正写在这里。**上一节 §二 的「状态：未修」以本节为准。**

### 一、改了什么（`frontend/src/views/Config.vue`，2 处）

1. **精度**：`:precision="row._meta?.precision ?? 0"` → `numPrecision(row)`。
   有 `_meta.precision` 用契约值；**无契约项按值推断**小数位（整数 0 位；小数取实际位数）。
   **已知不覆盖**：`String(1e-7)` 是 `"1e-7"`、不含小数点 ⇒ 推成 0 位、显示成 `0`。
   *（第一版曾加「科学计数法给 6 位兜底」分支，复核时发现它达不到目的 —— precision=6 下
   渲染成 `0.000000`，同样是错值，已删除并在代码注释里标为已知限制。）*
2. **控件链**：单位后缀 `<span>` 从链**中间**移到整条 `v-if/v-else-if/v-else` 链**之后**。

### 二、⚠️ 订正上一节 §三.2 的定级与范围

原登记：「配置中心**无契约的 number 项**会多渲染一个空 JSON 文本域」。
**实际更宽** —— `Config.vue:40` 的单位后缀 `<span>` 用 `v-if` **另起了一条链**（40 → 41 → 47），
而 `:27`/`:32` 是第一条链；链尾 `v-else` 因此对**所有非 `text` 型**项都为真 ⇒
**`bool` 行也在开关旁边多挂一个空文本域**（如 `judge_cache_enabled`）。
只逐行数控件数（`td` 内 `textarea` / `input` / `.el-switch` 的个数）才看得出来，
按「有没有多余控件」扫一眼很容易漏掉。

### 三、真机复验（重建镜像 + 硬刷后，逐值）

| 项 | 库值 | 修前 UI | 修后 UI |
|---|---|---|---|
| `judge_review_confidence`（run） | 0.7 | 1 | **0.7** ✓ |
| `alarm.error_ratio`（global） | 0.5 | 1 | **0.5** ✓ |
| `judge_drift_consistency_threshold`（global） | 0.8 | 1 | **0.8** ✓ |
| `judge_na_threshold`（有契约，对照组） | 0.30 | 0.30 | **0.30** ✓（未受波及） |
| `alarm.enabled`（bool） | false | switch + 空文本域 | **switch，文本域 0 个** ✓ |

行数未变（运行期 16 / 进程级 20 / 注册期 2 = 38）。修后**仅存的两个 textarea** 是
`run_timeout`（库值 `null`）与 `llm_allowlist`（空 list）—— **本就该走 JSON 分支**
（placeholder「null（使用默认）」），不是残留。

**回归测试**：新增 `frontend/src/views/Config.test.js`（挂载式，对齐仓内 `views/*.test.js` 惯例；
缺陷 1、2 里有一条是**模板链**问题，纯函数单测覆盖不到，故必须挂载）。
`npm test` → **11 文件 59 例全绿**。

**判别力已单独验证**（光有绿灯不算数）：把 `Config.vue` 回退到修前版本重跑，**2 red** ——
`expected '1' to be '0.7'` 与 `expected [...] to have a length of +0 but got 1`，红的两条正是缺陷本体。
**另 4 条在修前修后都绿，是「防修过头」的守卫，不具判别力**，勿当成验证力。

### 四、⚠️ 验证路径坑：容器是烤入的，且 html 无 no-cache

- `docker-compose.yml:76` 是 `build: ./frontend`，**无任何 volume 挂载** ⇒
  前端跑的是 Dockerfile 烤入 nginx 的静态包，**改源码不重建镜像，容器里就不是这份代码**。
- `nginx.conf:50` 只给 `js|css|png…` 发 `expires 7d`，`location /` **未给 `index.html` 显式 `no-cache`**。
- 本次实测症状：容器内已是新包 `Config-DfCe6P81.js`，页面加载的却还是旧包 `Config-BJmgMw97.js`
  （入口 `index-BPc__vfJ.js` vs 容器 `index-gfwWmP9h.js`），`judge_review_confidence` **照旧显示 `1`** ——
  与「改了没生效」逐字同形。
- **判据**：必须硬刷（reload + ignoreCache），并核对
  `performance.getEntriesByType('resource')` 里的 chunk 名 == 容器 `ls /usr/share/nginx/html/assets/` 的结果。

### 五、本次未处置 / 新登记

- 上一节 §三.1（用户管理对已禁用用户仍显示「禁用」，`Users.vue:46` 标签写死）**本次未动**。
- **已订正的一处登记（我原先写反了）**：曾把「`solution_detail.md:889` 说 `judge_review_confidence`
  热生效=是，而 `configMeta.js` 无此键 ⇒ UI 置灰」登记为**规格↔前端契约分歧**，
  并暗示「补 `CONFIG_META` 条目」才是对的。

  **取证结论：UI 置灰正确，分歧在文档侧。** `sysconfig_schema.py:15-16` 明写「meta 缺失 → 拒绝热改」，
  而 PUT 传的 meta 来自 `DEFAULT_SYSTEM_CONFIG`；`judge_review_confidence` 在 `backend/` 下零命中
  ⇒ PUT 必 400。前端「无契约即置灰」的代理正确，且由 `backend/tests/test_frontend_meta_sync.py`
  （断言 `configMeta.js` 与 `DEFAULT_SYSTEM_CONFIG` key 集合双向严格相等，实跑 1 passed）守着。
  DB 里 `is_hot=1` 的那行是**历史残留**，与 `Config.vue:111-113` 注释所称「无契约残留项」吻合。

- **真问题（新登记，未处置）**：`solution_detail.md` §七 表与实现**成片不同步** ——
  规格 **31 项** / `seed.py` **24 项** / **交集 20**；规格有实现无 **11 项**，实现有规格无 **4 项**。
  **⚠️ 「实现里没有」≠「实现漏了」**：可能是有意裁剪，也可能是真缺口，**本次未取证、不下结论**。
  全量 keys 与判定方法记在 online 仓 `task.md` §3.7。

### 六、复核命令

```bash
# ⓪ 回归测试（含判别力验证：回退 Config.vue 后应见 2 red，而非全绿）
cd frontend && npx vitest run src/views/Config.test.js

# ① 代码：两处改动
grep -n -A8 'const numPrecision' frontend/src/views/Config.vue
grep -n -B2 'unit-suffix' frontend/src/views/Config.vue   # 应已移到 v-else 文本域之后

# ② 库值（对照；judge_review_confidence / judge_na_threshold 在 run scope）
docker exec -i shared-mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N' <<'SQL'
select `key`, json_unquote(value) from ai_evaluation.system_config
where `key` in ('judge_review_confidence','alarm.error_ratio','judge_drift_consistency_threshold','alarm.enabled','judge_na_threshold')
order by `key`;
SQL

# ③ 重建 + 硬刷，再核对「页面加载的 chunk == 容器内的 chunk」
docker compose build frontend && docker compose up -d frontend
docker exec ai-eval-frontend ls /usr/share/nginx/html/assets/ | grep -E '^index-|^Config-'
# 浏览器：reload + ignoreCache 后取
#   performance.getEntriesByType('resource').map(r=>r.name).filter(n=>/index-|Config-/.test(n))
# 两者不一致 ⇒ 验的还是旧包，结论作废。
```
