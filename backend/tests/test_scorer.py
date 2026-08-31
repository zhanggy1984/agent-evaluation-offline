"""3.3 评分合成与门禁单元测试（score_case 纯函数 + 辅助函数）。

核心逻辑：N/A 归一化加权、门禁判定、judge_incomplete 权重、成本计算、target 接口覆盖。
不触 DB：score_case/_enabled_dims/_total_cost/_resolve_targets/_unified/_aggregate_precomputed。
"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.runner.scorer import (
    _aggregate_precomputed, _enabled_dims, _gate_met, _resolve_targets, _resolve_weights,
    _score_executed_results, _total_cost, _unified, score_case,
)

# 断言算子输出的最小形态（实际由 run_assertions 产出）
DEF_WEIGHTS = {"completeness": 0.3, "factuality": 0.35, "reasoning_quality": 0.15, "tool_usage": 0.2}


def _base(**kw):
    """score_case 默认入参（无断言/judge/性能/成本 → 全 N/A）。"""
    p = dict(case_metrics=None, assertion_results=[], judge_results=None,
             ttft_p50=None, ttft_p95=None, e2e_p50=None, e2e_p95=None,
             usage=None, model_price=None, weights=DEF_WEIGHTS, targets={})
    p.update(kw)
    return score_case(**p)


class TestWeightedComposition(unittest.TestCase):
    def test_full_pass_rules_only(self):
        # 规则两维满分；语义两维无 judge → N/A，剔除后重归一化
        r = _base(assertion_results=[
            {"dimension": "completeness", "pass": True},
            {"dimension": "completeness", "pass": True},
            {"dimension": "tool_usage", "pass": True},
        ])
        # acc = completeness(100,w.3) + tool_usage(100,w.2)，total_w=0.5
        self.assertEqual(r.score_total, 100.0)
        self.assertEqual(r.pass_fail, "pass")
        # 语义 N/A 权重 0.35+0.15=0.5 / 总 1.0 → 50% > 30% → run 会标 judge_incomplete
        self.assertAlmostEqual(r.semantic_na_weight, 0.5)
        self.assertAlmostEqual(r.total_accuracy_weight, 1.0)

    def test_partial_pass_renormalize(self):
        r = _base(assertion_results=[
            {"dimension": "completeness", "pass": True},
            {"dimension": "completeness", "pass": True},
            {"dimension": "completeness", "pass": False},   # completeness 66.67
            {"dimension": "tool_usage", "pass": True},
        ])
        # (66.67*0.3 + 100*0.2) / 0.5 = (20+20)/0.5 = 80
        self.assertAlmostEqual(r.score_total, 80.0, places=2)
        self.assertEqual(r.pass_fail, "pass")

    def test_enabled_filter_skips_unlisted(self):
        # 只启用 completeness：semantic 不参与（也不计 N/A 权重）
        r = _base(case_metrics={"completeness": {"enabled": True}},
                  assertion_results=[
                      {"dimension": "completeness", "pass": True},
                      {"dimension": "completeness", "pass": True}])
        self.assertEqual(r.score_total, 100.0)
        self.assertEqual(len([p for p in r.score_per_dimension if not p["na"]]), 1)
        self.assertEqual(r.semantic_na_weight, 0.0)

    def test_all_disabled_all_na(self):
        r = _base(case_metrics={d: {"enabled": False} for d in DEF_WEIGHTS})
        self.assertIsNone(r.score_total)
        self.assertEqual(r.pass_fail, "na")
        self.assertEqual(r.na_reason, "metric_na")  # 首个 N/A（completeness 无断言）


class TestGate(unittest.TestCase):
    def test_target_met_pass(self):
        r = _base(assertion_results=[
            {"dimension": "completeness", "pass": True},
            {"dimension": "completeness", "pass": True},
            {"dimension": "tool_usage", "pass": True}],
            targets={"completeness": 90.0})
        self.assertEqual(r.pass_fail, "pass")
        self.assertFalse(r.gate_failed)

    def test_target_unmet_fail(self):
        r = _base(assertion_results=[
            {"dimension": "completeness", "pass": True},
            {"dimension": "completeness", "pass": False},  # 50
            {"dimension": "tool_usage", "pass": True}],
            targets={"completeness": 60.0})
        self.assertEqual(r.pass_fail, "fail")
        self.assertTrue(r.gate_failed)
        # 仍给出总分（门禁只影响 pass/fail）
        self.assertIsNotNone(r.score_total)

    def test_na_dimension_not_gated(self):
        # 无 judge → factuality N/A，不参与门禁；目标只压 factuality 也不判 fail
        r = _base(assertion_results=[
            {"dimension": "completeness", "pass": True}],
            targets={"factuality": 99.0})
        self.assertEqual(r.pass_fail, "pass")
        self.assertFalse(r.gate_failed)

    def test_non_accuracy_target_ignored(self):
        r = _base(assertion_results=[
            {"dimension": "completeness", "pass": True},
            {"dimension": "tool_usage", "pass": True}],
            targets={"ttft": 1000.0})  # 性能维度不在门禁范围
        self.assertEqual(r.pass_fail, "pass")

    def test_gate_uses_aggregated_majority_score(self):
        # P0-1：judge 多次采样聚合 result（顶层 score=多数档 80 + repeats 明细）
        # → 门禁按聚合分判，不因 repeats 键存在偏移
        agg = {
            "dimension": "factuality", "level": 4, "score": 80.0,
            "reason": "答案准确", "rubric_version": "1.2", "repeat": 3,
            "repeats": [
                {"level": 4, "score": 80.0, "reason": "答案准确"},
                {"level": 4, "score": 80.0, "reason": "与参考一致"},
                {"level": 3, "score": 60.0, "reason": "有明显缺陷"},
            ],
        }
        common = {"completeness": [{"dimension": "completeness", "pass": True}],
                  "tool_usage": [{"dimension": "tool_usage", "pass": True}]}
        # #2 档位化：80 与 85 同档（4 档）→ 不再悬崖 fail（修复硬伤 P1-2「85 只有 100 能过」）
        r_met = _base(assertion_results=common["completeness"] + common["tool_usage"],
                      judge_results=[agg], targets={"factuality": 85.0})
        self.assertEqual(r_met.pass_fail, "pass")
        self.assertFalse(r_met.gate_failed)
        # 跨档仍 fail：80（4 档）对 100（5 档）不达标
        r_fail = _base(assertion_results=common["completeness"] + common["tool_usage"],
                       judge_results=[agg], targets={"factuality": 100.0})
        self.assertEqual(r_fail.pass_fail, "fail")
        self.assertTrue(r_fail.gate_failed)
        # 同档下限边界：target 92 也归 4 档，80 可过
        r_floor = _base(assertion_results=common["completeness"] + common["tool_usage"],
                        judge_results=[agg], targets={"factuality": 92.0})
        self.assertEqual(r_floor.pass_fail, "pass")


class TestGateMet(unittest.TestCase):
    """#2 门禁档位化判定矩阵：语义维度 floor 档、规则维度连续分、异常回退。"""

    def test_semantic_floor_bucket(self):
        # 85/92 target 归 4 档：80 可过（消除「85 只有 100 能过」悬崖）
        for t in (80.0, 85.0, 92.0):
            self.assertTrue(_gate_met(80.0, t, "factuality"), f"80 对 {t} 应过")
        # 100 target 归 5 档：80/92 均不过，100 过
        self.assertFalse(_gate_met(80.0, 100.0, "factuality"))
        self.assertFalse(_gate_met(92.0, 100.0, "factuality"))
        self.assertTrue(_gate_met(100.0, 100.0, "factuality"))
        # 跨档下探仍不过：60（3 档）对 80（4 档）
        self.assertFalse(_gate_met(60.0, 80.0, "factuality"))

    def test_semantic_low_target(self):
        # target 归 0 档：任何非负语义分（≥0 档）都过
        self.assertTrue(_gate_met(0.0, 0.0, "reasoning_quality"))
        self.assertTrue(_gate_met(20.0, 0.0, "reasoning_quality"))
        self.assertTrue(_gate_met(0.0, 5.0, "reasoning_quality"))

    def test_rule_dimension_continuous(self):
        # 规则维度通过率连续分：79 对 80 不过（档位化会错误放宽，保持连续比较）
        self.assertFalse(_gate_met(79.0, 80.0, "completeness"))
        self.assertTrue(_gate_met(80.0, 80.0, "completeness"))
        self.assertTrue(_gate_met(90.0, 80.0, "completeness"))
        self.assertFalse(_gate_met(79.0, 80.0, "tool_usage"))

    def test_none_or_negative_fallback(self):
        # 无档位语义 → 连续分比较（fail 方向保守）
        self.assertFalse(_gate_met(None, 85.0, "factuality"))
        self.assertFalse(_gate_met(80.0, None, "factuality"))
        self.assertFalse(_gate_met(-5.0, 85.0, "factuality"))
        # 负数回退连续分比较：-5 ≥ -10 过，-20 < -10 不过
        self.assertTrue(_gate_met(-5.0, -10.0, "factuality"))
        self.assertFalse(_gate_met(-20.0, -10.0, "factuality"))


