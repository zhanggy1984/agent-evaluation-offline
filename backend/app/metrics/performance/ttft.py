"""ttft 首字延迟：评测端时钟 P50/P95（预聚合列，决策 #40）。"""
from __future__ import annotations

from app.core.constants import DIM_TTFT
from app.metrics.base import Metric, MetricContext, MetricResult


class TtftMetric(Metric):
    """score = ttft_p50（毫秒）。无 first_token 采集（同步接口/断流）→ N/A。

    na_reason=ttft_ns（plan §六：contract-check 同步不测首字标 N/A，决策 #40）。
    detail 带 p50/p95 供独立面板与可选门禁（TTFT P95 > 阈值告警）。
    """

    dimension = DIM_TTFT

    def compute(self, ctx: MetricContext) -> MetricResult:
        if ctx.ttft_p50 is None:
            return MetricResult(na=True, na_reason="ttft_ns", detail={})
        return MetricResult(score=ctx.ttft_p50, na=False,
                            detail={"p50": ctx.ttft_p50, "p95": ctx.ttft_p95})
