"""`runner/pull_loop` requeue 重处理路径单测（R-28）。

**为什么写**：§6.5 判据第一条明写「`status='rejected'`（ack_status **任意，含已 acked 的
invalidated 闭环**）→ 重处理」，而实现里 `_process_envelope` 对已存在的 payload_id 只做了
`ack_status == "pending"` 的重放，其余一律 `return "skipped"` —— **已 acked 的驳回行没有任何
重处理路径**。真机实证（2026-09-16）：offline `error_backflow_inbox` 行
`status=rejected + reject_code=online_content_gap + ack_status=acked`，online requeue 后该
payload 每 30s 被重拉、每轮 skipped，link 恒 `assembled`。

同批还补了一个连带缺口：`_activate`/`_reject` 原先直接 `db.add(ErrorBackflowInbox(...))`
**插新行**，重处理路径下必撞 `uk_inbox_payload` ⇒ 改走 `_inbox_put` 先查后写。

**反向对照**（不可省）：R-8 修订把 `offline_cap_gap` 收窄为仅 `ack_status∈{none,pending}`
复位，已 acked 的闭环行**必须仍跳过**——没有这条，「所有 rejected 都重处理」也能让正向断言通过。
"""
import unittest
from unittest import mock

from app.core import backflow_client
from app.runner import pull_loop as pl


class _Row:
    """冒充 inbox 行（覆盖重处理判据与复位用到的全部字段）。"""

    def __init__(self, status, ack_status, *, reject_code=None, case_id=None,
                 envelope_json=None, reject_detail=None):
        self.payload_id = "p-1"
        self.status = status
        self.ack_status = ack_status
        self.reject_code = reject_code
        self.case_id = case_id
        self.reject_detail = reject_detail
        self.envelope_json = envelope_json if envelope_json is not None else {}
        self.schema_version = "1.0"
        self.case_type = "regression_error"


def _envelope(assembled_ts="2026-09-16T02:30:00Z"):
    return {
        "payload_id": "p-1",
        "schema_version": "1.0",
        "case_type": "regression_error",
        "assembled_ts": assembled_ts,
        "no_fallback_config": {"words": ["兜底话术"]},
        "evidence": {"input": {}},
        "source": {"agent": "customer-service", "interface": "POST /x"},
    }


class TestNeedsReprocess(unittest.TestCase):
    """判据矩阵（纯函数，穷举）。"""

    def test_content_gap_acked_is_reprocessed(self) -> None:
        """核心：已 acked 的 online_content_gap 行**也要**重处理（§6.5 判据第一条）。"""
        self.assertTrue(pl._needs_reprocess(
            _Row("rejected", "acked", reject_code="online_content_gap")))

    def test_content_gap_any_ack_status(self) -> None:
        """ack_status 任意都重处理（none/pending 也就地覆盖为 none 重跑）。"""
        for ack in ("none", "pending", "acked", "blocked"):
            self.assertTrue(pl._needs_reprocess(
                _Row("rejected", ack, reject_code="online_content_gap")), ack)

    def test_cap_gap_acked_is_skipped(self) -> None:
        """**反向对照**（R-8 收窄）：已 acked 的 cap_gap 闭环行不复位、不重发 invalidated。"""
        self.assertFalse(pl._needs_reprocess(
            _Row("rejected", "acked", reject_code="offline_cap_gap")))

    def test_cap_gap_unclosed_is_reprocessed(self) -> None:
        """cap_gap 未闭环（首次驳回 / 对账未闭环）仍复位重处理。"""
        for ack in ("none", "pending"):
            self.assertTrue(pl._needs_reprocess(
                _Row("rejected", ack, reject_code="offline_cap_gap")), ack)

    def test_manual_invalidate_is_skipped(self) -> None:
        """人工判无效不重推（重推语义归 online admin）。"""
        self.assertFalse(pl._needs_reprocess(
            _Row("rejected", "acked", reject_code="manual_invalidate")))

    def test_non_rejected_rows_are_skipped(self) -> None:
        """**反向对照**：case_created（已 active 闭环）与 new（卡住行）都走跳过。"""
        self.assertFalse(pl._needs_reprocess(
            _Row("case_created", "acked", case_id=9)))
        self.assertFalse(pl._needs_reprocess(_Row("new", "none")))

    def test_rejected_without_code_is_skipped(self) -> None:
        """reject_code 为空（非法组合）不复位——宁可跳过也不猜。"""
        self.assertFalse(pl._needs_reprocess(_Row("rejected", "acked")))


