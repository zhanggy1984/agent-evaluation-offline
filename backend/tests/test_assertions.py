"""3.1 断言算子单元测试（内置算子 + 注册表 + 执行入口）。

核心逻辑（CLAUDE.md）：算子判定分支与 run_assertions 汇总逻辑是核心，
用纯内存 unified 对象喂，不触网络/DB。自定义加载（load_class）测白名单与
importlib 实例化。
"""
import unittest

from app.assertions import run_assertions
from app.assertions.base import AssertionOpError
from app.assertions.path import MISSING, resolve, resolve_optional
from app.assertions.registry import get_op, list_ops, load_class, register
from app.assertions import ops  # noqa: F401  触发内置算子注册

# 模拟 ResultAssembler.to_unified 输出（contract-check 场景 + 检索场景）
UNIFIED = {
    "answer": "已检出 2 处违规：缺生效日期、缺付款条款",
    "reasoning": "按合同规则逐一比对",
    "tool_calls": [
        {"name": "contract_rules", "args": {}, "result": {
            "violations": ["缺生效日期", "缺付款条款"],
            "sources": [{"doc_id": "b1", "title": "b1_missing_date"}]}},
        {"name": "search", "args": {"q": "x"}, "result": {
            "sources": [{"doc_id": "x1", "score": 0.9}]}},
    ],
    "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    "meta": {"agent": "cc", "model": "deepseek"},
}


class TestResolve(unittest.TestCase):
    def test_dict_key(self):
        self.assertEqual(resolve(UNIFIED, "answer"), UNIFIED["answer"])

    def test_nested_path(self):
        self.assertEqual(resolve(UNIFIED, "usage.total_tokens"), 150)

    def test_list_index(self):
        self.assertEqual(resolve(UNIFIED, "tool_calls.0.name"), "contract_rules")

    def test_missing_raises(self):
        with self.assertRaises(KeyError):
            resolve(UNIFIED, "usage.nonexist")

    def test_optional_sentinel(self):
        self.assertIs(resolve_optional(UNIFIED, "usage.nonexist"), MISSING)
        self.assertEqual(resolve_optional(UNIFIED, "usage.total_tokens"), 150)


class TestFieldPresent(unittest.TestCase):
    def setUp(self):
        self.op = get_op("field_present")

    def test_present(self):
        ok, _ = self.op.run(UNIFIED, {"path": "usage"})
        self.assertTrue(ok)

    def test_missing_path_fails(self):
        ok, actual = self.op.run(UNIFIED, {"path": "usage.nonexist"})
        self.assertFalse(ok)
        self.assertIn("未取到", actual)

    def test_default_path_answer(self):
        ok, _ = self.op.run(UNIFIED, {})
        self.assertTrue(ok)


class TestFieldNonempty(unittest.TestCase):
    def setUp(self):
        self.op = get_op("field_nonempty")

    def test_nonempty_str(self):
        ok, _ = self.op.run(UNIFIED, {"path": "answer"})
        self.assertTrue(ok)

    def test_empty_str_fails(self):
        ok, _ = self.op.run({"answer": ""}, {"path": "answer"})
        self.assertFalse(ok)

    def test_none_fails(self):
        ok, _ = self.op.run({"answer": None}, {"path": "answer"})
        self.assertFalse(ok)

    def test_nonempty_list(self):
        ok, _ = self.op.run(UNIFIED, {"path": "tool_calls"})
        self.assertTrue(ok)

    def test_missing_path_fails(self):
        ok, actual = self.op.run(UNIFIED, {"path": "usage.nonexist"})
        self.assertFalse(ok)
        self.assertIn("未取到", actual)


