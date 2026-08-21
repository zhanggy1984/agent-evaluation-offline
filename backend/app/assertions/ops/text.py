"""文本类断言算子（§7.1）：关键信息命中。"""
from __future__ import annotations

from app.assertions.base import AssertionOp, AssertionOpError
from app.assertions.path import resolve


class KeywordContainsOp(AssertionOp):
    """keyword_contains：path 指向的文本包含全部/任一关键词（子串匹配）。

    args: {path: 默认 "answer", keywords: [...], match: "all"(默认)/"any"}。
    """

    op = "keyword_contains"

    def run(self, unified: dict, args: dict) -> tuple[bool, object]:
        keywords = args.get("keywords")
        if not isinstance(keywords, list) or not keywords:
            raise AssertionOpError("keyword_contains 缺 args.keywords（非空 list）")
        path = args.get("path", "answer")
        try:
            val = resolve(unified, path)
        except KeyError:
            return False, f"<未取到 {path}>"
        if not isinstance(val, str):
            return False, f"<非文本: {type(val).__name__}>"
        hits = [k for k in keywords if k in val]
        want_all = args.get("match", "all") == "all"
        ok = len(hits) == len(keywords) if want_all else bool(hits)
        return ok, val
