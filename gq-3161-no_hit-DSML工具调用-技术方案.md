# gq no_hit-工资发放日（case 3161）未达标根因与修复方案 v2

> 状态：方案 v2 定稿（已并入独立 Plan subagent 评审意见 + 用户三决策确认），待落地
> 用户决策（2026-09-01 AskUserQuestion）：① 改动 2 只修命中路径；② 评测侧防御断言 = gq 全部 case 金丝雀；③ 验收口径 = 16 case 多数决 + 8-31 基线对照
> 修复对象：good-question agent（仓库 `D:\study\aiprojcet\good-question`，容器 `rag-backend`）
> 驱动问题：2026-09-01 run 3030/3031/3032，case 3161（no_hit-工资发放日）规则维度 completeness 稳定 fail

---

## 一、现象

| run | pass/fail | case 3161 | 维度 |
|---|---|---|---|
| 3030（9-1 07:54）| 15/16 | fail 77.50 | completeness=50, factuality=100, reasoning=80 |
| 3031/3032（9-1 07:57 重跑）| 0/1 ×2 | fail 77.50 | 同上 |

- 3161 断言 2 条（均 completeness）：`field_nonempty(answer)` + `keyword_contains(answer, no_hit 语义集合)`。1 过 1 fail → completeness=50。
- judge factuality=100、reasoning=80，**问题只在规则维度** → 门禁规程 V2 第 6 条：规则维度 fail 属 agent 真实缺陷，改 agent 业务逻辑。
- 8-31 V2 门禁（2558/2559/2560）3161 全过；9-1 稳定 fail，三次 answer 一致（含 usage `cached:true`）。

## 二、根因

### 2.1 直接原因：命中路径第二轮"不带 tools"，LLM 的二次检索意图无法兑现

`chat_service.py:535` `stream_chat` 编排：

1. 第一轮（604-628）：LLM 带 `hybrid_retrieve` 工具自主决策。3161 检索到低置信结果（source_count=3, max_score=0.21, confidence_band=low）。
2. 命中路径（669-698）：第二轮 `llm_stream_chat(messages)` **不带 tools**（683，注释「防再循环」）。system prompt 约束 4（59）要求"低置信如实说明"，LLM 选择换 query 再检一次是**合理 agent 行为**，但无工具可用，只能把工具调用以文本形式渲染进 content。
3. 该文本被当最终 answer 流出（见 2.2），断言 fail。

**第一性根因**：agent 缺"低置信可再检索"的闭环（第二轮不带 tools 是主动设计）。DeepSeek V4 引入 DSML 输出只是把既有缺口从"低概率 XML 泄漏"升级为"高概率 DSML 泄漏"（2.5）。

### 2.2 DSML 泄漏入口：流式状态机，不是 `_parse_content_tool_calls`

run 3030 的 3161 `answer` 是**一段工具调用声明文本**（LLM 想二次调 `hybrid_retrieve`，query=「发薪日 工资支付 工资结算」），标签被 DSML 标记包裹。原始字节（repr 简化）：

- 形如：左尖括号 + 全角竖线(×2) + `DSML` + 全角竖线(×2) + `tool_calls` + 右尖括号；内部 `invoke name=...`、`parameter name=...` 子标签同构；结尾对应闭标签。
- 即字节 `3C EF BD 9C EF BD 9C 44 53 4D 4C EF BD 9C EF BD 9C ...`，其中 `EF BD 9C` = U+FF5C 全角竖线。**这是 DeepSeek V4 专有工具调用格式 DSML（DeepSeek Markup Language）的双全角竖线退化变体**（标准形 `｜DSML｜`，实测双 `｜｜`）。DSML 泄漏到 content 是 DeepSeek V4 已知问题（hermes-agent #15453、HF deepseek-ai/DeepSeek-V4-Pro #209、vLLM #52645 等）。

**关键：DSML 块在流式层就被切走，从未进入 `_parse_content_tool_calls`。** `llm_service.py:196-226` 的流式 content 状态机：

- 198 只匹配 `tool_calls` 完整块；DSML 前缀（`<` + `｜｜DSML｜｜...`）**不匹配**。
- 210-221 的"待闭合块累积"三处判断（`startswith`、`_TOOL_CALLS_OPEN.startswith`、`rfind`）对 DSML 前缀均 False / -1 → 直接走 222-224「纯普通文本全量输出」分支 → DSML 逐片当 content yield。
- 方案引用的 206-207「解析失败原样输出」是"m 已匹配但解析失败"的兜底，**DSML 场景根本到不了这一行**。