class TestValueEquals(unittest.TestCase):
    def setUp(self):
        self.op = get_op("value_equals")

    def test_equal(self):
        ok, _ = self.op.run(UNIFIED, {"path": "usage.total_tokens", "expected": 150})
        self.assertTrue(ok)

    def test_not_equal(self):
        ok, _ = self.op.run(UNIFIED, {"path": "usage.total_tokens", "expected": 151})
        self.assertFalse(ok)

    def test_type_sensitive(self):
        ok, _ = self.op.run(UNIFIED, {"path": "usage.total_tokens", "expected": "150"})
        self.assertFalse(ok)

    def test_missing_expected_raises(self):
        with self.assertRaises(AssertionOpError):
            self.op.run(UNIFIED, {"path": "usage.total_tokens"})

    def test_missing_path_fails(self):
        ok, _ = self.op.run(UNIFIED, {"path": "usage.nonexist", "expected": 1})
        self.assertFalse(ok)


class TestValueRange(unittest.TestCase):
    def setUp(self):
        self.op = get_op("value_range")

    def test_within_range(self):
        ok, _ = self.op.run(UNIFIED, {"path": "usage.total_tokens", "min": 0, "max": 1000})
        self.assertTrue(ok)

    def test_over_max(self):
        ok, _ = self.op.run(UNIFIED, {"path": "usage.total_tokens", "max": 100})
        self.assertFalse(ok)

    def test_below_min(self):
        ok, _ = self.op.run(UNIFIED, {"path": "usage.total_tokens", "min": 200})
        self.assertFalse(ok)

    def test_min_only(self):
        ok, _ = self.op.run(UNIFIED, {"path": "usage.total_tokens", "min": 100})
        self.assertTrue(ok)

    def test_non_numeric_fails(self):
        ok, actual = self.op.run(UNIFIED, {"path": "answer", "min": 0, "max": 10})
        self.assertFalse(ok)
        self.assertIn("非数值", actual)

    def test_no_bounds_raises(self):
        with self.assertRaises(AssertionOpError):
            self.op.run(UNIFIED, {"path": "usage.total_tokens"})


class TestKeywordContains(unittest.TestCase):
    def setUp(self):
        self.op = get_op("keyword_contains")

    def test_all_keywords(self):
        ok, _ = self.op.run(UNIFIED, {"keywords": ["生效日期", "付款条款"]})
        self.assertTrue(ok)

    def test_all_missing_one(self):
        ok, _ = self.op.run(UNIFIED, {"keywords": ["生效日期", "不存在词"]})
        self.assertFalse(ok)

    def test_match_any(self):
        ok, _ = self.op.run(UNIFIED, {"keywords": ["生效日期", "不存在词"], "match": "any"})
        self.assertTrue(ok)

    def test_substring_match(self):
        ok, _ = self.op.run(UNIFIED, {"keywords": ["付款"]})
        self.assertTrue(ok)

    def test_custom_path(self):
        ok, _ = self.op.run(UNIFIED, {"path": "reasoning", "keywords": ["规则"]})
        self.assertTrue(ok)

    def test_non_text_fails(self):
        ok, actual = self.op.run(UNIFIED, {"path": "usage.total_tokens", "keywords": ["1"]})
        self.assertFalse(ok)
        self.assertIn("非文本", actual)

    def test_empty_keywords_raises(self):
        with self.assertRaises(AssertionOpError):
            self.op.run(UNIFIED, {"keywords": []})


class TestToolCalled(unittest.TestCase):
    def test_called(self):
        ok, actual = get_op("tool_called").run(UNIFIED, {"tool": "search"})
        self.assertTrue(ok)
        self.assertIn("search", actual)

    def test_not_present_fails(self):
        ok, _ = get_op("tool_called").run(UNIFIED, {"tool": "nonexistent"})
        self.assertFalse(ok)

    def test_no_tool_calls_fails(self):
        ok, _ = get_op("tool_called").run({"answer": "x"}, {"tool": "search"})
        self.assertFalse(ok)

    def test_missing_tool_raises(self):
        with self.assertRaises(AssertionOpError):
            get_op("tool_called").run(UNIFIED, {})


