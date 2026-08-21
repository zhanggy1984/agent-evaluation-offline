"""3.2 评测维度单元测试（规则/judge/性能/成本四类 Metric + 注册表）。

核心逻辑：各维度分数计算与 N/A 判定，用纯内存 MetricContext 喂，不触网络/DB。
"""
import unittest

from app.core.constants import ALLOWED_CLASS_PATHS
from app.metrics import MetricContext, get_metric, list_metrics, load_class
from app.metrics.registry import load_from_db


def _ctx(**kw) -> MetricContext:
    return MetricContext(**kw)


class TestCompleteness(unittest.TestCase):
    def setUp(self):
        self.m = get_metric("completeness")

    def test_all_pass(self):
        ctx = _ctx(assertion_results=[
            {"dimension": "completeness", "pass": True},
            {"dimension": "completeness", "pass": True}])
        r = self.m.compute(ctx)
        self.assertFalse(r.na)
        self.assertEqual(r.score, 100.0)
        self.assertEqual(r.detail["pass"], 2)

    def test_partial_pass(self):
        ctx = _ctx(assertion_results=[
            {"dimension": "completeness", "pass": True},
            {"dimension": "completeness", "pass": False}])
        r = self.m.compute(ctx)
        self.assertEqual(r.score, 50.0)

    def test_no_assertions_na(self):
        r = self.m.compute(_ctx())
        self.assertTrue(r.na)
        self.assertEqual(r.na_reason, "metric_na")

    def test_ignores_other_dimension(self):
        ctx = _ctx(assertion_results=[
            {"dimension": "tool_usage", "pass": False}])
        r = self.m.compute(ctx)
        self.assertTrue(r.na)  # 无 completeness 断言


class TestToolUsage(unittest.TestCase):
    def setUp(self):
        self.m = get_metric("tool_usage")

    def test_all_pass(self):
        ctx = _ctx(assertion_results=[
            {"dimension": "tool_usage", "pass": True},
            {"dimension": "tool_usage", "pass": True}])
        r = self.m.compute(ctx)
        self.assertEqual(r.score, 100.0)

    def test_fail_lowers(self):
        ctx = _ctx(assertion_results=[
            {"dimension": "tool_usage", "pass": True},
            {"dimension": "tool_usage", "pass": False},
            {"dimension": "tool_usage", "pass": False}])
        r = self.m.compute(ctx)
        self.assertAlmostEqual(r.score, 100 / 3)

    def test_no_assertions_na(self):
        self.assertTrue(self.m.compute(_ctx()).na)


class TestFactuality(unittest.TestCase):
    def setUp(self):
        self.m = get_metric("factuality")

    def test_reads_judge_result(self):
        ctx = _ctx(judge_results=[
            {"dimension": "factuality", "score": 80.0, "reason": "引用准确"},
            {"dimension": "reasoning_quality", "score": 60.0}])
        r = self.m.compute(ctx)
        self.assertFalse(r.na)
        self.assertEqual(r.score, 80.0)
        self.assertEqual(r.detail["reason"], "引用准确")

    def test_no_judge_result_na(self):
        r = self.m.compute(_ctx(judge_results=[]))
        self.assertTrue(r.na)
        self.assertEqual(r.na_reason, "judge_fail")


class TestReasoning(unittest.TestCase):
    def test_reads_judge_result(self):
        r = get_metric("reasoning_quality").compute(_ctx(judge_results=[
            {"dimension": "reasoning_quality", "score": 60.0}]))
        self.assertEqual(r.score, 60.0)

    def test_no_judge_result_na(self):
        self.assertTrue(get_metric("reasoning_quality").compute(_ctx()).na)