### 2.3 评测断言判定

answer = DSML 声明（无自然语言回答）→ `field_nonempty` pass / `keyword_contains(未找到…)` fail → completeness=50。

### 2.4 缓存固化

`chat_service.py:894-914` 把坏 answer 写 Redis（TTL 2h）。3030 真实生成，3031/3032 命中缓存重放（usage `cached:true`，`chat_cache.py:194`）。坏答案一经入缓存，TTL 内重复评测必然复现。

### 2.5 为什么 8-31 pass / 9-1 fail

8-31 三次 LLM 第二轮直接答「未找到」，未触发再检索；9-1 首跑 LLM 恰好输出 DSML 声明 → fail 并被缓存固化。本质：DeepSeek 服务端升级 V4 引入 DSML + gq 缺低置信再检索闭环 + 缓存放大。非断言误判（用户真实提问，agent 只回工具调用声明无回答，确属缺陷）。

## 三、修复方案

### 改动 1（llm_service.py，全局）：DSML 识别集成进流式状态机

- 把 DSML 变体开标签前缀（双全角 `｜｜`、单全角 `｜`、ASCII `||`、`|DSML|`、无竖线裸标签等）**加入 210-221 的待闭合累积判断**，跨 chunk 分片也能累积到完整块；闭合后归一化为标准 XML，复用 `_parse_content_tool_calls` 产出与标准一致的 `tool_call` 事件。
- 归一化与现有解析**合并为单一函数**（如 `_normalize_tool_call_block()`），供流式层、缓存防护（改动 3）共用，避免正则漂移。
- 保留「解析失败原样输出」保守兜底（206-207，不吞用户正文；误伤面：正文恰含 `｜｜DSML` 字样，概率低且失败时原样输出）。
- 这条是**全局兜底**：round 1/round 3 的 DSML 泄漏都靠它（`_parse_content_tool_calls` 自身 docstring 记录 ~1/7 概率 content 渲染）。

### 改动 2（chat_service.py，只修命中路径）：低置信二次检索 agent loop

命中路径第二轮遇 `tool_call` 事件（标准或 DSML 解析所得）不再 `continue`，改为：

1. 执行二次检索 `execute_retrieve_tool(library_id, new_query)`。
2. 回传第二个 tool 消息（复用 673-681 模式），**assistant tool_calls 消息必须携带第二轮 `reasoning_content`**（DeepSeek 校验 tool 轮次必须带 reasoning_content，缺则 400 → 整 case 报错）。
3. 第三轮**也带 tools**，用 `MAX_TOOL_ROUNDS`（102，已确认只定义未引用）作循环上限；达到上限后强制落 `_NOT_FOUND_ANSWER`。**第三轮不带 tools 会在同一位置复现泄漏**（评审否决了「第三轮不带 tools」的初版）。

事件序变化：`meta → reasoning* → tool_call → sources → reasoning* → tool_call(2) → sources(2) → reasoning* → token* → usage → done`。评测平台对多 tool_call 追加收集（assembler.py:75-76），前端 sse.ts 已支持 tool_call/sources；需回归确认无事件序错乱。

### 改动 3（chat_service.py + chat_cache.py）：缓存防护 + 版本失效

- `answer_is_toolcall_only`：最终 answer 仅含工具调用声明（标准/DSML）无实质自然语言内容 → **不写缓存**（并入 894 条件，复用改动 1 的检测函数，防漂移）。
- **bump `_CACHE_VERSION` 2→3**（chat_cache.py:34）：改动 2 改变命中路径缓存 payload/replay 形态，版本号本来为此设计（33-34 注释）；bump 后部署自动作废含 3030 坏条目在内的全部旧缓存，比手工 `flush_library` 可靠。部署时再 flush 一次双保险。

### 改动 4（chat_service.py，兜底）：工具调用声明即未作答，落 no_hit 话术

