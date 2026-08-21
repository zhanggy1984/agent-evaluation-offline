"""reasoning_quality 思考链：LLM-judge 分级 rubric（语义维度，3.4 填充分数）。"""
from __future__ import annotations

from app.core.constants import DIM_REASONING
from app.metrics.base import Metric, MetricContext, MetricResult


class ReasoningMetric(Metric):
    """reasoning_quality 分数从 ctx.judge_results 读（judge 层写入，3.4）。

    无对应 judge 结果 → N/A（na_reason=judge_fail）。detail 带 judge 理由。
    """

    dimension = DIM_REASONING

    def compute(self, ctx: MetricContext) -> MetricResult:
        for r in ctx.judge_results or []:
            if r.get("dimension") == DIM_REASONING and r.get("score") is not None:
                return MetricResult(score=r["score"],
                                    detail={"reason": r.get("reason")})
        return MetricResult(na=True, na_reason="judge_fail", detail={})
