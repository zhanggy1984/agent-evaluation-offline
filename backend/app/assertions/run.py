"""断言执行入口：统一结果对象 + 断言定义列表 → 逐条执行汇总（§3.1 / §7.1）。"""
from __future__ import annotations

from typing import Any

from app.assertions.base import AssertionOpError
from app.assertions.registry import get_op

_ACTUAL_TRUNCATE = 500  # 字符串快照上限，避免 L4 下钻 JSON 撑爆


def run_assertions(unified: dict, assertion_defs: list[dict]) -> list[dict]:
    """执行用例的全部结构断言。

    assertion_defs（test_case.assertions）：[{dimension, op, args, source_detail}]。
    返回（与 eval_result.assertion_results 存储结构一致，评分层直接消费）：
        [{dimension, op, args, expected, actual, pass, source_detail}]
    单条断言独立成败：取数失败/算子异常计 fail，不中断其余断言（3.1 决策）。
    """
    results = []
    for ad in assertion_defs or []:
        op_name = ad.get("op") or ""
        try:
            impl = get_op(op_name)
            passed, actual = impl.run(unified, ad.get("args") or {})
        except AssertionOpError as exc:
            # 断言定义不合法（缺必填 args / 算子未注册）→ 整条 fail + 记录原因
            passed, actual = False, f"<断言定义错误: {exc}>"
        except Exception as exc:  # 算子内部意外异常按 fail 处理，不中断用例
            passed, actual = False, f"<断言执行异常: {type(exc).__name__}>"
        results.append({
            "dimension": ad.get("dimension"),
            "op": op_name,
            "args": ad.get("args") or {},
            "expected": (ad.get("args") or {}).get("expected"),
            "actual": _truncate(actual),
            "pass": bool(passed),
            "source_detail": ad.get("source_detail"),
        })
    return results


def _truncate(actual: Any) -> Any:
    """字符串快照截断；非字符串原样返回。"""
    if isinstance(actual, str) and len(actual) > _ACTUAL_TRUNCATE:
        return actual[:_ACTUAL_TRUNCATE] + "…"
    return actual
