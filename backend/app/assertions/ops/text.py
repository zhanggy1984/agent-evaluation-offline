"""文本类断言算子（§7.1）：关键信息命中。

**关键词匹配一律大小写不敏感**（#235，2026-09-14 拍板）：词表侧 `sanitize_words` 归一，
算子侧再对两侧 `lower()`。两侧都做不是冗余——词表侧归一解决**去重与呈现一致**，算子侧
折叠解决**匹配**；只做前者正是原先那条漏判缺陷（`"Fallback"` 话术匹配不上折叠后的
`"fallback"`）。

⚠️ **两个算子共用 `_keyword_hits`，这是有意的结构性约束**：分别实现必然出现「只改了
一侧」的脱节，共用一处则不可能脱节。新增文本算子请一律走它，勿再手写 `k in val`。

⚠️ **折叠让短 ASCII 关键词的误命中面变大**（`"AI"` 会命中 `"he said"`）：对
`keyword_not_contains` 是假红（方向安全），对 `keyword_contains` 是**假绿**（方向不安全）。
未加长度闸（#235 遗留开放项）——注意**别把「真库实测 0 例」读成「已验无需加闸」**：
该 0 取自结构上装不下此风险的样本（详见 `core/error_payload.py` 文件头的量不出说明）。
"""
from __future__ import annotations

from app.assertions.base import AssertionOp, AssertionOpError
from app.assertions.path import resolve


def _keyword_hits(keywords: list, val: str) -> list[str]:
    """命中集：`keyword.lower() in val.lower()` 的子串匹配（大小写不敏感）。

    元素类型显式校验后抛 `AssertionOpError`——原实现 `k in val` 对非 str 元素抛
    `TypeError`，改成 `k.lower()` 后会变成 `AttributeError`（异常类型变了、报错更差），
    故补这一道守卫把错误路径钉死。
    """
    for k in keywords:
        if not isinstance(k, str):
            raise AssertionOpError(f"keywords 元素须为字符串，实为 {type(k).__name__}")
    low = val.lower()
    return [k for k in keywords if k.lower() in low]


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
        hits = _keyword_hits(keywords, val)
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
        # R-12：空/纯空白应答 = leakage「空话术」复发证据 ⇒ FAIL（**不是 na**：na = 复现没跑到，
        # fail = 跑到且答空，两条路径不可混）。空串天然不含任何 keyword，不显式判空就会被下面
        # 的 `not hits` 判成 PASS ⇒ 该抓的没抓到。纯空白必须一并判（只写 `== ""` 会漏 "   "）。
        if not val.strip():
            return False, "<空/纯空白应答 → 复现空答（leakage 空话术证据）>"
        hits = _keyword_hits(keywords, val)
        want_all = args.get("match", "all") == "all"
        # match="all"（默认）：所有关键词都不出现才通过；任一出现即 fail
        # match="any"：至少一个关键词不出现即通过（全部出现才 fail）
        ok = not hits if want_all else len(hits) < len(keywords)
        return ok, val
