"""断言算子基类与统一结果对象契约（§7.1 结构断言插件，3.1）。

统一结果对象（ResultAssembler.to_unified）：
    {answer, reasoning, tool_calls, usage, meta}
断言定义（test_case.assertions 条目）：
    {dimension, op, args, source_detail}
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar


class AssertionOpError(Exception):
    """断言定义/加载错误：算子未注册、class_path 不合法、args 缺必填字段。"""


def filter_tool_calls(unified: dict, tool: str | None = None) -> list[dict]:
    """取 tool_calls 数组；tool 非空时过滤 name==tool 的元素。"""
    entries = list(unified.get("tool_calls") or [])
    if tool:
        return [e for e in entries if e.get("name") == tool]
    return entries


class AssertionOp(ABC):
    """断言算子插件基类。

    每个内置算子实现 run()，对统一结果对象执行结构断言；自定义算子继承本类并
    注册进 registry（class_path 必须 ∈ ALLOWED_CLASS_PATHS 白名单，安全基线 §九-2）。
    """

    op: ClassVar[str] = ""

    @abstractmethod
    def run(self, unified: dict, args: dict) -> tuple[bool, Any]:
        """执行断言，返回 (是否通过, 实际值快照)。

        args 为断言定义里的静态参数（期望值/路径/工具名等，3.1 决策：仅静态值）。
        取不到源值或类型不符：返回 (False, 描述串)，不抛异常——断言不满足即 fail
        （3.1 决策：取数失败计 fail 而非 error）。
        仅当 args 缺必填字段导致无法执行时抛 AssertionOpError。
        """

    def _tool_calls(self, unified: dict, tool: str | None = None) -> list[dict]:
        """子类取数助手：tool_calls 数组（可按 name 过滤）。"""
        return filter_tool_calls(unified, tool)
