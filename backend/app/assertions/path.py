"""点号路径取值：dict 键 + list 下标（与 ConfigEngine 模板路径同语义）。"""
from __future__ import annotations

from typing import Any

MISSING = object()  # 哨兵：区分「路径缺失」与「值为 None」


def resolve(obj: Any, path: str) -> Any:
    """按点号路径取 obj 内值；dict 按键、list 按下标（如 tool_calls.0.name）。

    任一段取不到即抛 KeyError（调用方按 fail 处理，3.1 决策）。
    """
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            raise KeyError(path)
    return cur


def resolve_optional(obj: Any, path: str) -> Any:
    """resolve 容错版：取不到返回 MISSING 哨兵（供遍历匹配场景使用）。"""
    try:
        return resolve(obj, path)
    except KeyError:
        return MISSING