class TestTtft(unittest.TestCase):
    def setUp(self):
        self.m = get_metric("ttft")

    def test_with_p50(self):
        r = self.m.compute(_ctx(ttft_p50=120.5, ttft_p95=300.0))
        self.assertFalse(r.na)
        self.assertEqual(r.score, 120.5)
        self.assertEqual(r.detail["p95"], 300.0)

    def test_no_ttft_na(self):
        r = self.m.compute(_ctx())
        self.assertTrue(r.na)
        self.assertEqual(r.na_reason, "ttft_ns")  # 同步接口不测首字（决策 #40）


class TestE2e(unittest.TestCase):
    def test_with_p50(self):
        r = get_metric("e2e").compute(_ctx(e2e_p50=800.0, e2e_p95=1200.0))
        self.assertEqual(r.score, 800.0)

    def test_no_e2e_na(self):
        r = get_metric("e2e").compute(_ctx())
        self.assertTrue(r.na)
        self.assertEqual(r.na_reason, "e2e_ns")


class TestTokenCost(unittest.TestCase):
    def setUp(self):
        self.m = get_metric("token_cost")

    def test_cost_sum(self):
        # 单价单位 = 元/百万 token → 金额(元) = tokens × 单价 / 1e6
        ctx = _ctx(
            usage=[{"prompt_tokens": 1000, "completion_tokens": 500},
                   {"prompt_tokens": 2000, "completion_tokens": 1000}],
            model_price={"input": 1.5, "output": 4.5})
        r = self.m.compute(ctx)
        # (1000*1.5+500*4.5)/1e6 + (2000*1.5+1000*4.5)/1e6 = 0.00375+0.0075 = 0.01125
        self.assertAlmostEqual(r.score, 0.01125)
        self.assertEqual(r.detail["attempts"], 2)

    def test_cost_cache_hit(self):
        # 7.4 cache 口径：命中部分按 cache_hit_price，未命中按 input
        ctx = _ctx(
            usage=[{"prompt_tokens": 1000, "prompt_cache_hit_tokens": 600,
                    "completion_tokens": 400}],
            model_price={"input": 1.5, "output": 4.5, "cache_hit": 0.1})
        r = self.m.compute(ctx)
        # 600×0.1 + 400×1.5 + 400×4.5 = 2460 → 0.00246
        self.assertAlmostEqual(r.score, 0.00246)

    def test_cost_cache_fallback(self):
        # price 未配 cache_hit → 命中部分回退 input（与旧公式一致）
        ctx = _ctx(
            usage=[{"prompt_tokens": 1000, "prompt_cache_hit_tokens": 600,
                    "completion_tokens": 400}],
            model_price={"input": 1.5, "output": 4.5})
        r = self.m.compute(ctx)
        self.assertAlmostEqual(r.score, 0.0033)  # 1000×1.5 + 400×4.5

    def test_no_price_na(self):
        r = self.m.compute(_ctx(usage=[{"prompt_tokens": 1}]))
        self.assertTrue(r.na)

    def test_no_usage_na(self):
        r = self.m.compute(_ctx(model_price={"input": 1, "output": 1}))
        self.assertTrue(r.na)


class TestRegistry(unittest.TestCase):
    def test_all_builtins_registered(self):
        for dim in ("completeness", "tool_usage", "factuality", "reasoning_quality",
                    "ttft", "e2e", "token_cost"):
            self.assertIn(dim, list_metrics())

    def test_unknown_raises(self):
        with self.assertRaises(KeyError):
            get_metric("no_such_dim")

    def test_load_class_from_whitelist(self):
        m = load_class("metrics.accuracy.completeness.CompletenessMetric")
        self.assertEqual(m.dimension, "completeness")

    def test_load_class_rejects_whitelist(self):
        with self.assertRaises(ValueError):
            load_class("os.system")

    def test_whitelist_covers_all_builtin_paths(self):
        # 内置 7 个维度 class_path 必须全部 ∈ 白名单（防 seed/加载失败）
        from app.core.constants import DIMENSION_METRIC_CLASS
        for path in DIMENSION_METRIC_CLASS.values():
            self.assertIn(path, ALLOWED_CLASS_PATHS)


if __name__ == "__main__":
    unittest.main()
