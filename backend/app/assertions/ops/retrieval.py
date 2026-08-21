"""检索溯源类断言算子（§7.1）：source 证据存在。"""
from __future__ import annotations

from app.assertions.base import AssertionOp
from app.assertions.path import resolve


class SourceHitOp(AssertionOp):
    """source_hit：检索类 tool_call 的 result 里含非空溯源证据（如 sources）。

    args: {tool?: 限定 tool name，path: 相对 tool_call 元素的取值路径（默认
    "result.sources"）}。任一命中（取到且非空）即通过；未命中返回 fail。
    """

    op = "source_hit"

    def run(self, unified: dict, args: dict) -> tuple[bool, object]:
        path = args.get("path", "result.sources")
        tool = args.get("tool")
        for e in self._tool_calls(unified, tool):
            try:
                val = resolve(e, path)
            except KeyError:
                continue
            if val not in (None, "", [], {}):
                return True, val
        return False, "<无命中的 source 证据>"
