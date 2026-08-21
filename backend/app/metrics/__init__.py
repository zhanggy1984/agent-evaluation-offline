"""评测维度插件模块（§7.2 / 3.2）：规则/judge/性能/成本四类 + 注册表。"""
from app.metrics.accuracy import completeness, factuality, reasoning, tool_usage
from app.metrics.base import Metric, MetricContext, MetricResult
from app.metrics.cost import token_cost
from app.metrics.performance import e2e, ttft
from app.metrics.registry import get_metric, list_metrics, load_class, register

# 内置维度注册（对齐 constants.DIMENSION_METRIC_CLASS 白名单）
for _cls in (
    accuracy.completeness.CompletenessMetric,
    accuracy.tool_usage.ToolUsageMetric,
    accuracy.factuality.FactualityMetric,
    accuracy.reasoning.ReasoningMetric,
    performance.ttft.TtftMetric,
    performance.e2e.E2eMetric,
    cost.token_cost.TokenCostMetric,
):
    register(_cls())

__all__ = ["Metric", "MetricContext", "MetricResult",
           "get_metric", "list_metrics", "load_class"]
