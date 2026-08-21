"""6.4a 元评测纯函数单测（零 DB 依赖，宿主直接跑）。

只 import app.judge.drift（与 test_dashboard 测 dashboard_rules 同模式）。
覆盖（CLAUDE.md 核心逻辑=业务分支）：
- dim_consistency：一致率聚合（容差 10 = 重判等级与金标准严格一致）、空输入、容差边界
- is_drift：低于阈值 / 等于阈值 / 高于阈值 / 判据缺失
HTTP（GET /meta/drift 序列可查、POST /meta/drift/check 权限 + 编排 + 告警幂等）
由容器 e2e 覆盖（6.4e）。
"""
import unittest

from app.judge.drift import DRIFT_SCORE_TOLERANCE, dim_consistency, is_drift


class TestDimConsistency(unittest.TestCase):
    """一致率：|重判分 − gold分| ≤ 10 计一致（score 步进 20 → 等级严格一致）。"""

    def test_all_agree(self):
        self.assertEqual(dim_consistency([(100.0, 100.0), (80.0, 80.0)]), 1.0)

    def test_level_mismatch_is_inconsistent(self):
        """等级差 1 → 分数差 20 > 容差 10 → 不一致。"""
        self.assertEqual(dim_consistency([(100.0, 80.0)]), 0.0)

    def test_tolerance_boundary_agree(self):
        """差恰为容差 10 → 计一致（如 gold 80 重判 90）。"""
        self.assertEqual(dim_consistency([(90.0, 100.0)]), 1.0)

    def test_partial_agree(self):
        self.assertEqual(dim_consistency([(100.0, 100.0), (60.0, 80.0)]), 0.5)

    def test_rounding(self):
        """1/3 一致 → 0.3333（4 位小数）。"""
        self.assertEqual(dim_consistency([(100.0, 100.0), (0.0, 80.0), (0.0, 80.0)]), 0.3333)

    def test_empty_returns_zero(self):
        """无可比判据 → 0.0（视同不达标，需人关注）。"""
        self.assertEqual(dim_consistency([]), 0.0)

    def test_tolerance_constant(self):
        """容差 10 是等级一致判据的关键常量（score 步进 20 的一半）。"""
        self.assertEqual(DRIFT_SCORE_TOLERANCE, 10.0)


class TestIsDrift(unittest.TestCase):
    """漂移判定：一致率 < 阈值 → 漂移；等于阈值不漂移；判据缺失视为漂移。"""

    def test_below_threshold(self):
        self.assertTrue(is_drift(0.6, 0.8))

    def test_equal_threshold_not_drift(self):
        self.assertFalse(is_drift(0.8, 0.8))

    def test_above_threshold(self):
        self.assertFalse(is_drift(0.9, 0.8))

    def test_missing_judgement_is_drift(self):
        """consistency_rate 缺失（无可比判据）同样视为漂移，需人关注。"""
        self.assertTrue(is_drift(None, 0.8))


if __name__ == "__main__":
    unittest.main()
