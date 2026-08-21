"""集合类断言算子（§7.1）：列表包含 / 违规集精确率-召回率。"""
from __future__ import annotations

from app.assertions.base import AssertionOp, AssertionOpError, filter_tool_calls
from app.assertions.path import MISSING, resolve_optional


def _extract_list(unified: dict, args: dict) -> object:
    """定位待断言列表：
    - args.tool 存在：从 tool_calls 中 name==tool 的元素按 args.path 取（相对该元素）
    - 否则：从统一结果对象按 args.path 取
    取不到返回 MISSING 哨兵。
    """
    if args.get("tool"):
        for e in filter_tool_calls(unified, args["tool"]):
            val = resolve_optional(e, args.get("path", "result"))
            if val is not MISSING:
                return val
        return MISSING
    path = args.get("path")
    if not path:
        return MISSING
    return resolve_optional(unified, path)


class ListContainsOp(AssertionOp):
    """list_contains：目标 list 包含 args.expected（list→子集，单值→成员）。

    args: {path | tool+path, expected}。
    """

    op = "list_contains"

    def run(self, unified: dict, args: dict) -> tuple[bool, object]:
        if "expected" not in args:
            raise AssertionOpError("list_contains 缺 args.expected")
        val = _extract_list(unified, args)
        if val is MISSING:
            return False, "<未取到目标列表>"
        if not isinstance(val, list):
            return False, f"<非列表: {type(val).__name__}>"
        exp = args["expected"]
        if isinstance(exp, list):
            ok = set(exp) <= set(val)
        else:
            ok = exp in val
        return ok, val


class ListPrecisionRecallOp(AssertionOp):
    """list_precision_recall：目标 list 与 args.expected 集合的 F1 ≥ 阈值。

    args: {path | tool+path, expected: [...], threshold?: 0.5}。
    F1 = 2PR/(P+R)；实际与期望皆空集合时按全中处理（通过）。
    实际快照含 precision/recall/f1/matched，支撑 L4 证据级下钻。
    """

    op = "list_precision_recall"

    def run(self, unified: dict, args: dict) -> tuple[bool, object]:
        expected = args.get("expected")
        if not isinstance(expected, list):
            raise AssertionOpError("list_precision_recall 缺 args.expected（list）")
        val = _extract_list(unified, args)
        if val is MISSING:
            return False, {"precision": 0.0, "recall": 0.0, "f1": 0.0,
                           "matched": [], "actual": [], "expected": expected}
        if not isinstance(val, list):
            return False, {"error": f"<非列表: {type(val).__name__}>",
                           "expected": expected}
        a, e = set(val), set(expected)
        inter = a & e
        # 期望空集合：无召回需求，recall 恒 1；实际空集合但期望非空：precision 0（全漏）
        prec = len(inter) / len(a) if a else (1.0 if not e else 0.0)
        rec = len(inter) / len(e) if e else 1.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        threshold = float(args.get("threshold", 0.5))
        return f1 >= threshold, {
            "precision": prec, "recall": rec, "f1": f1,
            "matched": sorted(inter), "actual": val, "expected": expected,
        }
