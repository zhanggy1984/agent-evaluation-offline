"""5.3 看板聚合核心逻辑单测（纯函数，不触 DB）。

只 import app.core.dashboard_rules（宿主无 aiomysql/aiosqlite 时可直接跑）。
覆盖（CLAUDE.md 核心逻辑=业务分支）：
- build_gate_cards：L0 门禁墙（版本反推/跨 suite 汇总/陈旧 suite 标注/无 run）
- significance：L2 2σ 显著性（数据不足/显著 up/down/flat）
- resolve_targets：baseline_target interface 级优先回退 agent 默认

HTTP 行为（viewer 403 evidence、gate/trend/compare 权限）由容器 e2e 覆盖（5.3e）。
"""
import unittest
from datetime import datetime

from app.core.dashboard_rules import (
    PF_ORDER, build_baseline, build_coverage, build_gate_cards, resolve_targets, significance,
)
from app.models.agent import Agent, AgentInterface
# 模型类 Test* 前缀会被 pytest 收集，起别名规避
from app.models.case import SceneCatalog, TestSuite as SuiteModel
from app.models.run import EvalResult as ResultModel, EvalRun as RunModel


def _dt(hour):
    return datetime(2026, 8, 1, hour)


def _agent(aid, name="agent"):
    return Agent(id=aid, name=name, enabled=True)


def _suite(sid, agent_id, name):
    return SuiteModel(id=sid, agent_id=agent_id, name=name)


def _run(rid, agent_id, suite_id, version, status, started_at, agent_score=None,
         total_case=0, pass_case=0, fail_case=0, error_case=0, finished_at=None,
         trigger_type="manual", run_config=None):
    return RunModel(id=rid, agent_id=agent_id, suite_id=suite_id, version=version,
                   status=status, started_at=started_at, finished_at=finished_at,
                   agent_score=agent_score, total_case=total_case, pass_case=pass_case,
                   fail_case=fail_case, error_case=error_case,
                   trigger_type=trigger_type, run_config=run_config)


