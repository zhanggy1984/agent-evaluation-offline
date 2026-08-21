"""问题状态机 + 复现验证判定纯逻辑单测（零 DB 依赖，宿主直接跑）。

只 import app.core.issue_rules（与 test_dashboard 测 dashboard_rules 同模式）。
覆盖（CLAUDE.md 核心逻辑=业务分支）：
- can_transition：线性路径 / open→closed / 非法跳转 / closed 终态 / 未知状态
- judge_issue：整体 / 维度优先 / 回退整体 / error、na 跳过（6.2 验收口径）
- should_reopen：回归自动重开判定
- _resolve_targets：interface 级达标分优先、缺省回退 agent 默认（7.1 补充，
  从 issue_verify 平移至此，纯逻辑可宿主直测）
- 常量一致性：转移表键集 == ISSUE_STATUS
HTTP 行为（CRUD/权限/非法流转 400 / 复现验证编排）由容器 e2e 覆盖。
"""
import unittest

from app.core.issue_rules import (
    ALLOWED_TRANSITIONS, ISSUE_SEVERITY, ISSUE_STATUS, _resolve_targets,
    can_transition, judge_issue, should_reopen,
)


def _res(pf: str, dims: list[dict] | None = None) -> dict:
    return {"pass_fail": pf, "score_per_dimension": dims or []}


class TestCanTransition(unittest.TestCase):
    def test_linear_path(self):
        self.assertTrue(can_transition("open", "fixing"))
        self.assertTrue(can_transition("fixing", "fixed"))
        self.assertTrue(can_transition("fixed", "verified"))
        self.assertTrue(can_transition("verified", "closed"))

    def test_open_to_closed(self):
        """登记作废：open 可直接 closed。"""
        self.assertTrue(can_transition("open", "closed"))

    def test_illegal_jump(self):
        self.assertFalse(can_transition("open", "fixed"))
        self.assertFalse(can_transition("open", "verified"))
        self.assertFalse(can_transition("fixing", "closed"))
        self.assertFalse(can_transition("fixed", "fixing"))  # 回退不允许

    def test_closed_terminal(self):
        self.assertFalse(can_transition("closed", "open"))
        self.assertFalse(can_transition("closed", "verified"))

    def test_unknown_status(self):
        self.assertFalse(can_transition("bogus", "open"))
        self.assertFalse(can_transition("open", "bogus"))

    def test_transition_table_covers_all_statuses(self):
        """转移表键集必须与 ISSUE_STATUS 完全一致（防枚举漂移）。"""
        self.assertEqual(set(ALLOWED_TRANSITIONS), set(ISSUE_STATUS))

    def test_severity_enum(self):
        self.assertEqual(list(ISSUE_SEVERITY), ["low", "medium", "high", "critical"])


