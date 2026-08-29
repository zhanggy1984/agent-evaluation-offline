"""system_config 热改校验（P2-C2）。

配置契约 meta 定义在 seed.DEFAULT_SYSTEM_CONFIG（单一真相源），本模块只做校验逻辑。
规则：type（int/number/str/list）必填；int/number 配 min/max，str 配 max_len，
list 配 item_type/max_items；nullable=True 允许 None（如 run_timeout=估算）。
meta 缺失 → 拒绝（强制新 key 带契约，防「可热改但无校验」的配置项漏网）。
"""
from __future__ import annotations

from typing import Any


def validate_sysconfig_value(key: str, value: Any, meta: dict | None) -> str | None:
    """校验单个配置值。返回错误消息；None=合法。"""
    if not meta:
        return f"{key} 缺少配置契约（meta），拒绝热改"
    vtype = meta.get("type")
    if meta.get("nullable") and value is None:
        return None
    if vtype == "int":
        # bool 是 int 子类（True==1）须单独排除，否则真/假当数值放行
        if isinstance(value, bool) or not isinstance(value, int):
            return f"{key} 应为整数，收到 {type(value).__name__}"
        return _check_range(key, value, meta)
    if vtype == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"{key} 应为数字，收到 {type(value).__name__}"
        return _check_range(key, value, meta)
    if vtype == "str":
        if not isinstance(value, str):
            return f"{key} 应为字符串，收到 {type(value).__name__}"
        max_len = meta.get("max_len")
        if max_len is not None and len(value) > max_len:
            return f"{key} 长度 {len(value)} 超过上限 {max_len}"
        return None
    if vtype == "list":
        if not isinstance(value, list):
            return f"{key} 应为数组，收到 {type(value).__name__}"
        if meta.get("item_type") == "str":
            bad = [v for v in value if not isinstance(v, str)]
            if bad:
                return f"{key} 数组含非字符串项: {bad[:3]}"
        max_items = meta.get("max_items")
        if max_items is not None and len(value) > max_items:
            return f"{key} 数组长度 {len(value)} 超过上限 {max_items}"
        return None
    return f"{key} 配置契约 type 非法: {vtype!r}"


def _check_range(key: str, value: int | float, meta: dict) -> str | None:
    lo, hi = meta.get("min"), meta.get("max")
    if lo is not None and value < lo:
        return f"{key}={value} 小于下限 {lo}"
    if hi is not None and value > hi:
        return f"{key}={value} 大于上限 {hi}"
    return None
