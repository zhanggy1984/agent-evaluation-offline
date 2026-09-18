"""R-12 存量回归探针：翻转「空答 PASS→FAIL」会不会误伤已落库判定（**只读**）。

**为什么必须先跑这个**：`KeywordNotContainsOp` 是 manual/held_out 与 error_regression
**共享**算子（`run_assertions` 单一入口）⇒ 翻转同时作用于存量普通 case。

**⚠️ 口径必须用「被翻转的量」= `eval_result.answer`，不是断言结果里的 `actual`。**
`scorer._unified` 是 `{answer: r.answer or ""}` ⇒ **NULL answer 会被喂成空串**，
在算子层等价于空答。pre-scan 报告 Q① 量的是 `actual`（代理量），两者只在
`answer` 为 NULL 时分开——库里恰好有 NULL 行（manual 5 / error 11）。

判据：① 空 `answer` 且带 `keyword_not_contains` 断言的结果行 = 0（不误伤）
      ② 非空 `answer` 且带该断言的结果行 > 0（**判别力对照**：若 ① 与 ② 同时为 0，
         说明查询取不到数，① 不构成证据）

用法（容器内）：docker exec ai-eval-backend python tests/integration/r12_empty_answer_probe.py
"""
from __future__ import annotations

import json
import os
import sys

from sqlalchemy import create_engine, text

OP = "keyword_not_contains"


def main() -> int:
    url = (f"mysql+pymysql://{os.environ['DB_USER']}:{os.environ['DB_PASSWORD']}"
           f"@{os.environ['DB_HOST']}:{os.environ['DB_PORT']}/{os.environ['DB_NAME']}")
    blank_hit: list[str] = []
    nonblank = 0
    unregistered = 0
    n_rows = 0
    type_counts: dict = {}

    with create_engine(url).connect() as c:
        rows = c.execute(text(
            "select er.id, er.answer, er.assertion_results, r.trigger_type "
            "from eval_result er join eval_run r on r.id = er.run_id")).fetchall()

    for rid, ans, ar, tt in rows:
        parsed = json.loads(ar) if isinstance(ar, str) else (ar or [])
        mine = [a for a in parsed if a.get("op") == OP]
        if not mine:
            continue
        n_rows += 1
        type_counts[tt] = type_counts.get(tt, 0) + 1
        for a in mine:
            if "未注册" in str(a.get("actual") or ""):
                unregistered += 1
                break
        is_blank = ans is None or (isinstance(ans, str) and ans.strip() == "")
        if is_blank:
            shown = "NULL" if ans is None else repr(ans)
            blank_hit.append(f"er={rid} run_type={tt} answer={shown}")
        else:
            nonblank += 1

    print(f"带 {OP} 断言的结果行：{n_rows}（其中 {unregistered} 条 actual 为「算子未注册」历史行）")
    print(f"  ⚠️ 按 trigger_type 拆分（**判别力所系**）：{type_counts}")
    if not any(k != "error_regression" for k in type_counts):
        print("  ❗全部来自 error_regression ⇒ manual/held_out 上该断言**无样本**，")
        print("     判据①的 0 是平凡真，**不能**读作「存量普通 case 零误伤」。")
    print(f"\n判据① 空 answer 且带该断言 = {len(blank_hit)}  （期望 0）")
    for b in blank_hit[:10]:
        print("   ", b)
    print(f"判据② 非空 answer 且带该断言 = {nonblank}  （期望 > 0，作判别力对照）")

    fails = []
    if blank_hit:
        fails.append(f"判据①失败：{len(blank_hit)} 条存量行的判定会被 R-12 翻转（误伤）")
    if nonblank == 0:
        fails.append("判据②失败：对照为 0 ⇒ 查询取不到数，判据①不构成证据")
    if fails:
        print("\n❌ FAIL")
        for f in fails:
            print("  -", f)
        return 1
    print("\n✅ PASS：存量无会被翻转的行，且对照非空 ⇒ 判据①有意义")
    return 0


if __name__ == "__main__":
    sys.exit(main())
