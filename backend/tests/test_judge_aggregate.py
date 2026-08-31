"""app/judge/aggregate.py 档位多数决聚合单测（P0-1）。

关键不变量：
- tie 必须显式取低档（不能依赖 Counter.most_common 插入序，否则 repeat=2 平局非确定）
- reason 取多数档样本（不拼接离群理由）
- repeat = 实际采样数（可识别退化多数决）
"""
from app.judge.aggregate import aggregate_verdicts
from app.judge.client import JudgeVerdict


def _v(level: int, reason: str = "r") -> JudgeVerdict:
    return JudgeVerdict(dimension="factuality", level=level, score=level * 20,
                        reason=reason, rubric_version="1.2")


# ---------------- 多数决 ----------------
def test_majority_3_0():
    out = aggregate_verdicts([_v(4), _v(4), _v(4)])
    assert out["level"] == 4
    assert out["score"] == 80


def test_majority_2_1():
    # 2 档 4 + 1 档 3 → 多数决取 4（离群 3 不翻案）
    out = aggregate_verdicts([_v(4), _v(4), _v(3)])
    assert out["level"] == 4
    assert out["score"] == 80


def test_majority_all_low_fail():
    # 3 次全判低档（fail 档）→ 聚合也是低档（强 fail 语义保持）
    out = aggregate_verdicts([_v(1), _v(1), _v(1)])
    assert out["level"] == 1
    assert out["score"] == 20


# ---------------- tie 显式取低档（评审 P1-2，防非确定） ----------------
def test_tie_repeat2_takes_lower():
    # repeat=2 平局：4 先插入 / 3 后插入 → 必须取低档 3（不能因插入序翻向 4）
    out = aggregate_verdicts([_v(4), _v(3)])
    assert out["level"] == 3
    assert out["score"] == 60


def test_tie_repeat4_two_each_takes_lower():
    # repeat=4 平局 4/4 vs 3/3 → 取低档 3
    out = aggregate_verdicts([_v(4), _v(3), _v(3), _v(4)])
    assert out["level"] == 3


def test_tie_repeat2_reverse_insertion_order():
    # 验证不依赖插入序：3 先插入 / 4 后插入 → 仍取低档 3
    out = aggregate_verdicts([_v(3), _v(4)])
    assert out["level"] == 3


# ---------------- reason 取多数档样本（评审 P1-3） ----------------
def test_reason_taken_from_majority_anchor():
    out = aggregate_verdicts([_v(4, "答案准确"), _v(4, "与参考一致"), _v(3, "有明显缺陷")])
    assert out["reason"] == "答案准确"  # 取第一个多数档样本，不拼接离群"缺陷"理由


def test_outsider_reason_kept_in_repeats():
    out = aggregate_verdicts([_v(4, "准确"), _v(4, "准确"), _v(3, "有缺陷")])
    outsider = [r for r in out["repeats"] if r["level"] == 3]
    assert outsider and outsider[0]["reason"] == "有缺陷"  # 离群理由供 evidence 下钻


# ---------------- 结构字段 ----------------
def test_output_contains_dimension_and_rubric_version():
    out = aggregate_verdicts([_v(4)])
    assert out["dimension"] == "factuality"
    assert out["rubric_version"] == "1.2"


def test_repeat_is_actual_sample_count():
    out = aggregate_verdicts([_v(4), _v(4)])
    assert out["repeat"] == 2  # 实际采样数（区别于配置 judge_repeat）


def test_repeats_detail_matches_inputs():
    out = aggregate_verdicts([_v(4, "a"), _v(3, "b")])
    assert out["repeats"] == [
        {"level": 4, "score": 80, "reason": "a"},
        {"level": 3, "score": 60, "reason": "b"},
    ]


# ---------------- 边界 ----------------
def test_empty_input_returns_none():
    assert aggregate_verdicts([]) is None