class TestToolNotCalled(unittest.TestCase):
    def test_not_called(self):
        ok, _ = get_op("tool_not_called").run(UNIFIED, {"tool": "delete_database"})
        self.assertTrue(ok)

    def test_called_fails(self):
        ok, _ = get_op("tool_not_called").run(UNIFIED, {"tool": "search"})
        self.assertFalse(ok)

    def test_missing_tool_raises(self):
        with self.assertRaises(AssertionOpError):
            get_op("tool_not_called").run(UNIFIED, {})


class TestSourceHit(unittest.TestCase):
    def test_hit_default_path(self):
        ok, _ = get_op("source_hit").run(UNIFIED, {})
        self.assertTrue(ok)  # result.sources 非空

    def test_hit_filtered_tool(self):
        ok, _ = get_op("source_hit").run(UNIFIED, {"tool": "search"})
        self.assertTrue(ok)

    def test_no_source_fails(self):
        unified = {"tool_calls": [{"name": "search", "result": {"sources": []}}]}
        ok, actual = get_op("source_hit").run(unified, {})
        self.assertFalse(ok)
        self.assertIn("无命中", actual)

    def test_missing_result_fails(self):
        unified = {"tool_calls": [{"name": "search", "result": {"hits": 1}}]}
        ok, _ = get_op("source_hit").run(unified, {})
        self.assertFalse(ok)

    def test_custom_path(self):
        ok, _ = get_op("source_hit").run(UNIFIED, {"tool": "contract_rules",
                                                   "path": "result.sources"})
        self.assertTrue(ok)


class TestListContains(unittest.TestCase):
    def test_subset(self):
        ok, _ = get_op("list_contains").run(
            UNIFIED, {"tool": "contract_rules", "path": "result.violations",
                      "expected": ["缺生效日期"]})
        self.assertTrue(ok)

    def test_not_subset(self):
        ok, _ = get_op("list_contains").run(
            UNIFIED, {"tool": "contract_rules", "path": "result.violations",
                      "expected": ["缺生效日期", "缺印章"]})
        self.assertFalse(ok)

    def test_single_item(self):
        ok, _ = get_op("list_contains").run(
            UNIFIED, {"tool": "contract_rules", "path": "result.violations",
                      "expected": "缺付款条款"})
        self.assertTrue(ok)

    def test_missing_list_fails(self):
        ok, actual = get_op("list_contains").run(
            UNIFIED, {"tool": "contract_rules", "path": "result.nonexist",
                      "expected": ["x"]})
        self.assertFalse(ok)
        self.assertIn("未取到", actual)

    def test_missing_expected_raises(self):
        with self.assertRaises(AssertionOpError):
            get_op("list_contains").run(UNIFIED, {"tool": "contract_rules"})