class TestNaProtection(unittest.TestCase):
    """硬伤 #6 judge 判分失败 N/A 保护：judge_failed_dims 权重占比超阈值 → na。

    只对「该 case 有 judge 任务但判分失败」的维度保护；judge 未配置/无任务不触发。
    """

    def _pass_rules(self):
        return [{"dimension": "completeness", "pass": True},
                {"dimension": "completeness", "pass": True},
                {"dimension": "tool_usage", "pass": True}]

    def _judge_fail(self, dim="factuality"):
        return [{"dimension": dim, "level": 1, "score": 0.0}]

    def test_factuality_fail_triggers_na(self):
        # factuality 权重 0.35/1.0 = 0.35 > 阈值 0.3 → na（决策点 A1 明示的不对称触发侧）
        r = _base(assertion_results=self._pass_rules(),
                  judge_results=self._judge_fail(),
                  judge_failed_dims={"factuality"})
        self.assertEqual(r.pass_fail, "na")
        self.assertEqual(r.na_reason, "semantic_na_high")

    def test_reasoning_fail_not_triggers(self):
        # A1 明示：reasoning 权重 0.15 < 0.3 → 不触发，pass（已知不对称行为）
        r = _base(assertion_results=self._pass_rules(),
                  judge_results=self._judge_fail("reasoning_quality"),
                  judge_failed_dims={"reasoning_quality"})
        self.assertEqual(r.pass_fail, "pass")
        self.assertFalse(r.gate_failed)

    def test_combined_semantic_fail_triggers(self):
        # factuality + reasoning = 0.5 > 0.3 → na
        r = _base(assertion_results=self._pass_rules(),
                  judge_results=self._judge_fail(),
                  judge_failed_dims={"factuality", "reasoning_quality"})
        self.assertEqual(r.pass_fail, "na")

    def test_no_failed_dims_not_triggers(self):
        # judge 未配置（judge_failed_dims=None）/无判分失败（空集）→ 不保护，pass
        r1 = _base(assertion_results=self._pass_rules(),
                   judge_results=self._judge_fail())
        self.assertEqual(r1.pass_fail, "pass")
        r2 = _base(assertion_results=self._pass_rules(),
                   judge_results=self._judge_fail(), judge_failed_dims=set())
        self.assertEqual(r2.pass_fail, "pass")

    def test_gate_fail_not_masked(self):
        # 门禁 fail 优先：judge 失败占比超阈值也不把 fail 掩蔽成 na（保护放门禁后）
        r = _base(assertion_results=[
            {"dimension": "completeness", "pass": True},
            {"dimension": "completeness", "pass": False},  # 50
            {"dimension": "tool_usage", "pass": True}],
            judge_results=self._judge_fail(),
            judge_failed_dims={"factuality"},
            targets={"completeness": 60.0})
        self.assertEqual(r.pass_fail, "fail")
        self.assertTrue(r.gate_failed)

    def test_zero_weight_all_na_no_crash(self):
        # 除零守卫：四维权重全 0 + 全 N/A → score_total=None 走 na，不除零不崩
        r = _base(assertion_results=[], judge_results=None,
                  judge_failed_dims={"factuality"},
                  weights={d: 0.0 for d in DEF_WEIGHTS})
        self.assertEqual(r.pass_fail, "na")
        self.assertIsNone(r.score_total)

    def test_custom_threshold_low(self):
        # judge_na_threshold 调低（0.1）后 reasoning 单独失败也触发
        r = _base(assertion_results=self._pass_rules(),
                  judge_results=self._judge_fail("reasoning_quality"),
                  judge_failed_dims={"reasoning_quality"}, judge_na_threshold=0.1)
        self.assertEqual(r.pass_fail, "na")


