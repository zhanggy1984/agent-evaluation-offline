"""completeness 完成度：结构断言通过率 × 100（规则维度，§7.2 / §15.2）。"""
from __future__ import annotations

from app.core.constants import DIM_COMPLETENESS
from app.metrics.base import Metric, MetricContext, MetricResult


class CompletenessMetric(Metric):
    """completeness = 该维度断言通过条数 / 总条数 × 100。

    断言定义须带 dimension="completeness"（3.1 产出 assertion_results 同域）。
    无该维度断言时 N/A（不参与加权，na_reason=metric_na）。
    断言取数失败/不合法按 fail 计（3.1 决策），通过率随之拉低。
    """

    dimension = DIM_COMPLETENESS

    def compute(self, ctx: MetricContext) -> MetricResult:
        rows = [r for r in ctx.assertion_results
                if r.get("dimension") == DIM_COMPLETENESS]
        if not rows:
            return MetricResult(na=True, na_reason="metric_na",
                                detail={"assertions": 0})
        passed = sum(1 for r in rows if r.get("pass"))
        score = passed / len(rows) * 100
        return MetricResult(score=score, detail={"pass": passed, "total": len(rows)})