class TestBuildGateCards(unittest.TestCase):
    def test_no_runs_untested_with_all_suites_stale(self):
        cards = build_gate_cards(
            [_agent(1)], [], [_suite(10, 1, "s1"), _suite(11, 1, "s2")], {10: 2, 11: 3})
        self.assertEqual(len(cards), 1)
        c = cards[0]
        self.assertIsNone(c["version"])
        self.assertIsNone(c["agent_score"])
        self.assertEqual(len(c["stale_suites"]), 2)

    def test_single_run_basic(self):
        run = _run(1, 1, 10, "1.2.0", "completed", _dt(8), agent_score=7.5,
                   total_case=3, pass_case=2, fail_case=1)
        cards = build_gate_cards([_agent(1)], [run], [_suite(10, 1, "s1")], {10: 3})
        c = cards[0]
        self.assertEqual(c["version"], "1.2.0")
        self.assertEqual(c["agent_score"], 7.5)
        self.assertAlmostEqual(c["pass_rate"], 2 / 3, places=4)
        self.assertEqual(c["stale_suites"], [])

    def test_latest_version_wins(self):
        """最近终态 run 反推当前版本：旧 version 不参与汇总。"""
        old = _run(1, 1, 10, "1.1.0", "completed", _dt(1), agent_score=6.0,
                   total_case=2, pass_case=1, fail_case=1)
        new = _run(2, 1, 10, "1.2.0", "completed", _dt(2), agent_score=8.0,
                   total_case=2, pass_case=2, fail_case=0)
        cards = build_gate_cards([_agent(1)], [old, new], [_suite(10, 1, "s1")], {10: 2})
        c = cards[0]
        self.assertEqual(c["version"], "1.2.0")
        self.assertEqual(c["agent_score"], 8.0)

    def test_cross_suite_summary_mean_score(self):
        """同一版本多 suite run：agent_score 取各 run 均值，case 计数求和。"""
        r1 = _run(1, 1, 10, "1.0.0", "completed", _dt(1), agent_score=7.0,
                  total_case=3, pass_case=2, fail_case=1)
        r2 = _run(2, 1, 11, "1.0.0", "completed", _dt(2), agent_score=9.0,
                  total_case=1, pass_case=1, fail_case=0)
        cards = build_gate_cards(
            [_agent(1)], [r1, r2], [_suite(10, 1, "s1"), _suite(11, 1, "s2")], {10: 3, 11: 1})
        c = cards[0]
        self.assertEqual(c["agent_score"], 8.0)   # (7+9)/2
        self.assertEqual(c["total_case"], 4)
        self.assertEqual(c["pass_case"], 3)

    def test_stale_suite_marked(self):
        """当前版本只跑了 suite1 → suite2 标陈旧。"""
        run = _run(1, 1, 10, "1.0.0", "completed", _dt(1), agent_score=7.0,
                   total_case=1, pass_case=1, fail_case=0)
        suites = [_suite(10, 1, "s1"), _suite(11, 1, "s2")]
        cards = build_gate_cards([_agent(1)], [run], suites, {10: 1, 11: 2})
        self.assertEqual([s["id"] for s in cards[0]["stale_suites"]], [11])

    def test_non_terminal_runs_ignored(self):
        """running/scoring 未终态 run 不参与门禁墙。"""
        running = _run(1, 1, 10, "1.1.0", "running", _dt(1), agent_score=7.0,
                       total_case=1, pass_case=1, fail_case=0)
        done = _run(2, 1, 10, "1.0.0", "completed", _dt(2), agent_score=8.0,
                    total_case=1, pass_case=1, fail_case=0)
        cards = build_gate_cards([_agent(1)], [running, done], [_suite(10, 1, "s1")], {10: 1})
        self.assertEqual(cards[0]["version"], "1.0.0")

    def test_no_score_runs_version_kept(self):
        """终态 run 无 agent_score（全 N/A）→ 版本仍反推、分数 None。"""
        run = _run(1, 1, 10, "1.0.0", "completed", _dt(1), agent_score=None,
                   total_case=2, pass_case=0, fail_case=0)
        cards = build_gate_cards([_agent(1)], [run], [_suite(10, 1, "s1")], {10: 2})
        self.assertEqual(cards[0]["version"], "1.0.0")
        self.assertIsNone(cards[0]["agent_score"])


class TestSignificance(unittest.TestCase):
    def test_insufficient_lt3_runs(self):
        sigma, sig = significance([6.0, 7.0], 6.0, 8.0)
        self.assertIsNone(sigma)
        self.assertEqual(sig, "insufficient")

    def test_up_over_2sigma(self):
        sigma, sig = significance([6.0, 7.0, 8.0], 6.0, 8.1)
        self.assertIsNotNone(sigma)
        self.assertEqual(sig, "up")

    def test_down_over_2sigma(self):
        sigma, sig = significance([6.0, 7.0, 8.0], 8.0, 5.9)
        self.assertEqual(sig, "down")

    def test_flat_within_2sigma(self):
        sigma, sig = significance([6.0, 7.0, 8.0], 7.0, 7.5)
        self.assertEqual(sig, "flat")

    def test_zero_sigma_flat(self):
        """历史无波动 → 视为无显著变化（不误报）。"""
        sigma, sig = significance([7.0, 7.0, 7.0], 6.0, 8.0)
        self.assertEqual(sigma, 0.0)
        self.assertEqual(sig, "flat")

    def test_missing_side_insufficient(self):
        sigma, sig = significance([6.0, 7.0, 8.0], None, 8.0)
        self.assertEqual(sig, "insufficient")


