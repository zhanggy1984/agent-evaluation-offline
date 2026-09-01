# Agent 接入评测指南

> 面向**要把自己的 agent 接入评测平台的工程师**。你的 agent 是一个 HTTP 服务（对话、问答、
> 合同审查等 AI 接口），平台会在隔离环境里用固定用例集反复调用它、给它打分。
>
> 读完本文 + 跟着操作，你的 agent 就能被平台评测。**不需要了解平台内部实现。**
>
> 技术细节权威来源见 [接入指南](接入指南.md)（manifest v2 + 标准契约完整规范，含 4 家已接入样例）。

---

## 一、接入前理解：平台怎么评你的 agent

先理解平台的评测方式，后面每一步就都顺了：

```
平台对每个用例做：
  ① 重置数据（调用你的 /admin/reset，回到干净的初始状态）
  ② 调用你的业务接口（按你声明的 manifest 发请求）
  ③ 采集回答 + 性能（首字延迟/耗时）+ token 用量
  ④ 打分（规则断言 + LLM-judge 判分，含判定理由）
```

平台有一条**铁律：平台定标准，agent 适配，平台对任何 agent 零特判。**
它只认标准契约信号，你的接口协议不达标会被**直接拦截**（契约探测失败 → 该次评测标记失败），
不会给你"打个折"。

所以接入你只需要做**三件事**：

| # | 你要做的 | 对应章节 |
|---|---|---|
| ① | 实现标准端点 `GET /api/contracts`，返回一份声明文件（manifest） | §3、§4 |
| ② | 你的评测接口**契约达标**（SSE 或同步二选一）+ 提供 `POST /admin/reset` 造数重置 | §3 |
| ③ | 在平台注册（填地址 → 发现 → 确认 → 冒烟 → 生成用例） | §5 |

> **平台怎么访问你的 agent**：平台容器通过 `host.docker.internal` 访问你部署在宿主机或其他
> 可路由地址的 agent。你的 agent 要能在平台容器网络里被访问到。

---

## 二、接入总览（三步）

```
① 实现 GET /api/contracts 返回 manifest（agent 自我声明：我有哪些接口、平台该怎么驱动我）
② 契约达标（SSE §3.2 或 同步 §3.3）+ 提供 POST /admin/reset
③ 平台注册（§5：填 URL → 发现 → 确认 adapter → 配凭证 → 冒烟 → 用例骨架 → 落库）
```

建议顺序：**先自己自测（§6）**，达标后再去平台注册，省得来回折腾。

---

## 三、标准契约（你的接口要满足什么协议）

平台支持**两种变体，二选一**：

| 变体 | 适用 | 判定口径 |
|---|---|---|
| **SSE**（流式） | 对话类流式输出（打字机效果） | 事件流里必须出现 `usage` 和 `done` |
| **同步**（JSON） | 一次性返回完整回答 | 响应体必须有 `answer` / `usage` / `timing` |

### 3.1 公共要求：造数重置 `POST /admin/reset`

每个用例执行前，平台会调用 `POST /admin/reset` 重置你的业务数据到干净状态，
保证用例可重复执行（同一题每次跑结果可比）。

- 幂等：重复调用不报错。
- 如果你的接口无状态、不需要造数，提供个空实现（返回 200）即可。

### 3.2 SSE 变体

标准 Server-Sent Events（`Content-Type: text/event-stream`），一行一字段。

**必选事件**：`usage`、`done` —— 事件流里缺任何一个，探测直接报「未收到 usage/done 事件」失败。

**事件类型全集**：`meta` / `stage` / `reasoning` / `tool_call` / `answer` / `usage` / `done` / `error`

**帧格式示例**：

```text
event: meta
id: 1
data: {"agent": "my-agent", "model": "gpt-4o", "interface": "chat",
       "contract_version": "2.0", "git_sha": "abc123", "knowledge_version": "v3",
       "ts": 1725000000000}

event: token
id: 2
data: {"content": "你好", "delta": "你好", "ts": 1725000000001}

event: usage
id: 8
data: {"prompt_tokens": 120, "completion_tokens": 45, "total_tokens": 165, "ts": 1725000000010}

event: done
id: 9
data: {"ts": 1725000000010}
```

**各事件字段要求**：