class TestJudgeIssue(unittest.TestCase):
    """复现验证判定（6.2）：维度优先 + 整体回退，error/na 跳过。"""

    def test_error_skipped(self):
        vr, reason = judge_issue(result=_res("error"), related_dimension=None,
                                 dim_target=None, issue_status="open")
        self.assertIsNone(vr)
        self.assertIn("错误", reason)

    def test_na_skipped(self):
        vr, reason = judge_issue(result=_res("na"), related_dimension=None,
                                 dim_target=None, issue_status="open")
        self.assertIsNone(vr)
        self.assertIn("N/A", reason)

    def test_no_result_skipped(self):
        vr, reason = judge_issue(result=None, related_dimension="completeness",
                                 dim_target=None, issue_status="open")
        self.assertIsNone(vr)
        self.assertIn("不在", reason)

    def test_overall_pass_fixed(self):
        vr, _ = judge_issue(result=_res("pass"), related_dimension=None,
                            dim_target=None, issue_status="fixing")
        self.assertEqual(vr, "fixed")

    def test_overall_pass_verified_status(self):
        """verified 态 + pass → verified（闭环确认，区别于 fixed）。"""
        vr, _ = judge_issue(result=_res("pass"), related_dimension=None,
                            dim_target=None, issue_status="verified")
        self.assertEqual(vr, "verified")

    def test_overall_fail_reproduced(self):
        vr, _ = judge_issue(result=_res("fail"), related_dimension=None,
                            dim_target=None, issue_status="open")
        self.assertEqual(vr, "reproduced")

    def test_dim_pass_ge_target(self):
        """维度优先：value >= 达标分 → fixed（即使整体 fail 也以维度为准）。"""
        dims = [{"code": "completeness", "value": 92.0, "na": False}]
        vr, _ = judge_issue(result=_res("fail", dims), related_dimension="completeness",
                            dim_target=90.0, issue_status="open")
        self.assertEqual(vr, "fixed")

    def test_dim_fail_lt_target(self):
        dims = [{"code": "completeness", "value": 80.0, "na": False}]
        vr, _ = judge_issue(result=_res("pass", dims), related_dimension="completeness",
                            dim_target=90.0, issue_status="open")
        self.assertEqual(vr, "reproduced")

    def test_dim_na_skipped(self):
        dims = [{"code": "completeness", "value": None, "na": True, "na_reason": "judge 超时"}]
        vr, reason = judge_issue(result=_res("pass", dims), related_dimension="completeness",
                                 dim_target=90.0, issue_status="open")
        self.assertIsNone(vr)
        self.assertIn("N/A", reason)

    def test_dim_no_target_falls_back_overall(self):
        """维度有值但无达标分 → 回退整体（挑战点 2）。"""
        dims = [{"code": "completeness", "value": 55.0, "na": False}]
        vr, _ = judge_issue(result=_res("pass", dims), related_dimension="completeness",
                            dim_target=None, issue_status="open")
        self.assertEqual(vr, "fixed")

    def test_dim_not_enabled_falls_back_overall(self):
        """run 未启用该维度（score_per_dimension 无此维）→ 回退整体。"""
        vr, _ = judge_issue(result=_res("fail", [{"code": "factuality", "value": 88.0, "na": False}]),
                            related_dimension="completeness", dim_target=90.0, issue_status="open")
        self.assertEqual(vr, "reproduced")


class TestShouldReopen(unittest.TestCase):
    """回归自动重开：reproduced ∧ {fixed, verified}。"""

    def test_fixed_reopens(self):
        self.assertTrue(should_reopen("fixed", "reproduced"))

    def test_verified_reopens(self):
        self.assertTrue(should_reopen("verified", "reproduced"))

    def test_open_not_reopen(self):
        self.assertFalse(should_reopen("open", "reproduced"))
        self.assertFalse(should_reopen("fixing", "reproduced"))

    def test_pass_not_reopen(self):
        self.assertFalse(should_reopen("verified", "fixed"))
        self.assertFalse(should_reopen("fixed", "verified"))

    def test_none_result_not_reopen(self):
        self.assertFalse(should_reopen("fixed", None))


class TestResolveTargets(unittest.TestCase):
    """达标分解析（7.1）：interface 级优先，缺省回退 agent 默认（interface_id=0 哨兵）。"""

    def test_interface_preferred_over_default(self):
        targets = {(1, "factuality"): 70.0, (0, "factuality"): 60.0, (0, "completeness"): 50.0}
        r = _resolve_targets(targets, interface_id=1)
        self.assertEqual(r["factuality"], 70.0)   # interface 级优先于默认
        self.assertEqual(r["completeness"], 50.0)  # 无接口级 → 默认回退

    def test_missing_interface_falls_back_to_default(self):
        targets = {(0, "factuality"): 60.0, (2, "factuality"): 80.0}
        r = _resolve_targets(targets, interface_id=3)
        self.assertEqual(r["factuality"], 60.0)  # interface 3 无配置 → agent 默认

    def test_default_not_used_when_interface_exists(self):
        targets = {(0, "factuality"): 60.0, (1, "factuality"): 80.0}
        r = _resolve_targets(targets, interface_id=1)
        self.assertEqual(r["factuality"], 80.0)  # 不回退默认

    def test_empty_targets(self):
        self.assertEqual(_resolve_targets({}, interface_id=1), {})

    def test_interface_zero_itself(self):
        # interface_id=0 请求 → 自身即默认
        targets = {(0, "factuality"): 60.0}
        self.assertEqual(_resolve_targets(targets, interface_id=0), {"factuality": 60.0})