class TestResolveTargets(unittest.TestCase):
    def test_interface_preferred(self):
        targets = {(0, "completeness"): 6.0, (0, "factuality"): 6.5, (3, "completeness"): 8.0}
        resolved = resolve_targets(targets, 3)
        self.assertEqual(resolved["completeness"], 8.0)
        self.assertEqual(resolved["factuality"], 6.5)  # 缺省回退 agent 默认

    def test_fallback_agent_default(self):
        targets = {(0, "completeness"): 6.0}
        resolved = resolve_targets(targets, 99)
        self.assertEqual(resolved["completeness"], 6.0)

    def test_default_does_not_override_interface(self):
        targets = {(0, "completeness"): 6.0, (3, "completeness"): 8.0}
        resolved = resolve_targets(targets, 3)
        self.assertEqual(resolved["completeness"], 8.0)


class TestPFOrder(unittest.TestCase):
    def test_error_fail_front(self):
        self.assertTrue(PF_ORDER["error"] < PF_ORDER["fail"] < PF_ORDER["na"] < PF_ORDER["pass"])


class TestBuildCoverage(unittest.TestCase):
    def _iface(self, iid, name):
        return AgentInterface(id=iid, agent_id=1, name=name, path=f"/{name}", method="GET", enabled=True)

    def _scene(self, tag, desc=""):
        return SceneCatalog(id=1, agent_id=1, scene_tag=tag, description=desc)

    def test_all_covered_no_blank(self):
        out = build_coverage([self._iface(1, "a"), self._iface(2, "b")], {1, 2},
                             [self._scene("s1"), self._scene("s2")], {"s1", "s2"})
        self.assertEqual(out["interface_rate"], 1.0)
        self.assertEqual(out["interface_blank"], [])
        self.assertEqual(out["scene_rate"], 1.0)
        self.assertEqual(out["scene_blank"], [])

    def test_partial_with_blank(self):
        ifaces = [self._iface(1, "a"), self._iface(2, "b"), self._iface(3, "c")]
        scenes = [self._scene("s1", "一问"), self._scene("s2", "二答"), self._scene("s3")]
        out = build_coverage(ifaces, {1}, scenes, {"s1"})
        self.assertEqual(out["interface_total"], 3)
        self.assertEqual(out["interface_covered"], 1)
        self.assertAlmostEqual(out["interface_rate"], 1 / 3, places=4)
        self.assertEqual([b["id"] for b in out["interface_blank"]], [2, 3])
        self.assertEqual([b["tag"] for b in out["scene_blank"]], ["s2", "s3"])
        self.assertEqual(out["scene_blank"][0]["description"], "二答")  # 已覆盖的 s1 不进盲区

    def test_empty_no_rate(self):
        out = build_coverage([], set(), [], set())
        self.assertIsNone(out["interface_rate"])
        self.assertIsNone(out["scene_rate"])
        self.assertEqual(out["interface_blank"], [])
        self.assertEqual(out["scene_blank"], [])


