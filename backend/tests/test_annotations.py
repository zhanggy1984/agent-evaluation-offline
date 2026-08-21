"""5.2 标注状态机核心逻辑单测（纯函数，不触 DB）。

_recalc_status 整体口径（每 POST 重算落库）：
  draft（无标注）→ single（有维度仅 1 人）→ double（所有已标维度均双人）→
  consensus（双人且 level 全部一致）/ disputed（任一维度两人 level 不一致）
"""
import unittest

from app.core.case_rules import recalc_annotation_status as _recalc_status
# 模型类 TestCase 命名为 CaseModel，避免 pytest 按 Test* 前缀收集
from app.models.case import CaseAnnotation, TestCase as CaseModel


def _case(annotation_status="draft"):
    return CaseModel(suite_id=1, interface_id=1, name="c", input_type="text",
                     annotation_status=annotation_status)


def _ann(annotator_id: int, dim: str, level: float | None = None, note: str | None = None):
    return CaseAnnotation(case_id=1, annotator_id=annotator_id,
                          dimension_code=dim, level=level, note=note)


class TestRecalcStatus(unittest.TestCase):
    def test_no_rows_draft(self):
        c = _case()
        _recalc_status(c, [])
        self.assertEqual(c.annotation_status, "draft")

    def test_single_annotator_single(self):
        c = _case()
        _recalc_status(c, [_ann(1, "factuality", 7.0)])
        self.assertEqual(c.annotation_status, "single")

    def test_two_same_level_consensus(self):
        c = _case()
        _recalc_status(c, [_ann(1, "factuality", 7.0), _ann(2, "factuality", 7.0)])
        self.assertEqual(c.annotation_status, "consensus")

    def test_two_conflict_disputed(self):
        c = _case()
        _recalc_status(c, [_ann(1, "factuality", 7.0), _ann(2, "factuality", 4.0)])
        self.assertEqual(c.annotation_status, "disputed")

    def test_two_levels_missing_double(self):
        # 双人但 level 未标全 → 双人未定级 → double（不到 consensus）
        c = _case()
        _recalc_status(c, [_ann(1, "factuality", 7.0), _ann(2, "factuality")])
        self.assertEqual(c.annotation_status, "double")

    def test_two_both_note_only_double(self):
        c = _case()
        _recalc_status(c, [_ann(1, "factuality", note="ok"), _ann(2, "factuality", note="ok")])
        self.assertEqual(c.annotation_status, "double")

    def test_multi_dim_one_single(self):
        # 一维度双人 + 另一维度仅 1 人 → 整体 single（未全部双人）
        c = _case()
        rows = [_ann(1, "factuality", 7.0), _ann(2, "factuality", 7.0),
                _ann(1, "reasoning_quality", 6.0)]
        _recalc_status(c, rows)
        self.assertEqual(c.annotation_status, "single")

    def test_multi_dim_conflict_wins(self):
        # 任一维度冲突 → disputed（即使其他维度双人一致）
        c = _case()
        rows = [_ann(1, "factuality", 7.0), _ann(2, "factuality", 4.0),
                _ann(1, "reasoning_quality", 6.0), _ann(2, "reasoning_quality", 6.0)]
        _recalc_status(c, rows)
        self.assertEqual(c.annotation_status, "disputed")

    def test_same_annotator_updates_no_double(self):
        # 同人同维度重复标 → UK 覆盖更新，annotator 去重后仍 1 人
        c = _case()
        _recalc_status(c, [_ann(1, "factuality", 5.0), _ann(1, "factuality", 6.0)])
        self.assertEqual(c.annotation_status, "single")

    def test_multi_dim_all_consensus(self):
        c = _case()
        rows = [_ann(1, "factuality", 7.0), _ann(2, "factuality", 7.0),
                _ann(1, "reasoning_quality", 6.0), _ann(2, "reasoning_quality", 6.0)]
        _recalc_status(c, rows)
        self.assertEqual(c.annotation_status, "consensus")

    def test_near_equal_levels_not_disputed(self):
        # Numeric(3,2) 精度：7.0 vs 7.00 视为一致
        c = _case()
        _recalc_status(c, [_ann(1, "factuality", 7.0), _ann(2, "factuality", 7.00)])
        self.assertEqual(c.annotation_status, "consensus")


if __name__ == "__main__":
    unittest.main()
