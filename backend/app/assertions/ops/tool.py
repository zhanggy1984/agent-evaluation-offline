"""工具使用类断言算子（§7.1）：tool_call 存在性。"""
from __future__ import annotations

from app.assertions.base import AssertionOp, AssertionOpError


class ToolCalledOp(AssertionOp):
    """tool_called：tool_calls 中存在 name==args.tool 的调用。args: {tool}。"""

    op = "tool_called"

    def run(self, unified: dict, args: dict) -> tuple[bool, object]:
        tool = args.get("tool")
        if not tool:
            raise AssertionOpError("tool_called 缺 args.tool")
        names = [e.get("name") for e in self._tool_calls(unified)]
        return tool in names, names


class ToolNotCalledOp(AssertionOp):
    """tool_not_called：tool_calls 中不存在 name==args.tool 的调用。args: {tool}。"""

    op = "tool_not_called"

    def run(self, unified: dict, args: dict) -> tuple[bool, object]:
        tool = args.get("tool")
        if not tool:
            raise AssertionOpError("tool_not_called 缺 args.tool")
        names = [e.get("name") for e in self._tool_calls(unified)]
        return tool not in names, names