class TestResetForReprocess(unittest.TestCase):
    def test_fields_reset_and_envelope_overwritten(self) -> None:
        """复位：清驳回痕迹 + ack 归 none + 覆盖留档信封（内容已刷新，审计链必须跟着走）。"""
        row = _Row("rejected", "acked", reject_code="online_content_gap",
                   case_id=123, reject_detail="content_gap: 空词表")
        new_env = _envelope("2026-09-16T03:00:00Z")
        pl._reset_for_reprocess(row, new_env)
        self.assertEqual(row.status, "new")
        self.assertEqual(row.ack_status, "none")
        self.assertIsNone(row.reject_code)
        self.assertIsNone(row.reject_detail)
        self.assertIsNone(row.case_id)
        self.assertEqual(row.envelope_json, new_env)
        self.assertEqual(row.envelope_json["assembled_ts"], "2026-09-16T03:00:00Z")


class _FakeScalars:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def scalars(self):
        return _FakeScalars(self._row)


class _FakeSession:
    def __init__(self, row):
        self._row = row
        self.added = []

    async def execute(self, *_a, **_kw):
        return _FakeResult(self._row)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        return None


class _FakeScoped:
    """冒充 `SessionLocal`：每次 `async with` 给同一行、同一 session。"""

    def __init__(self, row):
        self._row = row
        self.session = _FakeSession(row)

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_exc):
        return False


class TestProcessEnvelopeReprocess(unittest.TestCase):
    """端到端：重处理行**不再**落到 skipped，而是落回自检流程。"""

    def test_acked_content_gap_falls_through_to_selfcheck(self) -> None:
        row = _Row("rejected", "acked", reject_code="online_content_gap",
                   envelope_json={"assembled_ts": "2026-09-16T02:00:00Z"})
        scoped = _FakeScoped(row)
        # 自检判不过（最短路径）：证明流程**走到了自检**而不是在幂等分支被切掉。
        with mock.patch.object(pl, "SessionLocal", scoped), \
             mock.patch.object(pl, "validate_envelope",
                               return_value=(False, ["words 空"], "content_gap")), \
             mock.patch.object(pl.backflow_client, "ack",
                               side_effect=backflow_client.BackflowClientError("stub")):
            outcome = _run(pl._process_envelope(_envelope()))
        self.assertEqual(outcome, "rejected")
        self.assertEqual(row.status, "rejected")          # 复位后被再次驳回
        self.assertEqual(row.envelope_json["assembled_ts"], "2026-09-16T02:30:00Z")  # 留档已覆盖
        self.assertEqual(scoped.session.added, [])        # upsert 走更新，未插新行

    def test_case_created_still_skipped(self) -> None:
        """**反向对照**：已建单的行仍幂等跳过（否则每轮都会重复建 case）。"""
        row = _Row("case_created", "acked", case_id=9)
        scoped = _FakeScoped(row)
        with mock.patch.object(pl, "SessionLocal", scoped), \
             mock.patch.object(pl, "validate_envelope") as val:
            outcome = _run(pl._process_envelope(_envelope()))
        self.assertEqual(outcome, "skipped")
        val.assert_not_called()


class TestInboxPut(unittest.TestCase):
    """`_inbox_put` 的 upsert 语义（重处理的连带缺口）。"""

    def test_updates_existing_row_without_insert(self) -> None:
        row = _Row("rejected", "acked", reject_code="online_content_gap")
        db = _FakeSession(row)
        out = _run(pl._inbox_put(db, "p-1", status="case_created", ack_status="pending"))
        self.assertIs(out, row)
        self.assertEqual(row.status, "case_created")
        self.assertEqual(row.ack_status, "pending")
        self.assertEqual(db.added, [], "行已存在时不得再 add（会撞 uk_inbox_payload）")

    def test_inserts_when_absent(self) -> None:
        db = _FakeSession(None)
        out = _run(pl._inbox_put(db, "p-2", status="rejected", ack_status="pending"))
        self.assertEqual(len(db.added), 1)
        self.assertIs(db.added[0], out)
        self.assertEqual(out.payload_id, "p-2")
        self.assertEqual(out.status, "rejected")


def _run(coro):
    import asyncio
    return asyncio.run(coro)


if __name__ == "__main__":
    unittest.main()