| 事件 | 必带字段 | 要点 |
|---|---|---|
| `meta` | `agent`/`model`/`interface`/`contract_version`/`git_sha`/`knowledge_version`/`ts` | 建议首个事件；`git_sha`/`knowledge_version` 可空串 |
| `reasoning` / `token` | `content` + `delta` + `ts` | 双字段并存（前端展示与评测消费都要）；单帧时 `content == delta` |
| `tool_call` | `id`/`name`/`args`/`result`/`status`/`ts` | `status ∈ {ok, error, rule_override, rule_override_error}`；检索命中后另发 `sources` |
| `answer` | 终答内容 | 最终答案；若你的终答事件名不叫 `answer`，用 `sse.field_map` 映射（见 §4.5） |
| `usage` | `prompt_tokens`/`completion_tokens`/`total_tokens` + `ts` | 多轮合并后在 `done` 前发**一次**即可 |
| `done` | `ts` | 结束标记，最后一个事件 |
| `error` | — | 内部异常事件；出现会触发探测失败上报 |

**建议事件顺序**：`meta` 首个 → 推理/内容增量 →（工具调用 → sources）→ 增量 → `usage` → `done`。

### 3.3 同步变体

一次性 JSON 响应，必带三项：

```json
{
  "answer": "该合同缺失生效日期条款，建议补充。",
  "usage": { "prompt_tokens": 320, "completion_tokens": 88, "total_tokens": 408 },
  "timing": { "start_ts": 1725000000000, "end_ts": 1725000008500 },
  "tool_calls": []
}
```

| 字段 | 判定 | 说明 |
|---|---|---|
| `answer` | 非空 | 最终答案 |
| `usage` | 三分量合法（同 §3.2） | token 计数 |
| `timing` | `start_ts`/`end_ts` 存在 | 耗时区间 |

> 同步变体不验 `done`。

### 3.4 鉴权说明

评测环境鉴权走**环境变量开关**旁路：你的 agent 是否要鉴权、怎么鉴权，由你的部署决定。
需要凭证时，平台注册阶段配置（见 §5，`{{auth.*}}` 占位符），平台会在 prepare 步骤里带上。

---

## 四、v2 manifest（agent 自我声明文件）

**为什么需要它**：平台怎么驱动你的接口——请求路径、请求体、要不要先登录/建会话/上传文件、
参数从哪来——全部由你在 manifest 里声明。平台读它、校验它，然后生成驱动配置（adapter）。

### 4.1 顶层结构

```jsonc
{
  "agent": "my-agent",                // 推荐与平台注册名一致
  "contract_version": "2.0",          // v2 标志
  "interfaces": [                     // 全部对外接口清单（含辅助接口）
    { "name": "chat", "path": "/api/chat", "method": "POST",
      "contract_type": "sse", "llm": true, "description": "对话评测接口" }
  ],
  "scenes": [                         // 业务场景清单（可选，用于自动生成用例）
    { "tag": "greeting", "description": "问候与闲聊" }
  ],
  "contract": {                       // 契约段：平台该怎么驱动我
    "type": "sse", "timeout": 120,
    "prepare": [],                    // 前置步骤（可选）
    "request": { "path": "/api/chat", "method": "POST",
                 "body": { "content": "{{input.content}}" } }
  }
}
```

### 4.2 interfaces（接口清单）

| 字段 | 必选 | 说明 |
|---|---|---|
| `name` | ✅ | 接口名，唯一 |
| `path` | ✅ | 接口路径（可含 prepare 产物占位，如 `/api/sessions/{{prepare.session.id}}/messages`） |
| `method` | ✅ | HTTP 方法 |
| `contract_type` | ✅ | `sse` 或 `sync`，须与 contract.type 一致 |
| `llm` | ✅ | `true` = 评测接口（要打分、要生成用例），`false` = 辅助接口（供 prepare 步骤用） |
| `description` | ⬜ | 说明，供平台展示 |

> 一个 agent 可以有**多个 `llm=true` 接口**（比如"对话"+"打分"两个口），平台会逐一评测。

### 4.3 scenes（业务场景，可选）

```json
{ "tag": "greeting", "description": "问候与闲聊" }
```

平台用场景标签给用例分类、按场景自动生成用例骨架。没有可以不写。

### 4.4 contract（契约段：平台该怎么驱动我）

