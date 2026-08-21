"""6.4b 留出集复测 overfit 纯函数单测（零 DB 依赖，宿主直接跑）。

只 import app.runner.overfit 的纯函数（overfit_gap/is_overfit）；
check_overfit 的 DB 编排（held_out run 评分后比对 manual + 告警幂等）由容器 e2e 覆盖。
"""
import unittest

from app.runner.overfit import is_overfit, overfit_gap


class TestOverfitGap(unittest.TestCase):
    """overfit_gap = 最近 manual agent_score − 最近 N 次 held_out agent_score 均值。"""

    def test_basic(self):
        self.assertEqual(overfit_gap(80.0, [70.0]), 10.0)

    def test_manual_higher(self):
        self.assertEqual(overfit_gap(90.0, [60.0]), 30.0)

    def test_manual_lower(self):
        """manual 低于 held_out（留出集反而更好）→ 负 gap，不算过拟合。"""
        self.assertEqual(overfit_gap(70.0, [80.0]), -10.0)

    def test_avg_over_window(self):
        """多次 held_out 取均值：90 − (70+90)/2 = 10，单次低分不放大。"""
        self.assertEqual(overfit_gap(90.0, [70.0, 90.0]), 10.0)

    def test_three_runs_avg(self):
        self.assertEqual(overfit_gap(100.0, [60.0, 80.0, 100.0]), 20.0)

    def test_missing_manual(self):
        self.assertIsNone(overfit_gap(None, [70.0]))

    def test_empty_held(self):
        self.assertIsNone(overfit_gap(80.0, []))

    def test_both_missing(self):
        self.assertIsNone(overfit_gap(None, []))


class TestIsOverfit(unittest.TestCase):
    """过拟合判定：gap > 阈值；gap 缺失（无法比对）不算。"""

    def test_above_threshold(self):
        self.assertTrue(is_overfit(16.0, 15.0))

    def test_equal_threshold(self):
        self.assertFalse(is_overfit(15.0, 15.0))

    def test_below_threshold(self):
        self.assertFalse(is_overfit(10.0, 15.0))

    def test_negative_gap(self):
        self.assertFalse(is_overfit(-10.0, 15.0))

    def test_missing_gap(self):
        self.assertFalse(is_overfit(None, 15.0))


if __name__ == "__main__":
    unittest.main()
