"""token_cost：usage × model_price（金额，预聚合列，独立呈现）。"""
from __future__ import annotations

from app.core.constants import DIM_TOKEN_COST
from app.metrics.base import Metric, MetricContext, MetricResult


class TokenCostMetric(Metric):
    """score = 全部 attempt token 成本合计（cache hit×cache_hit_price + 未命中×input + completion×output），单位元。

    单价单位 = 元/百万 token → 金额(元) = tokens × 单价 / 1e6。
    单价由评分层按 model 查 model_price 传入 ctx.model_price={"input","output","cache_hit"}。
    7.4 cache 口径：命中缓存部分按 cache_hit_price（未配 → 回退 input，兼容既有数据零变化），
    与 scorer._total_cost 同公式（对齐 DeepSeek 上下文缓存账单）。
    无 usage 或无价格 → N/A（na_reason=metric_na，避免 0 成本误判）。
    """

    dimension = DIM_TOKEN_COST

    def compute(self, ctx: MetricContext) -> MetricResult:
        usages = [u for u in ctx.usage or [] if u]
        price = ctx.model_price
        if not usages or not price:
            return MetricResult(na=True, na_reason="metric_na",
                                detail={"attempts": len(usages),
                                        "has_price": bool(price)})
        input_price = price.get("input", 0)
        cache_price = price.get("cache_hit")
        if cache_price is None:
            cache_price = input_price
        total = 0.0
        for u in usages:
            prompt = u.get("prompt_tokens") or 0
            cache_hit = min(u.get("prompt_cache_hit_tokens") or 0, prompt)
            total += (cache_hit * cache_price
                      + (prompt - cache_hit) * input_price
                      + (u.get("completion_tokens") or 0) * price.get("output", 0))
        total /= 1e6
        return MetricResult(score=total, na=False,
                            detail={"total_cost": total, "attempts": len(usages)})