class TestJudgeIncomplete(unittest.TestCase):
    def test_semantic_na_weight_ratio(self):
        # 只给 completeness 断言：factuality/reasoning 全 N/A，权重 0.5/1.0
        r = _base(assertion_results=[
            {"dimension": "completeness", "pass": True}])
        self.assertAlmostEqual(r.semantic_na_weight / r.total_accuracy_weight, 0.5)

    def test_semantic_scored_ratio_zero(self):
        # 语义有 judge 分数 → 不计 N/A 权重；四维齐全总分 = (30+28+9+20)/1.0
        r = _base(assertion_results=[
            {"dimension": "completeness", "pass": True},
            {"dimension": "tool_usage", "pass": True}],
            judge_results=[
                {"dimension": "factuality", "score": 80.0},
                {"dimension": "reasoning_quality", "score": 60.0}])
        self.assertEqual(r.semantic_na_weight, 0.0)
        self.assertAlmostEqual(r.score_total, 87.0)


class TestCost(unittest.TestCase):
    def test_cost_with_price(self):
        # 单价单位 = 元/百万 token → 金额(元) = tokens × 单价 / 1e6
        r = _base(assertion_results=[{"dimension": "completeness", "pass": True}],
                  usage=[{"prompt_tokens": 1000, "completion_tokens": 500}],
                  model_price={"input": 1.5, "output": 4.5})
        self.assertAlmostEqual(r.total_cost, (1000 * 1.5 + 500 * 4.5) / 1e6)  # 0.00375

    def test_cost_without_price_none(self):
        r = _base(assertion_results=[{"dimension": "completeness", "pass": True}],
                  usage=[{"prompt_tokens": 100}])
        self.assertIsNone(r.total_cost)

    def test_cost_without_usage_none(self):
        r = _base(model_price={"input": 1, "output": 1})
        self.assertIsNone(r.total_cost)


