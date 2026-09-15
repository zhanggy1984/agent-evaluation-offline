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

    # ---------- #235：大小写不敏感（2026-09-14 拍板，翻案） ----------

    def test_case_insensitive_both_directions(self):
        """词表给大写、文本给小写（及反向）都要命中——改前这是 False。

        本例是 #235 的**判别力来源**：旧的 `k in val` 大小写敏感实现跑它会红。
        """
        self.assertTrue(self.op.run({"answer": "the FALLBACK was triggered"},
                                    {"keywords": ["fallback"]})[0])
        self.assertTrue(self.op.run({"answer": "the fallback was triggered"},
                                    {"keywords": ["FALLBACK"]})[0])

    def test_case_folding_does_not_make_everything_match(self):
        """反例：折叠不得把**不相关**的词也放过（防「一 fold 就恒真」这类做反法）。"""
        self.assertFalse(self.op.run({"answer": "the fallback was triggered"},
                                     {"keywords": ["fallback", "完全不存在的词"]})[0])

    def test_non_str_keyword_raises(self):
        """元素非 str 抛 AssertionOpError（不是 TypeError/AttributeError）。

        折叠加了 `.lower()` 会把这条例外从 TypeError 变成 AttributeError，
        报错更差，故显式守卫——本条把它钉死。
        """
        with self.assertRaises(AssertionOpError):
            self.op.run(UNIFIED, {"keywords": ["生效日期", 123]})


class TestKeywordNotContains(unittest.TestCase):
    """keyword_not_contains：防御性金丝雀断言——answer 残留工具调用声明（DSML/XML 泄漏）即 fail。

    通用负向文本算子（客观公平）：任何 agent 的 answer 含任一泄漏标记都 fail，
    不偏袒单一 agent；gq 3161 根因（DeepSeek V4 DSML 声明泄漏进 answer）用其作防御哨兵。
    """

    def setUp(self):
        self.op = get_op("keyword_not_contains")

    def test_clean_answer_passes(self):
        ok, _ = self.op.run(UNIFIED, {"keywords": ["DSML", "tool_calls"]})
        self.assertTrue(ok)

    def test_blank_answer_fails(self):
        """R-12：空/纯空白应答 = leakage 空话术复发证据 ⇒ FAIL（**不是 na**）。

        空串天然不含任何 keyword，改动前会被 `not hits` 判成 PASS ⇒ 该抓的没抓到。
        纯空白必须单列：只写 `== ""` 的实现会在纯空白形态上漏判。
        """
        for blank in ("", "   ", "   \n\t "):
            with self.subTest(blank=repr(blank)):
                ok, actual = self.op.run({"answer": blank}, {"keywords": ["DSML", "tool_calls"]})
                self.assertFalse(ok)
                self.assertIn("空/纯空白", str(actual))

    def test_nonempty_answer_without_keyword_still_passes(self):
        """防误伤正面判据：非空且不命中 ⇒ 仍 PASS（否则「一刀切 return False」也会全绿）。"""
        ok, _ = self.op.run(
            {"answer": "根据制度，年休假按连续工作年限划分。"},
            {"keywords": ["DSML", "tool_calls"]})
        self.assertTrue(ok)

    def test_dsml_markup_fails(self):
        ok, _ = self.op.run(
            {"answer": "我再查一下<DSML><DSML>tool_calls"}, {"keywords": ["DSML", "tool_calls"]})
        self.assertFalse(ok)

    def test_standard_xml_markup_fails(self):
        ok, _ = self.op.run(
            {"answer": "<tool_calls><invoke name=\"hybrid_retrieve\">..</tool_calls>"},
            {"keywords": ["DSML", "tool_calls"]})
        self.assertFalse(ok)

    def test_partial_markup_fails(self):
        # 残留开标签/孤立 DSML 标记任一出现即 fail（改动 1 拦截失败的极端兜底）
        ok, _ = self.op.run({"answer": "未闭合<DSML>tool_calls"}, {"keywords": ["DSML", "tool_calls"]})
        self.assertFalse(ok)

    def test_match_any_semantics(self):
        # match="any"：仅当全部关键词都出现才 fail（至少一个不出现即通过）
        ok, _ = self.op.run({"answer": "含DSML标记但无工具调用声明"},
                            {"keywords": ["DSML", "tool_calls"], "match": "any"})
        self.assertTrue(ok)
        ok2, _ = self.op.run({"answer": "含DSML标记且残留tool_calls声明"},
                             {"keywords": ["DSML", "tool_calls"], "match": "any"})
        self.assertFalse(ok2)

    def test_custom_path(self):
        ok, _ = self.op.run(UNIFIED, {"path": "reasoning", "keywords": ["DSML"]})
        self.assertTrue(ok)

    def test_non_text_fails(self):
        ok, actual = self.op.run(UNIFIED, {"path": "usage.total_tokens", "keywords": ["1"]})
        self.assertFalse(ok)
        self.assertIn("非文本", actual)

    def test_empty_keywords_raises(self):
        with self.assertRaises(AssertionOpError):
            self.op.run(UNIFIED, {"keywords": []})

    # ---------- #235：大小写不敏感（本算子是**漏判**的那一侧） ----------

    def test_case_insensitive_catches_capitalized_leak(self):
        """词表折叠成小写后，仍须拦住**大写开头**的泄漏标记——改前这里是漏判。

        成因：`sanitize_words` 把词表折成 `dsml`，而 `k in val` 大小写敏感 ⇒
        `"<DSML>"` 匹配不上 ⇒ 金丝雀 **静默放行**。这正是 #235 要修的主缺陷
        （方向 = 假绿，最危险的一侧）。
        """
        ok, _ = self.op.run({"answer": "残留 <DSML> 声明"}, {"keywords": ["dsml"]})
        self.assertFalse(ok, "折小写的词表竟拦不住大写泄漏 —— 漏判复发")
        ok2, _ = self.op.run({"answer": "残留 <dsml> 声明"}, {"keywords": ["DSML"]})
        self.assertFalse(ok2)

    def test_case_insensitive_does_not_false_alarm(self):
        """反例：折叠不得把干净文本判成泄漏（否则是假红，方向虽安全但同样错）。"""
        ok, _ = self.op.run({"answer": "一切正常，无任何标记"}, {"keywords": ["DSML"]})
        self.assertTrue(ok)

    def test_non_str_keyword_raises(self):
        with self.assertRaises(AssertionOpError):
            self.op.run(UNIFIED, {"keywords": ["DSML", None]})


class TestKeywordOpsShareFolding(unittest.TestCase):
    """**结构性护栏的判别力检查**（#235）：两个算子必须对同一输入给出一致的大小写口径。

    实现上二者共用 `_keyword_hits`（见 `assertions/ops/text.py`），故「只改一侧」本应
    不可能发生；本例把这个结构约束**钉在测试面**——若日后有人把某个算子改回手写
    `k in val`，这里会红。这正是本仓「两侧必须同批动」的判据。
    """

    def test_both_ops_agree_on_folded_case(self):
        val = {"answer": "触发 Fallback 兜底"}
        contains_hit = get_op("keyword_contains").run(val, {"keywords": ["FALLBACK"]})[0]
        not_contains_hit = get_op("keyword_not_contains").run(val, {"keywords": ["FALLBACK"]})[0]
        # 同一文本、同一关键词：前者判定「包含」= True，后者判定「不包含」= False
        self.assertTrue(contains_hit, "keyword_contains 未折叠大小写")
        self.assertFalse(not_contains_hit, "keyword_not_contains 未折叠大小写（两算子口径脱节）")


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