class TestListPrecisionRecall(unittest.TestCase):
    def test_full_match_f1_one(self):
        ok, actual = get_op("list_precision_recall").run(
            UNIFIED, {"tool": "contract_rules", "path": "result.violations",
                      "expected": ["缺生效日期", "缺付款条款"]})
        self.assertTrue(ok)
        self.assertEqual(actual["f1"], 1.0)
        self.assertEqual(len(actual["matched"]), 2)

    def test_partial_match_below_threshold(self):
        # 命中 2 / 期望 8 → rec=0.25, f1=0.4 < 0.5 默认阈值
        ok, actual = get_op("list_precision_recall").run(
            UNIFIED, {"tool": "contract_rules", "path": "result.violations",
                      "expected": ["缺生效日期", "缺付款条款", "缺印章", "缺页码",
                                   "缺甲方签章", "缺乙方签章", "缺金额大写", "缺争议条款"]})
        self.assertFalse(ok)
        self.assertLess(actual["f1"], 0.5)

    def test_threshold_tunable(self):
        ok, _ = get_op("list_precision_recall").run(
            UNIFIED, {"tool": "contract_rules", "path": "result.violations",
                      "expected": ["缺生效日期", "缺付款条款", "缺印章", "缺页码",
                                   "缺甲方签章", "缺乙方签章", "缺金额大写", "缺争议条款"],
                      "threshold": 0.1})
        self.assertTrue(ok)

    def test_empty_actual_fails(self):
        unified = {"tool_calls": [{"name": "contract_rules",
                                   "result": {"violations": []}}]}
        ok, actual = get_op("list_precision_recall").run(
            unified, {"tool": "contract_rules", "path": "result.violations",
                      "expected": ["缺生效日期"]})
        self.assertFalse(ok)
        self.assertEqual(actual["precision"], 0.0)
        self.assertEqual(actual["recall"], 0.0)

    def test_missing_list_fails(self):
        ok, actual = get_op("list_precision_recall").run(
            UNIFIED, {"tool": "contract_rules", "path": "result.nonexist",
                      "expected": ["x"]})
        self.assertFalse(ok)
        self.assertEqual(actual["f1"], 0.0)

    def test_missing_expected_raises(self):
        with self.assertRaises(AssertionOpError):
            get_op("list_precision_recall").run(
                UNIFIED, {"tool": "contract_rules", "path": "result.violations"})


class TestRegistry(unittest.TestCase):
    def test_all_builtins_registered(self):
        for op in ("field_present", "field_nonempty", "value_equals", "value_range",
                   "keyword_contains", "tool_called", "tool_not_called",
                   "source_hit", "list_contains", "list_precision_recall"):
            self.assertIn(op, list_ops())

    def test_unknown_op_raises(self):
        with self.assertRaises(AssertionOpError):
            get_op("no_such_op")

    def test_load_class_from_whitelist(self):
        op = load_class("assertions.ops.structure.FieldPresentOp")
        self.assertEqual(op.op, "field_present")

    def test_load_class_whitelist_rejects(self):
        with self.assertRaises(AssertionOpError):
            load_class("os.system")

    def test_register_duplicate_overwrites(self):
        class _Tmp(ops.structure.FieldPresentOp):
            pass
        register(_Tmp())
        self.assertIs(get_op("field_present"), get_op("field_present"))


class TestRunAssertions(unittest.TestCase):
    def test_multi_assertions_summary(self):
        defs = [
            {"dimension": "completeness", "op": "field_nonempty",
             "args": {"path": "answer"}, "source_detail": {"rule": "r1"}},
            {"dimension": "tool_usage", "op": "tool_called",
             "args": {"tool": "search"}},
            {"dimension": "completeness", "op": "keyword_contains",
             "args": {"keywords": ["不存在词"]}},
            {"dimension": "completeness", "op": "value_equals",
             "args": {"path": "usage.total_tokens", "expected": 150}},
        ]
        results = run_assertions(UNIFIED, defs)
        self.assertEqual(len(results), 4)
        self.assertEqual([r["pass"] for r in results], [True, True, False, True])
        # dimension / source_detail / expected 快照透传
        self.assertEqual(results[0]["dimension"], "completeness")
        self.assertEqual(results[0]["source_detail"], {"rule": "r1"})
        self.assertEqual(results[3]["expected"], 150)
        self.assertEqual(results[1]["actual"], ["contract_rules", "search"])

    def test_unknown_op_is_fail_not_raise(self):
        results = run_assertions(UNIFIED, [{"op": "no_such_op", "args": {}}])
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["pass"])
        self.assertIn("未注册", results[0]["actual"])

    def test_bad_args_is_fail(self):
        results = run_assertions(UNIFIED, [{"op": "value_equals", "args": {}}])
        self.assertFalse(results[0]["pass"])
        self.assertIn("定义错误", results[0]["actual"])

    def test_empty_defs(self):
        self.assertEqual(run_assertions(UNIFIED, []), [])


if __name__ == "__main__":
    unittest.main()
