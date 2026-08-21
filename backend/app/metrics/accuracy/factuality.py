"""factuality 事实性：LLM-judge 分级 rubric（语义维度，3.4 填充分数）。"""
from __future__ import annotations

from app.core.constants import DIM_FACTUALITY
from app.metrics.base import Metric, MetricContext, MetricResult


class FactualityMetric(Metric):
    """factuality 分数从 ctx.judge_results 读（judge 层写入，3.4）。

    无对应 judge 结果 → N/A（na_reason=judge_fail，门禁按 judge_incomplete 保守判定，
    见 §15.2）。detail 带 judge 理由支撑 L4 下钻。
    """

    dimension = DIM_FACTUALITY

    def compute(self, ctx: MetricContext) -> MetricResult:
        for r in ctx.judge_results or []:
            if r.get("dimension") == DIM_FACTUALITY and r.get("score") is not None:
                return MetricResult(score=r["score"],
                                    detail={"reason": r.get("reason")})
        return MetricResult(na=True, na_reason="judge_fail", detail={})