class TestBuildBaseline(unittest.TestCase):
    """6.3 基线对比：接口分组 / target 回退 / 缺口与达标判定 / N-A 剔除。"""

    def _iface(self, iid, name):
        return AgentInterface(id=iid, agent_id=1, name=name, path=f"/{name}", method="GET",
                              enabled=True)

    def _result(self, cid, dims):
        """dims: {code: value|None}，None 视为该维度 na。"""
        return ResultModel(id=0, run_id=1, case_id=cid, case_version_id=0, pass_fail="pass",
                           score_per_dimension=[
                               {"code": c, "value": v, "na": v is None} for c, v in dims.items()
                           ])

    def test_grouped_by_interface_mean(self):
        results = [
            self._result(101, {"completeness": 78, "factuality": 65}),
            self._result(102, {"completeness": 82, "factuality": 75}),
            self._result(201, {"factuality": 88}),
        ]
        case_interface = {101: 1, 102: 1, 201: 2}
        out = build_baseline(results, case_interface, {}, [self._iface(1, "a"), self._iface(2, "b")])
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["interface_id"], 1)
        self.assertEqual(out[0]["case_count"], 2)
        d1 = {d["code"]: d for d in out[0]["dims"]}
        self.assertAlmostEqual(d1["completeness"]["score"], 80.0, places=2)  # (78+82)/2
        self.assertAlmostEqual(d1["factuality"]["score"], 70.0, places=2)
        d2 = {d["code"]: d for d in out[1]["dims"]}
        self.assertAlmostEqual(d2["factuality"]["score"], 88.0, places=2)

    def test_interface_target_preferred_fallback_agent(self):
        results = [self._result(101, {"completeness": 78})]
        targets = {(0, "completeness"): 70, (1, "completeness"): 75}
        out = build_baseline(results, {101: 1}, targets, [self._iface(1, "a")])
        d = out[0]["dims"][0]
        self.assertEqual(d["target"], 75.0)
        self.assertTrue(d["met"])
        self.assertAlmostEqual(d["gap"], 3.0, places=2)  # 78-75

    def test_fallback_agent_default(self):
        # #2 档位化：68 与 70 同档（3 档）→ met=True（消除「差 2 分生死不同档」悬崖）
        results = [self._result(101, {"factuality": 68})]
        out = build_baseline(results, {101: 1}, {(0, "factuality"): 70}, [self._iface(1, "a")])
        dims = {d["code"]: d for d in out[0]["dims"]}
        d = dims["factuality"]
        self.assertEqual(d["target"], 70.0)
        self.assertTrue(d["met"])
        self.assertAlmostEqual(d["gap"], -2.0, places=2)  # gap 保留连续差（展示口径不变）

    def test_semantic_below_floor_not_met(self):
        # 跨档仍不达标：59（2 档）对 70（3 档）→ met=False（与门禁 _gate_met 同源）
        results = [self._result(101, {"factuality": 59})]
        out = build_baseline(results, {101: 1}, {(0, "factuality"): 70}, [self._iface(1, "a")])
        d = {x["code"]: x for x in out[0]["dims"]}["factuality"]
        self.assertFalse(d["met"])
        self.assertAlmostEqual(d["gap"], -11.0, places=2)

    def test_na_and_none_excluded(self):
        """na 维度不计均值；无有效值 → score None（met/gap 无判定）。"""
        results = [
            self._result(101, {"completeness": 90, "factuality": None}),
            self._result(102, {"completeness": 70, "factuality": None}),
        ]
        out = build_baseline(results, {101: 1, 102: 1},
                             {(0, "completeness"): 80, (0, "factuality"): 75},
                             [self._iface(1, "a")])
        dims = {d["code"]: d for d in out[0]["dims"]}
        self.assertAlmostEqual(dims["completeness"]["score"], 80.0, places=2)
        self.assertTrue(dims["completeness"]["met"])
        self.assertIsNone(dims["factuality"]["score"])
        self.assertEqual(dims["factuality"]["target"], 75.0)
        self.assertIsNone(dims["factuality"]["met"])

    def test_no_target_gap_none(self):
        results = [self._result(101, {"completeness": 60})]
        out = build_baseline(results, {101: 1}, {}, [self._iface(1, "a")])
        d = out[0]["dims"][0]
        self.assertIsNone(d["target"])
        self.assertIsNone(d["gap"])
        self.assertIsNone(d["met"])

    def test_interface_without_results_omitted(self):
        results = [self._result(101, {"completeness": 60})]
        out = build_baseline(results, {101: 1}, {},
                             [self._iface(1, "a"), self._iface(2, "b")])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["interface_id"], 1)

    def test_case_without_interface_skipped(self):
        """case 无 interface 关联 → 不计入任何接口（异常数据隔离）。"""
        results = [self._result(999, {"completeness": 60})]
        out = build_baseline(results, {}, {}, [self._iface(1, "a")])
        self.assertEqual(out, [])

