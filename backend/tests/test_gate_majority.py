"""scripts/_gate_majority.py 门禁多数决 V2 判定逻辑单测。

规则维度（completeness/tool_usage）3 次全过才过；judge 维度（factuality/
reasoning_quality）≥2/3 多数决、0/3 全 fail 强 fail；全 N/A 不判。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from _gate_majority import decide_case  # noqa: E402


# ---------------- 规则维度：3 次全过才过 ----------------
def test_rule_all_pass_ok():
    ok, jok, rr, jr = decide_case({"completeness": [90.0, 90.0, 90.0]},
                                  {"completeness": 80.0})
    assert ok is True and jok is True and rr == [] and jr == []


def test_rule_one_fail_fails():
    # 规则断言确定性高：3 次中 1 次低于 target 即真实问题，不做多数决
    ok, jok, rr, jr = decide_case({"completeness": [90.0, 70.0, 90.0]},
                                  {"completeness": 80.0})
    assert ok is False
    assert any("completeness" in r for r in rr)


def test_rule_exact_target_ok():
    # 恰好等于 target（>= 判定）视为过
    ok, jok, rr, jr = decide_case({"tool_usage": [80.0, 80.0, 80.0]},
                                  {"tool_usage": 80.0})
    assert ok is True


# ---------------- judge 维度：≥2/3 多数决 ----------------
def test_judge_three_pass_ok():
    ok, jok, rr, jr = decide_case({"factuality": [90.0, 90.0, 90.0]},
                                  {"factuality": 60.0})
    assert jok is True


def test_judge_two_of_three_ok():
    # 单次 80 漂移是 LLM 采样噪声，2/3 过即放行（3182 铁证：同回答 3 次 fail/pass/pass）
    ok, jok, rr, jr = decide_case({"factuality": [90.0, 50.0, 90.0]},
                                  {"factuality": 60.0})
    assert jok is True


def test_judge_one_of_three_fails():
    ok, jok, rr, jr = decide_case({"factuality": [90.0, 50.0, 50.0]},
                                  {"factuality": 60.0})
    assert jok is False


def test_judge_zero_of_three_strong_fail():
    # 0/3 全 fail（概率 0.008）是真实退化信号，强 fail 不可翻案
    ok, jok, rr, jr = decide_case({"factuality": [50.0, 50.0, 50.0]},
                                  {"factuality": 60.0})
    assert jok is False
    assert any("全fail" in r for r in jr)


# ---------------- 边界与组合 ----------------
def test_na_all_skipped():
    # 值全为 None（N/A）的维度不判
    ok, jok, rr, jr = decide_case({"factuality": [None, None, None]},
                                  {"factuality": 60.0})
    assert jok is True and jr == []


def test_mixed_na_and_values():
    # 部分 N/A：仅判有值部分
    ok, jok, rr, jr = decide_case({"factuality": [None, 90.0, 90.0]},
                                  {"factuality": 60.0})
    assert jok is True


def test_rule_and_judge_compose():
    # 规则过 + judge 2/3 → case 过；任一维度 fail 由调用方合并成 case FAIL
    ok, jok, rr, jr = decide_case(
        {"completeness": [90.0, 90.0, 90.0], "factuality": [90.0, 50.0, 90.0]},
        {"completeness": 80.0, "factuality": 60.0})
    assert ok is True and jok is True


def test_rule_fail_but_judge_ok():
    ok, jok, rr, jr = decide_case(
        {"completeness": [90.0, 70.0, 90.0], "factuality": [90.0, 90.0, 90.0]},
        {"completeness": 80.0, "factuality": 60.0})
    assert ok is False and jok is True
