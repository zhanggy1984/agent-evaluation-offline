"""评测维度注册表：内置维度注册 + 白名单加载（安全基线 §九-2，与 assertions 同模式）。"""
from __future__ import annotations

import importlib

from app.core.constants import ALLOWED_CLASS_PATHS
from app.metrics.base import Metric

_registry: dict[str, Metric] = {}


def register(metric: Metric) -> None:
    """注册维度实例（内置 seed 与自定义加载时调用；同 dimension 后注册覆盖）。"""
    if not metric.dimension:
        raise ValueError("维度 dimension 标识为空")
    _registry[metric.dimension] = metric


def get_metric(dimension: str) -> Metric:
    """按 dimension code 取维度实现；未注册抛 KeyError。"""
    return _registry[dimension]


def list_metrics() -> list[str]:
    return sorted(_registry)


def load_class(class_path: str) -> Metric:
    """白名单校验 + importlib 加载并实例化自定义维度（不自动注册）。"""
    if class_path not in ALLOWED_CLASS_PATHS:
        raise ValueError(f"class_path 不在白名单: {class_path}")
    full = f"app.{class_path}"  # class_path 相对 app 包（constants 约定）
    module_name, _, cls_name = full.rpartition(".")
    module = importlib.import_module(module_name)
    cls = getattr(module, cls_name)
    if not (isinstance(cls, type) and issubclass(cls, Metric)):
        raise ValueError(f"class_path 非 Metric 子类: {class_path}")
    return cls()


async def load_from_db(db) -> int:
    """从 metric_def 表加载全部 enabled 维度（内置重复注册无害）。

    startup 时调用一次；返回加载数。新增自定义维度后需重启或动态调用。
    """
    from sqlalchemy import select

    from app.models import MetricDef

    rows = (await db.execute(select(MetricDef).where(MetricDef.enabled))).scalars().all()
    for r in rows:
        register(load_class(r.class_path))
    return len(rows)