```jsonc
"contract": {
  "type": "sse", "timeout": 120,
  "prepare": [
    {   // 前置步骤：登录拿 token
      "name": "login", "method": "POST", "path": "/api/v1/auth/login",
      "body": { "username": "{{auth.username}}", "password": "{{auth.password}}" },
      "extract": { "token": "access_token" }          // 从响应中取 token，命名成 "token"
    },
    {   // 引用上一步产物：建会话
      "name": "session", "method": "POST", "path": "/api/v1/sessions",
      "headers": { "Authorization": "Bearer {{prepare.login.token}}" },
      "extract": { "id": "session_id" }
    }
  ],
  "request": {                                        // 最终评测请求
    "path": "/api/v1/sessions/{{prepare.session.id}}/messages", "method": "POST",
    "headers": { "Authorization": "Bearer {{prepare.login.token}}",
                 "Content-Type": "application/json" },
    "body": { "content": "{{input.content}}" }
  }
}
```

**prepare 前置步骤**（可选，无则省）：

- **普通步骤**：`method/path/headers/body/files` + `extract`。`extract` 从响应按字段路径取值，
  供后续步骤或 request 引用（`{{prepare.<步骤名>.<key>}}`）。
- **轮询步骤**（等任务到达可消费状态，如文件解析完成）：

  ```jsonc
  { "name": "wait_done", "poll": {
      "path": "/api/tasks/{{prepare.upload.task_id}}",     // 轮询目标
      "until": { "status": ["WAITING_REVIEW", "SUCCESS", "FAILED", "CANCELLED"] },
      "interval": 2, "timeout": 300 } }
  ```

  直到响应 `status` 命中 `until` 列表即继续。

**sse.field_map**（可选）：当你的终答事件名与平台口径不一致时映射。
例如你的终答事件叫 `token` 而不是 `answer`：

```json
"sse": { "field_map": { "token": "answer" } }
```

**文件型上传**（如合同审查要传 PDF）：

```jsonc
"prepare": [
  { "name": "upload", "method": "POST", "path": "/api/files/upload",
    "files": { "file": "{{input.file_path}}" },          // 平台 uploads 里的样例文件路径
    "extract": { "task_id": "task_id" } }
]
```

### 4.5 占位符三域

| 域 | 来源 | 用途 | 示例 |
|---|---|---|---|
| `{{input.*}}` | 平台用例输入（`case.input`） | 请求 body / 文件路径 | `{{input.content}}`、`{{input.file_path}}` |
| `{{auth.*}}` | 平台注册时配置的凭证 | 鉴权参数 | `{{auth.username}}`、`{{auth.password}}` |
| `{{prepare.X.f}}` | 前序 prepare 步骤 `extract` 的产物 | 步骤间传参 | `{{prepare.login.token}}` |

### 4.6 校验规则（写错了平台会拒绝）

平台会校验你的 manifest，两类结果：

| 类型 | 情形 | 结果 |
|---|---|---|
| **硬错误** | 引用了不存在的 prepare 步骤 / extract 未声明的字段 / 未知占位符域 / 没有 `llm=true` 接口 / `{{prepare.X}}` 缺字段名 | **直接拒绝**生成 adapter，接不进来 |
| 软警告 | `extract` 声明了但没人引用 | 不阻断，提示你 |

> 校验覆盖 request + prepare + sse 全部占位符引用域。写完后先跑一遍自测（§6）最稳。

---

## 五、平台侧接入操作（把 agent 注册进平台）

### 5.1 推荐方式：前端「快捷接入」向导

登录平台（管理员账号）→ **Agent 管理** → 右上角 **「快捷接入」**，走完 6 步：

```
① 填 URL（你的 agent 地址，含 GET /api/contracts 端点）  → 触发「发现」
② 平台拉取 manifest，展示接口清单 / 场景 / 契约草案        → 确认（或直接编辑 JSON）
③ 确认 adapter（平台据 manifest 生成的驱动配置，可手工微调）→ 落库
④ 配置凭证（{{auth.*}} 来源，需要的话）                    → 保存
⑤ 冒烟探测（逐字段验证契约达标，不达标会报具体哪一项）      → 通过
⑥ 生成用例骨架（每场景 × 每评测接口一条用例）              → 确认落库
```

每一步都有明确提示；某步失败会告诉你**具体是哪一项不达标**，改完重新走。

> 权限：**发现（②）与冒烟探测（⑤）评测员也能做**；确认 adapter（③）、凭证与接口同步（④）、
> 用例骨架落库（⑥）需要管理员——非管理员走完 ② 后由管理员续做 ③④⑥。

### 5.2 手动方式（后端 API，等价于向导各步）

