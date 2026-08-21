"""全局常量：评测维度 / 插件 class_path 白名单（安全基线 §九-2 的唯一事实来源）。

插件白名单：ALLOWED_CLASS_PATHS 为代码内 frozenset，DB 只存指向该集合的索引，
绝不可经 API/DB 增删；加载统一走 importlib.import_module + getattr。
"""

# ---------- 评测维度（metric_def / 评分 / 门禁 共用 code，防拼写漂移） ----------
DIM_COMPLETENESS = "completeness"          # accuracy：完成度（结构断言，规则维度）
DIM_FACTUALITY = "factuality"              # accuracy：事实性（LLM-judge 语义维度）
DIM_REASONING = "reasoning_quality"        # accuracy：思考链（LLM-judge 语义维度）
DIM_TOOL_USAGE = "tool_usage"              # accuracy：工具使用（结构断言，规则维度）
DIM_TTFT = "ttft"                          # performance：首字延迟
DIM_E2E = "e2e"                            # performance：端到端延迟
DIM_TOKEN_COST = "token_cost"              # cost：token 成本

ACCURACY_DIMENSIONS = (DIM_COMPLETENESS, DIM_FACTUALITY, DIM_REASONING, DIM_TOOL_USAGE)
ALL_DIMENSIONS = (*ACCURACY_DIMENSIONS, DIM_TTFT, DIM_E2E, DIM_TOKEN_COST)

DIMENSION_CATEGORY = {
    DIM_COMPLETENESS: "accuracy",
    DIM_FACTUALITY: "accuracy",
    DIM_REASONING: "accuracy",
    DIM_TOOL_USAGE: "accuracy",
    DIM_TTFT: "performance",
    DIM_E2E: "performance",
    DIM_TOKEN_COST: "cost",
}

# 语义维度（LLM-judge 参与评分，N/A 归一化/门禁保守判定专用）
SEMANTIC_DIMENSIONS = (DIM_FACTUALITY, DIM_REASONING)

# 性能/成本维度（独立呈现，不进 accuracy 加权，不参与门禁）
NON_SCORED_DIMENSIONS = (DIM_TTFT, DIM_E2E, DIM_TOKEN_COST)

# ---------- 插件 class_path 白名单（frozenset，禁止运行时增删） ----------
_ASSERTION_OPS = (
    "assertions.ops.structure.FieldPresentOp",
    "assertions.ops.structure.FieldNonemptyOp",
    "assertions.ops.structure.ValueRangeOp",
    "assertions.ops.structure.ValueEqualsOp",
    "assertions.ops.text.KeywordContainsOp",
    "assertions.ops.tool.ToolCalledOp",
    "assertions.ops.tool.ToolNotCalledOp",
    "assertions.ops.retrieval.SourceHitOp",
    "assertions.ops.collection.ListPrecisionRecallOp",
    "assertions.ops.collection.ListContainsOp",
)

_METRICS = (
    "metrics.accuracy.completeness.CompletenessMetric",
    "metrics.accuracy.tool_usage.ToolUsageMetric",
    "metrics.accuracy.factuality.FactualityMetric",
    "metrics.accuracy.reasoning.ReasoningMetric",
    "metrics.performance.ttft.TtftMetric",
    "metrics.performance.e2e.E2eMetric",
    "metrics.cost.token_cost.TokenCostMetric",
)

ALLOWED_CLASS_PATHS = frozenset((*_ASSERTION_OPS, *_METRICS))

# 断言算子 → 默认 class_path（seed 内置算子时用）
ASSERTION_OP_CLASS = {
    "field_present": "assertions.ops.structure.FieldPresentOp",
    "field_nonempty": "assertions.ops.structure.FieldNonemptyOp",
    "value_range": "assertions.ops.structure.ValueRangeOp",
    "value_equals": "assertions.ops.structure.ValueEqualsOp",
    "keyword_contains": "assertions.ops.text.KeywordContainsOp",
    "tool_called": "assertions.ops.tool.ToolCalledOp",
    "tool_not_called": "assertions.ops.tool.ToolNotCalledOp",
    "source_hit": "assertions.ops.retrieval.SourceHitOp",
    "list_precision_recall": "assertions.ops.collection.ListPrecisionRecallOp",
    "list_contains": "assertions.ops.collection.ListContainsOp",
}

# 维度 → 默认 class_path（seed metric_def 时用）
DIMENSION_METRIC_CLASS = {
    DIM_COMPLETENESS: "metrics.accuracy.completeness.CompletenessMetric",
    DIM_FACTUALITY: "metrics.accuracy.factuality.FactualityMetric",
    DIM_REASONING: "metrics.accuracy.reasoning.ReasoningMetric",
    DIM_TOOL_USAGE: "metrics.accuracy.tool_usage.ToolUsageMetric",
    DIM_TTFT: "metrics.performance.ttft.TtftMetric",
    DIM_E2E: "metrics.performance.e2e.E2eMetric",
    DIM_TOKEN_COST: "metrics.cost.token_cost.TokenCostMetric",
}

# 默认权重（agent 级，seed 模板；interface 覆盖见 agent_dimension_weight）
DEFAULT_WEIGHTS = {
    DIM_COMPLETENESS: 0.30,
    DIM_FACTUALITY: 0.35,
    DIM_REASONING: 0.15,
    DIM_TOOL_USAGE: 0.20,
}

# adapter_type 硬编码白名单
ALLOWED_ADAPTER_TYPES = ("config", "code")

# 文件类型白名单（扩展名校验 + 二进制魔数校验在 core/case_rules.py validate_upload；
# 7.6 A7 修正此前指向不存在 core/files.py 的误导注释；文本类 txt/csv/json 宽松放行）
ALLOWED_FILE_EXTS = frozenset({"pdf", "docx", "xlsx"})

# contract_type 合法值
CONTRACT_SSE = "sse"
CONTRACT_SYNC = "sync"
