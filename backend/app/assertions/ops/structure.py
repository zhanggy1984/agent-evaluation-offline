"""结构类断言算子（§7.1）：字段存在 / 非空 / 值比较。"""
from __future__ import annotations

from app.assertions.base import AssertionOp, AssertionOpError
from app.assertions.path import resolve


class FieldPresentOp(AssertionOp):
    """field_present：path 指向的字段存在（值可为 None）。args: {path}。"""

    op = "field_present"

    def run(self, unified: dict, args: dict) -> tuple[bool, object]:
        path = args.get("path", "answer")
        try:
            val = resolve(unified, path)
        except KeyError:
            return False, f"<未取到 {path}>"
        return True, val


class FieldNonemptyOp(AssertionOp):
    """field_nonempty：path 指向的字段存在且非空（str/list/dict 长度>0）。"""

    op = "field_nonempty"

    def run(self, unified: dict, args: dict) -> tuple[bool, object]:
        path = args.get("path", "answer")
        try:
            val = resolve(unified, path)
        except KeyError:
            return False, f"<未取到 {path}>"
        if val is None:
            return False, val
        if isinstance(val, (str, list, dict, set, tuple)):
            return bool(val), val
        return True, val  # 数字/布尔等标量视为非空


class ValueEqualsOp(AssertionOp):
    """value_equals：path 指向的值 == args.expected（含类型）。args: {path, expected}。"""

    op = "value_equals"

    def run(self, unified: dict, args: dict) -> tuple[bool, object]:
        if "expected" not in args:
            raise AssertionOpError("value_equals 缺 args.expected")
        path = args.get("path", "answer")
        try:
            val = resolve(unified, path)
        except KeyError:
            return False, f"<未取到 {path}>"
        return val == args["expected"], val


class ValueRangeOp(AssertionOp):
    """value_range：path 指向的数值在 [min, max]（可只给一边）。args: {path, min?, max?}。"""

    op = "value_range"

    def run(self, unified: dict, args: dict) -> tuple[bool, object]:
        if "min" not in args and "max" not in args:
            raise AssertionOpError("value_range 至少给 min 或 max")
        path = args.get("path")
        try:
            val = resolve(unified, path)
        except KeyError:
            return False, f"<未取到 {path}>"
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            return False, f"<非数值: {type(val).__name__}={val!r}>"
        lo, hi = args.get("min"), args.get("max")
        if lo is not None and val < lo:
            return False, val
        if hi is not None and val > hi:
            return False, val
        return True, val
