"""7.4 run_timeout 估算公式单元测试（estimate_run_timeout 纯函数，不触 DB）。

公式（solution_detail.md:879）：执行窗口 + judge 窗口，封顶 120min（RUN_TIMEOUT_CAP_S）。
显式配置优先于公式的分支逻辑在 orchestrator._run 内（非纯函数），此处只测估算函数本身。
"""
import unittest

from app.runner.orchestrator import RUN_TIMEOUT_CAP_S, estimate_run_timeout

# judge 参数默认值（seed DEFAULT_SYSTEM_CONFIG）
DEF = dict(judge_repeat=2, judge_concurrency=4, judge_call_timeout=120)


def _est(**kw):
    p = dict(n_cases=10, active_runs=0, repeat=1, max_iface_timeout=120,
             max_retries=1, semantic_tasks=0, **DEF)
    p.update(kw)
    return estimate_run_timeout(**p)


class TestEstimateRunTimeout(unittest.TestCase):
    def test_basic(self):
        # ceil(10/1)×1×120×2×1.5 = 3600，judge 0 → 3600
        self.assertEqual(_est(), 3600)

    def test_active_runs_division(self):
        # 在途 1 个 run：并发折算 ceil(10/2)=5 → 5×120×2×1.5=1800
        self.assertEqual(_est(active_runs=1), 1800)

    def test_active_runs_multiple(self):
        # 在途 3 个：ceil(10/4)=3 → 3×120×2×1.5=1080
        self.assertEqual(_est(active_runs=3), 1080)

    def test_repeat_factor(self):
        # repeat=5 相对 repeat=1 放大 5 倍（参数避开 120min 封顶）
        # 5 case×5×60×2×1.5=4500 vs repeat=1 基线 5×60×2×1.5=900
        self.assertEqual(_est(n_cases=5, repeat=5, max_iface_timeout=60), 4500)

    def test_retries_factor(self):
        # max_retries=2 → ×(2+1)=3 → 10×120×3×1.5=5400
        self.assertEqual(_est(max_retries=2), 5400)

    def test_iface_timeout(self):
        # cc 300s 相对 120s 放大 2.5 倍（避开封顶）：5 case×300×2×1.5=4500
        self.assertEqual(_est(n_cases=5, max_iface_timeout=300), 4500)

    def test_judge_window(self):
        # 语义任务 10：ceil(10×2/4)=5 ×120=600
        self.assertEqual(_est(semantic_tasks=10), 4200)

    def test_judge_concurrency_low(self):
        # 并发 1：ceil(10×2/1)=20 ×120=2400
        self.assertEqual(_est(semantic_tasks=10, judge_concurrency=1), 6000)

    def test_zero_cases(self):
        # 0 case：执行窗口 0，judge 0 → 0（run 层面空 case 前置已拦截，双保险）
        self.assertEqual(_est(n_cases=0), 0)

    def test_cap_120min(self):
        # 超大值封顶 7200
        self.assertEqual(_est(n_cases=1000, repeat=5, max_iface_timeout=300), RUN_TIMEOUT_CAP_S)

    def test_cap_exact_boundary(self):
        # 恰在封顶内不截断：3600 保持
        self.assertEqual(_est(), 3600)


if __name__ == "__main__":
    unittest.main()