- 触发条件改为「**content 中出现任意工具调用声明块（标准/DSML 变体）即视为该轮未作答**」，而非初版「剥离后为空」（"我再查一下\n<DSML>"剥离后非空会漏判）。
- 3161 场景明确落 **`_NOT_FOUND_ANSWER`**（含"未找到"，106-109），**不能落 `_EMPTY_ANSWER_FALLBACK`**（142，不含"未找到"，keyword_contains 仍 fail）。
- **前提声明（评审关键点）**：评测 answer 是 SSE token 事件拼接（assembler.py:57-67），`full_answer` 兜底发生在全部 token 流出之后（881-885），**事后改 `full_answer` 对评测侧 answer 无效**，只影响 DB 落库与前端回放。改动 4 只在改动 1 已拦截失败时的**最后防线**，真正拦截靠改动 1 在流式层完成。

## 四、改动范围

- **改动 1（解析）全局三路**：它决定 DSML 会不会变 answer，必须三路（hit / empty / override）都拦截。
- **改动 2（二次检索）只限命中路径**：3161 走 hit；"低置信再检索"只在有弱命中时有意义，空路无内容可再查、override 已用 user 消息注入 context 语义不同，放开只放大成本与回归面（评审同意）。
- 命中路径以外两路的 `tool_call` `continue` 吞掉后，**若 answer 为空则落对应兜底**（统一出口，避免空 answer 中间态）。

## 五、验证计划

1. **单测**（good-question）：
   - `_parse_content_tool_calls` / 归一化：DSML 变体表（双/单全角、ASCII 双竖线、紧凑无换行、闭合标签同步归一）。
   - **跨 chunk 分片**：DSML 开标签被拆片（`<` 一片、`｜｜DSML` 下一片）时能累积到完整块（评审重点，初版遗漏）。
   - `stream_chat`：content 渲染 DSML → `tool_call` 事件。
   - round 3 仍泄漏（达 MAX_TOOL_ROUNDS 上限）→ 落 `_NOT_FOUND_ANSWER`。
   - 老缓存版本（v2）在 bump 后失效。
2. **复现验证**：bump 后部署，清库缓存，重跑 3161，多次调用确认 answer 为自然语言「未找到」。
3. **评测回归**：完整 16 case 多数决（评审建议加一次 8-31 基线对照），确认其余 15 case 无事件序错乱/重复输出/分数波动（历史教训：content 分支重复输出致 judge 扣分）。
4. **评测侧防御断言**（可选）：对 gq 全部 case 追加「answer 不含 DSML/XML 工具调用残留」金丝雀断言——DSML 是模型级现象，可能命中任意 case，不只 3161。门禁立场"改 agent"为主、防御断言为辅。

## 六、⚠️ 待确认

1. **改动 2 只修命中路径**是否接受（评审同意；三路统一是过度设计）？
2. **改动 3 缓存防护 + `_CACHE_VERSION` bump** 是否做（评审建议做，bump 是可靠失效手段）？
3. **改动 4 落 `_NOT_FOUND_ANSWER`**（含"未找到"）是否接受？注意初版「剥离后为空」判断有缺陷已修正。
4. **评测侧防御断言**：只加 3161 还是 gq 全部 case 追加金丝雀？（评审建议全部 case）
5. 验收口径：仅 3161 复测通过，还是完整 16 case 多数决 + 基线对照？
6. 改动对象是 gq agent（`D:\study\aiprojcet\good-question`），非评测平台仓库；gq 仓库需独立提交/发布，确认按 gq 的发布流程走。

---

**Sources**（DSML 机制参考）：
- [HuggingFace deepseek-ai/DeepSeek-V4-Pro #209 — DSML tool-call markup in content](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro/discussions/209)
- [Hermes-agent #15453 — Tool calls not parsed correctly with DeepSeek-V4](https://github.com/NousResearch/hermes-agent/issues/15453)
- [OpenClaw PR #53193 — strip DeepSeek DSML XML](https://github.com/openclaw/openclaw/pull/53193)
- [vLLM PR #52645 — Recover DeepSeek V4 tool calls with malformed DSML wrappers](https://app.semanticdiff.com/gh/vllm-project/vllm/pull/52645/overview)
- [vLLM PR #53417 — prevent DSML markup leak in DeepSeek V4 streaming tool calls](https://app.semanticdiff.com/gh/vllm-project/vllm/pull/53417/overview)
- [NVIDIA dynamo DSML parser（U+FF5C 格式与归一化正则）](https://github.com/ai-dynamo/dynamo/blob/ea92d583210ef9a420de89d0866bdc9bb5c20363/lib/parsers/src/tool_calling/dsml/parser.rs)