class TestHelpers(unittest.TestCase):
    def test_enabled_dims_default_all(self):
        self.assertEqual(_enabled_dims(None), set(DEF_WEIGHTS))

    def test_enabled_dims_partial(self):
        self.assertEqual(_enabled_dims({"completeness": {"enabled": True},
                                        "factuality": {"enabled": False}}),
                         {"completeness"})

    def test_total_cost(self):
        # 单价单位 = 元/百万 token → 金额(元) = tokens × 单价 / 1e6
        usage = [{"prompt_tokens": 100, "completion_tokens": 50},
                 {"prompt_tokens": 200, "completion_tokens": 100}]
        self.assertAlmostEqual(_total_cost(usage, {"input": 1000, "output": 2000}), 0.6)

    def test_total_cost_cache_hit(self):
        # 7.4 cache 口径：命中部分按 cache_hit_price，未命中按 input
        usage = [{"prompt_tokens": 1000, "prompt_cache_hit_tokens": 600,
                  "completion_tokens": 400}]
        price = {"input": 1.5, "output": 4.5, "cache_hit": 0.1}
        # 600×0.1 + 400×1.5 + 400×4.5 = 2460 → 0.00246
        self.assertAlmostEqual(_total_cost(usage, price), 0.00246)

    def test_total_cost_cache_fallback(self):
        # price 未配 cache_hit → 命中部分回退 input（兼容既有数据零变化，与旧公式一致）
        usage = [{"prompt_tokens": 1000, "prompt_cache_hit_tokens": 600,
                  "completion_tokens": 400}]
        price = {"input": 1.5, "output": 4.5}
        self.assertAlmostEqual(_total_cost(usage, price), 0.0033)  # 1000×1.5+400×4.5

    def test_total_cost_cache_hit_gt_prompt_clamped(self):
        # 防异常数据：cache_hit > prompt 时按 prompt 封顶（不产生负成本）
        usage = [{"prompt_tokens": 100, "prompt_cache_hit_tokens": 999,
                  "completion_tokens": 50}]
        self.assertAlmostEqual(_total_cost(usage, {"input": 1000, "output": 2000, "cache_hit": 100}),
                               (100 * 100 + 50 * 2000) / 1e6)

    def test_resolve_targets_interface_override(self):
        targets = {(0, "completeness"): 60.0, (5, "completeness"): 80.0,
                   (0, "tool_usage"): 50.0, (5, "factuality"): 70.0}
        self.assertEqual(_resolve_targets(targets, 5),
                         {"completeness": 80.0, "factuality": 70.0, "tool_usage": 50.0})
        self.assertEqual(_resolve_targets(targets, 9),
                         {"completeness": 60.0, "tool_usage": 50.0})

    def test_resolve_weights_interface_override(self):
        # 与 _resolve_targets 同口径：接口级权重优先，缺省回退 interface_id=0 哨兵默认
        weights = {(0, "completeness"): 0.3, (0, "tool_usage"): 0.2,
                   (5, "completeness"): 0.9, (5, "factuality"): 0.7}
        self.assertEqual(_resolve_weights(weights, 5),
                         {"completeness": 0.9, "factuality": 0.7, "tool_usage": 0.2})
        self.assertEqual(_resolve_weights(weights, 9),
                         {"completeness": 0.3, "tool_usage": 0.2})
        # 空接口匹配：全回退默认
        self.assertEqual(_resolve_weights({(0, "completeness"): 0.3}, 5),
                         {"completeness": 0.3})

    def test_score_case_uses_interface_weights(self):
        # 接口 5 覆盖 completeness 0.9（默认 0.3）；tool_usage 用默认 0.2。
        # 若 _load_weights 仍硬编码 interface_id==0，此处会按 0.3 加权 → 80 分，断言 90.91 即证明接口级权重生效。
        weights = _resolve_weights(
            {(0, "completeness"): 0.3, (0, "tool_usage"): 0.2, (5, "completeness"): 0.9}, 5)
        r = _base(assertion_results=[
            {"dimension": "completeness", "pass": True},
            {"dimension": "completeness", "pass": True},   # completeness 100
            {"dimension": "tool_usage", "pass": True},
            {"dimension": "tool_usage", "pass": False},    # tool_usage 50
        ], weights=weights)
        # (100*0.9 + 50*0.2) / (0.9+0.2) = 110/1.1 = 90.91
        self.assertAlmostEqual(r.score_total, 90.91, places=2)

    def test_unified_uses_last_attempt(self):
        r = SimpleNamespace(answer="a", reasoning="r", tool_calls=[{"name": "t"}],
                            usage=[{"prompt_tokens": 1}, {"prompt_tokens": 2}])
        u = _unified(r)
        self.assertEqual(u["answer"], "a")
        self.assertEqual(u["usage"], {"prompt_tokens": 2})

    def test_aggregate_precomputed(self):
        run = SimpleNamespace(ttft_p50=None, ttft_p95=None, e2e_p50=None, e2e_p95=None,
                              total_tokens=None, total_cost=None)
        results = [
            SimpleNamespace(ttft_p50=100.0, ttft_p95=200.0, e2e_p50=500.0, e2e_p95=900.0,
                            total_tokens=100, total_cost=0.2),
            SimpleNamespace(ttft_p50=300.0, ttft_p95=None, e2e_p50=None, e2e_p95=None,
                            total_tokens=200, total_cost=None),
        ]
        _aggregate_precomputed(run, results)
        self.assertEqual(run.ttft_p50, 200.0)       # (100+300)/2
        self.assertEqual(run.ttft_p95, 200.0)       # 单值
        self.assertEqual(run.total_tokens, 300)
        self.assertEqual(run.total_cost, 0.2)

    def test_aggregate_precomputed_all_none(self):
        run = SimpleNamespace(ttft_p50=None, ttft_p95=None, e2e_p50=None, e2e_p95=None,
                              total_tokens=None, total_cost=None)
        _aggregate_precomputed(run, [SimpleNamespace(ttft_p50=None, ttft_p95=None,
                                                      e2e_p50=None, e2e_p95=None,
                                                      total_tokens=0, total_cost=None)])
        self.assertIsNone(run.ttft_p50)
        self.assertEqual(run.total_tokens, 0)