| 步骤 | 操作 | 对应 API |
|---|---|---|
| 1 | 注册 agent，填 URL | `POST /agents` |
| 2 | 拉取 manifest，展示接口/场景 + adapter 草案 | `POST /agents/{id}/discover` |
| 3 | 确认 manifest → 服务端权威生成 adapter 落库 | `POST /agents/{id}/adapter` |
| 4 | 配置凭证（`{{auth.*}}` 来源） | `PUT /agents/{id}/auth` |
| 5 | 冒烟探测：逐字段验证契约达标 | `POST /agents/{id}/probe` |
| 6 | 生成用例骨架 → 确认落库 | `POST /agents/{id}/skeleton` → `POST /suites` + `POST /suites/{id}/cases` |

一般用向导即可，手动 API 适合自动化脚本。

---

## 六、自测：不用接平台，先验证自己达标

平台提供了离线自测脚本 `scripts/verify_agent.py`，**不用先注册平台**就能验证你的 manifest 和契约：

### 6.1 静态校验（零依赖，先跑这个）

```bash
python scripts/verify_agent.py --agent-url http://127.0.0.1:8000
```

- 拉取 `GET /api/contracts` → 解析 v2 manifest + 派生 adapter → 报告硬错误 / 软警告 / 需要的输入字段。
- 有硬错误 → 退出码非 0；无硬错误 → 打印「✅ manifest 校验通过」。

### 6.2 平台冒烟（契约真达标，需平台在线）

```bash
python scripts/verify_agent.py --agent-url http://host.docker.internal:8000 \
    --platform-url http://localhost:8180/api \
    --input '{"content": "你好"}' \
    --secrets '{"username": "svc", "password": "xxx"}'
```

- 自动完成：登录平台 → 注册 agent → 确认 adapter → 同步接口 → 配置凭证 → 冒烟探测 → 断言契约字段达标。
- `--input` 是探测输入；文件型 agent 需先把样例文件放到平台 uploads，传 `--input '{"file_path": "/app/uploads/xxx.pdf"}'`。
- 判定口径与平台完全一致——**这里过了，进平台基本就过**。

---

## 七、达标检查清单 + 常见坑

### 7.1 上平台前自检清单

- [ ] `GET /api/contracts` 可访问，返回合法 v2 manifest
- [ ] manifest 无硬错误（§4.6），`llm=true` 接口至少一个
- [ ] 评测接口契约达标：
  - [ ] SSE：事件流含 `usage` + `done`，所有事件 data 带 `ts`，增量事件带 `delta`
  - [ ] 同步：响应含非空 `answer` + 合法 `usage` + `timing`
- [ ] `POST /admin/reset` 存在且幂等
- [ ] 你的 agent 在平台容器网络内可访问（`host.docker.internal`）
- [ ] `verify_agent.py` 静态校验 + 冒烟探测通过

### 7.2 4 家已接入 agent 踩过的坑（别重踩）

| 坑 | 现象 | 正确做法 |
|---|---|---|
| 终答事件名不是 `answer` | 探测发现没有 answer | `contract.sse.field_map: {"token": "answer"}` |
| 缺 `usage` / `done` 事件 | 探测报「未收到 usage/done 事件」 | 事件流强制收尾 `usage` → `done` |
| 流式帧缺 `delta` | 前端/评测拿不到增量 | `content` 与 `delta` 双字段并存 |
| 事件不带 `ts` | 时序校验失败 | 全事件 data 带 `ts`（int 毫秒） |
| usage 缺三分量之一 | token 计数非法 | `prompt/completion/total_tokens` 三者齐全非负 |
| 多轮对话每次发 usage | 计费口径混乱 | 多轮合并，`done` 前发一次 |
| 有鉴权但没配凭证 | 冒烟 401 | 平台注册第 ④ 步配置 `{{auth.*}}` |

---

## 八、参考样例

4 家已接入 agent 的完整 manifest 在平台 seed 数据里（`backend/app/seed_data.py::MANIFEST_SNAPSHOTS`）：

| agent | 契约形态 | 特点 |
|---|---|---|
| `customer-service` | SSE | 最简对话接入，参考入门 |
| `smart-procurement` | SSE 双接口 | 一个 agent 多个 `llm=true` 接口 |
| `good-question` | SSE + field_map | 终答事件名映射 + 知识库问答 |
| `contract-check` | 同步 JSON + 文件上传 | 同步变体 + prepare 上传/轮询，参考复杂场景 |

接入中最容易卡住的是**契约细节**（SSE 事件字段、`ts`、`usage` 三分量），第 7.2 节核对一遍基本能过。
完整技术规范见 [接入指南](接入指南.md)。
