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


class KeywordNotContainsOp(AssertionOp):
    """keyword_not_contains：path 指向的文本不包含任一指定关键词（子串匹配）。

    防御性金丝雀断言：任一关键词出现在目标文本即 fail（match="all"，默认）——
    用于检测 answer 残留工具调用声明（DSML/XML 泄漏，gq 3161 根因：DeepSeek V4 把
    二次检索意图渲染成 DSML 声明泄漏进 answer）。即使 agent 侧拦截失效，评测侧也能
    第一时间捕获泄漏。
    match="any"（对称语义）：仅当全部关键词都出现才 fail（至少一个不出现即通过）。
    args: {path: 默认 "answer", keywords: [...], match: "all"(默认)/"any"}。
    """

    op = "keyword_not_contains"

    def run(self, unified: dict, args: dict) -> tuple[bool, object]:
        keywords = args.get("keywords")
        if not isinstance(keywords, list) or not keywords:
            raise AssertionOpError("keyword_not_contains 缺 args.keywords（非空 list）")
        path = args.get("path", "answer")
        try:
            val = resolve(unified, path)
        except KeyError:
            return False, f"<未取到 {path}>"
        if not isinstance(val, str):
            return False, f"<非文本: {type(val).__name__}>"
        hits = [k for k in keywords if k in val]
        want_all = args.get("match", "all") == "all"
        # match="all"（默认）：所有关键词都不出现才通过；任一出现即 fail
        # match="any"：至少一个关键词不出现即通过（全部出现才 fail）
        ok = not hits if want_all else len(hits) < len(keywords)
        return ok, val
