"""评测维度插件基类（§7.2 / 3.2）：规则/judge/性能/成本四类统一接口。

维度分类（constants）：
    accuracy（score_per_dimension）：completeness/tool_usage 规则维度（断言通过率），
        factuality/reasoning 语义维度（judge 分级 rubric，3.4 填充分数）。
    performance（预聚合列）：ttft/e2e（毫秒，独立呈现，可配可选门禁）。
    cost（预聚合列）：token_cost（金额，独立呈现）。

score 语义由维度决定：accuracy=0-100；performance=原始毫秒值；cost=金额。
na=True 表示该维度不适用（judge 未判 / 无 ttft / 无价格），不计入加权。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar


@dataclass
class MetricContext:
    """评分层传入的维度计算上下文（eval_result 已聚合字段 + 外部数据）。"""

    assertion_results: list[dict] = field(default_factory=list)
    judge_results: list[dict] = field(default_factory=list)
    ttft_p50: float | None = None
    ttft_p95: float | None = None
    e2e_p50: float | None = None
    e2e_p95: float | None = None
    usage: list[dict] = field(default_factory=list)  # [{attempt, prompt_tokens, ...}]
    model: str | None = None
    model_price: dict | None = None  # {"input": 单价, "output": 单价}（评分层查好传入）


@dataclass
class MetricResult:
    """单维度计算结果。score=None 时 na 必须为 True（N/A 语义）。"""

    score: float | None = None
    na: bool = False
    na_reason: str | None = None  # 枚举见 plan §六：judge_fail / ttft_ns / e2e_ns / metric_na
    detail: dict = field(default_factory=dict)  # 证据明细（通过率/P50/P95/理由），L4 下钻


class Metric(ABC):
    """评测维度插件基类。

    每个维度一个实现，compute() 对 MetricContext 产出 MetricResult。自定义插件
    继承本类并注册进 registry（class_path 必须 ∈ ALLOWED_CLASS_PATHS 白名单）。
    """

    dimension: ClassVar[str] = ""

    @abstractmethod
    def compute(self, ctx: MetricContext) -> MetricResult:
        """计算维度分数。取不到必要数据时返回 na=True（不抛异常）。"""
