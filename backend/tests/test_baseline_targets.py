"""7.7 baseline_target 双签流转单元测试（agents.set_targets）。

mock DB 跑 set_targets 的五个流转分支：首次改 / 第二人 / 同人 / admin 例外 / evaluator 拒绝。
dashboard baseline 的 pending_approval 过滤由容器验证（verify_77 场景 8）覆盖。
"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.api.agents import set_targets
from app.core.errors import ApiError


class TestSetTargetsFlow(unittest.TestCase):
    """阈值双签状态机：首次改→pending_approval(by_1)；第二人→approved(by_2)；
    同人重复改保持 pending；已 approved 仅 admin 可再改（回到待第二人）。"""

    def _user(self, uid, role):
        return SimpleNamespace(id=uid, role=role, username=f"u{uid}")

    def _row(self, **kw):
        p = dict(dimension_code="completeness", target_score=70.0,
                 calibration_source="手动", approved_by_1=None, approved_by_2=None,
                 approval_status="auto")
        p.update(kw)
        return SimpleNamespace(**p)

    def _call(self, user, row):
        db = AsyncMock()
        db.get.return_value = SimpleNamespace(id=1, enabled=True)  # _get_agent

        class _Exec:
            def scalar_one_or_none(self):
                return row
        db.execute.return_value = _Exec()
        body = SimpleNamespace(target_scores={"completeness": 80.0})
        request = SimpleNamespace(headers={}, client=SimpleNamespace(host="10.0.0.1"))
        asyncio.run(set_targets(1, 2, body, request, user, db))
        return db

    def test_first_change_pending(self):
        # 无既有行：新建 + 记 approved_by_1 + pending_approval
        db = self._call(self._user(1, "admin"), None)
        self.assertTrue(db.add.called)
        # 首次 add 是 BaselineTarget（write_audit 随后 add AuditLog，故取 call_args_list[0]）
        added = db.add.call_args_list[0][0][0]
        self.assertEqual(added.approval_status, "pending_approval")
        self.assertEqual(added.approved_by_1, 1)
        self.assertIsNone(added.approved_by_2)

    def test_second_person_approves(self):
        # 第二人（evaluator）改：approved_by_2 落位 → approved
        row = self._row(approved_by_1=1)
        self._call(self._user(2, "evaluator"), row)
        self.assertEqual(row.approval_status, "approved")
        self.assertEqual(row.approved_by_2, 2)

    def test_same_person_keeps_pending(self):
        # 同人重复改：仍 pending_approval，不覆盖 by_2
        row = self._row(approved_by_1=1)
        self._call(self._user(1, "admin"), row)
        self.assertEqual(row.approval_status, "pending_approval")
        self.assertIsNone(row.approved_by_2)

    def test_evaluator_edit_approved_rejected(self):
        # 已双签通过，非 admin 改 → 400 拒
        row = self._row(approved_by_1=1, approved_by_2=2, approval_status="approved")
        with self.assertRaises(ApiError):
            self._call(self._user(2, "evaluator"), row)

    def test_admin_edit_approved_allowed(self):
        # 已双签通过，admin 可改（新值需再走一轮：回 pending 待第二人）
        row = self._row(approved_by_1=1, approved_by_2=2, approval_status="approved")
        self._call(self._user(1, "admin"), row)
        self.assertEqual(row.approval_status, "pending_approval")
        self.assertEqual(row.target_score, 80.0)


if __name__ == "__main__":
    unittest.main()
