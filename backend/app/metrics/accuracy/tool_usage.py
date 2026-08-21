"""tool_usage 工具使用：结构断言通过率 × 100（规则维度，§7.2 / §15.2）。"""
from __future__ import annotations

from app.core.constants import DIM_TOOL_USAGE
from app.metrics.base import Metric, MetricContext, MetricResult


class ToolUsageMetric(Metric):
    """tool_usage = 该维度断言通过条数 / 总条数 × 100。

    断言定义须带 dimension="tool_usage"（tool_called/tool_not_called/source_hit
    等工具类断言）。无该维度断言时 N/A。
    """

    dimension = DIM_TOOL_USAGE

    def compute(self, ctx: MetricContext) -> MetricResult:
        rows = [r for r in ctx.assertion_results
                if r.get("dimension") == DIM_TOOL_USAGE]
        if not rows:
            return MetricResult(na=True, na_reason="metric_na",
                                detail={"assertions": 0})
        passed = sum(1 for r in rows if r.get("pass"))
        score = passed / len(rows) * 100
        return MetricResult(score=score, detail={"pass": passed, "total": len(rows)})