class TestRunLevelAggregation(unittest.TestCase):
    """7.7 run 级聚合边界：终态判定 / agent_score / judge_incomplete / 0-case 不除零。

    mock DB 跑生产聚合逻辑 _score_executed_results（score_case/run_assertions/
    _aggregate_precomputed 均真实纯函数），不建真实引擎。CaseVersion 用 mock snapshot 控制
    metrics/assertions：全 disabled → 全 NA；语义无 judge → N/A 权重触发 judge_incomplete。
    """

    def _run(self):
        return SimpleNamespace(id=1, status="scoring", agent_id=1, run_config={},
                               ttft_p50=None, ttft_p95=None, e2e_p50=None, e2e_p95=None,
                               total_tokens=None, total_cost=None,
                               pass_case=0, fail_case=0, na_case=0, error_case=0,
                               agent_score=None, judge_incomplete=False, finished_at=None)

    def _res(self, **kw):
        p = dict(pass_fail="pass", case_version_id=1, case_id=1,
                 ttft_p50=None, ttft_p95=None, e2e_p50=None, e2e_p95=None,
                 usage=None, model=None, answer="", reasoning=None, tool_calls=None,
                 total_tokens=0, total_cost=None,
                 assertion_results=None, judge_results=None,
                 score_total=None, score_per_dimension=None)
        p.update(kw)
        return SimpleNamespace(**p)

    def _score(self, results, snapshot, targets=None):
        """跑 _score_executed_results，返回 (run, 是否有评分异常)。

        run 级聚合是核心断言目标，run 由测试构造传入。
        weights 按 _load_weights 契约传 tuple-key（interface_id, dimension_code），
        与 targets 的 tuple-key 口径一致；评分按接口 0 解析即退化为全局默认。
        """
        db = AsyncMock()
        db.get.return_value = SimpleNamespace(snapshot=snapshot)
        db.commit.return_value = None
        run = self._run()
        weight_rows = {(0, d): w for d, w in DEF_WEIGHTS.items()}
        async def _go():
            ok = await _score_executed_results(db, run, results, weight_rows,
                                               targets or {}, {}, {})
            return ok
        ok = asyncio.run(_go())
        return run, ok

    def test_zero_cases_score(self):
        # 0 case：scored 空 → agent_score None，不除零；completed
        run, ok = self._score([], {})
        self.assertTrue(ok)
        self.assertEqual(run.status, "completed")
        self.assertIsNone(run.agent_score)
        self.assertFalse(run.judge_incomplete)

    def test_all_error_partial_failed(self):
        # 全 error：不评分（循环跳过）→ partial_failed、agent_score None
        err = self._res(pass_fail="error")
        run, ok = self._score([err, err], {})
        self.assertTrue(ok)
        self.assertEqual(run.status, "partial_failed")
        self.assertEqual(run.error_case, 2)
        self.assertIsNone(run.agent_score)

    def test_all_na_aggregate(self):
        # metrics 全 disabled → 无启用维度 → 全 NA；语义 N/A 权重 0 → judge_incomplete False
        disabled = {d: {"enabled": False} for d in DEF_WEIGHTS}
        run, ok = self._score([self._res(), self._res()], {"metrics": disabled})
        self.assertTrue(ok)
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.na_case, 2)
        self.assertIsNone(run.agent_score)
        self.assertFalse(run.judge_incomplete)

    def test_semantic_all_na_marks_incomplete(self):
        # 语义维度无 judge 全 N/A：N/A 权重 0.5/1.0 > 30% → judge_incomplete True
        run, ok = self._score([self._res(), self._res()], {})
        self.assertTrue(ok)
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.na_case, 2)
        self.assertIsNone(run.agent_score)
        self.assertTrue(run.judge_incomplete)

    def test_fail_only_completed(self):
        # fail-only（无 error）：completed；fail 也计分（0 分）→ agent_score 0.0
        snap = {
            "metrics": {"completeness": {"enabled": True}},
            "assertions": [{"dimension": "completeness", "op": "field_nonempty",
                            "args": {"path": "answer"}}],
        }
        f = self._res(answer="")   # answer 空 → field_nonempty 失败 → completeness 0
        run, ok = self._score([f, f], snap, targets={(0, "completeness"): 60.0})
        self.assertTrue(ok)
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.fail_case, 2)
        self.assertEqual(run.agent_score, 0.0)


if __name__ == "__main__":
    unittest.main()
