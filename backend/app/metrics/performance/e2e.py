"""e2e 端到端延迟：评测端时钟 P50/P95（预聚合列）。"""
from __future__ import annotations

from app.core.constants import DIM_E2E
from app.metrics.base import Metric, MetricContext, MetricResult


class E2eMetric(Metric):
    """score = e2e_p50（毫秒）。无 done 采集（断流）→ N/A（na_reason=e2e_ns）。"""

    dimension = DIM_E2E

    def compute(self, ctx: MetricContext) -> MetricResult:
        if ctx.e2e_p50 is None:
            return MetricResult(na=True, na_reason="e2e_ns", detail={})
        return MetricResult(score=ctx.e2e_p50, na=False,
                            detail={"p50": ctx.e2e_p50, "p95": ctx.e2e_p95})
