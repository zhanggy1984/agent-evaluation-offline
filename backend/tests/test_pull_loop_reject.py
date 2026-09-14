"""`runner/pull_loop` 驳回路径单测（批 B-补）。

**为什么写**：批 B 的真机复验造出四条驳回码后发现，四条 ack **全部 400**，且驳回后
`ack_status=pending` 的行**永远不被重放**。两次失败都在「全量单测 833 passed」之后才暴露
——本文件把这两处钉成回归护栏。

两处缺陷（均为真机实测，非读码推断）：

1. **reason 值与 online 值域不匹配**：offline 原发 `f"{code}: {detail}"[:200]`，而 online
   `ack_decide` 对 `reason` 做**严格相等**校验，值域是固定枚举 ⇒ 必 400。
   更深一层：该值会原样落进 `link.invalidate_reason`，而 R2 自愈例外判的是
   `== "offline_cap_gap"` 严格相等——**即便 online 放宽校验放行，长串也会把 R2 弄断**。
   → `test_ack_rejected_sends_bare_code` 与 `test_mapping_values_within_online_vocabulary`

2. **驳回 ack 永不重放**：`_process_envelope` 的幂等重放分支原只覆盖 `case_created`，
   `rejected + pending` 落到 `return "skipped"`（实测复跑 `fetched=4, skipped=4`、零 ack
   尝试）⇒ link 恒 `assembled`，每轮被重拉却永不收敛。
   → `test_rejected_pending_is_replayed`

3. **`reject_code` 落的是内部细码，而详设 §3.3 规定该列值域是 online 粗码**：真机复核
   （文档站点反查）时发现，非单测暴露。该列是 §5.9 requeue 重处理谓词与 §5.8
   `CAP_GAP_PROBE` 的筛选依据 ⇒ 存错即谓词失配（`content_gap` 匹配不上
   `rejected(online_content_gap)`）。修法 = 落库用映射后的粗码，内部细码前置进 `reject_detail`。
   → `TestRejectStoredColumns`

**online 值域为何硬编码**：跨仓，测试进程里 import 不到 online 的 `ack.REASON_CODES`。
硬编码的代价是 online 改枚举时本文件**不会自动红**——故 `_ONLINE_VOCAB` 处注明出处
（`online app/backflow/ack.py` 的 `REASON_CODES`），改 online 契约时须同步此处。
"""
import asyncio
import unittest
from unittest import mock

from app.runner import pull_loop as pl

# online `app/backflow/ack.py` 的 REASON_CODES 真值（真机读得，非文档转录）。
# online 侧新增/改名时**必须同步本常量**——这是本文件唯一的跨仓人工同步点。
_ONLINE_VOCAB = (
    "offline_cap_gap",
    "online_content_gap",
    "manual_invalidate",
)


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

    async def execute(self, *_a, **_kw):
        return _FakeResult(self._row)

    async def commit(self):
        return None


class _FakeScoped:
    """冒充 `SessionLocal`：`async with SessionLocal() as db` 每次给同一行。"""

    def __init__(self, row):
        self._row = row

    def __call__(self):
        return self

    async def __aenter__(self):
        return _FakeSession(self._row)

    async def __aexit__(self, *_exc):
        return False


class _InboxRow:
    """冒充 inbox 行（只用到 `_process_envelope` 幂等分支读的三个字段）。"""

    def __init__(self, status, ack_status, *, case_id=None, reject_code=None):
        self.status = status
        self.ack_status = ack_status
        self.case_id = case_id
        self.reject_code = reject_code


class TestRejectMapping(unittest.TestCase):
    def test_mapping_values_within_online_vocabulary(self) -> None:
        """映射后的码必须落在 online 值域内——否则真机必 400（实测过）。"""
        bad = {k: v for k, v in pl._ONLINE_REASON.items() if v not in _ONLINE_VOCAB}
        self.assertEqual(bad, {}, f"以下内部码映射到了 online 值域之外：{bad}")

    def test_every_internal_code_is_mapped(self) -> None:
        """四个内部码一个都不能漏——漏了 `_ack_rejected` 会在字典取值处 KeyError。"""
        internal = {
            pl.REJECT_CONTENT_GAP,
            pl.REJECT_VERSION_DRIFT,
            pl.REJECT_CAP_GAP,
            pl.REJECT_EMPTY_WORDS,
        }
        self.assertEqual(internal - set(pl._ONLINE_REASON), set())

    def test_cap_gap_maps_to_itself(self) -> None:
        """offline_cap_gap 必须**原样**：R2 自愈例外判的是该列严格等于此值。"""
        self.assertEqual(pl._ONLINE_REASON[pl.REJECT_CAP_GAP], "offline_cap_gap")

    def test_version_drift_folds_into_content_gap(self) -> None:
        """version_drift **折叠**进 online_content_gap（细粒度靠 reject_detail 承载）。

        设计依据（勿复活细分码）：详设 §5.5 verdict 语义明写 `content_gap` 与
        `version_drift` **同 reject_code**；register R-7（2026-09-11 重估）判该细分腿
        **作废**——同一版本对内 online 恒写常量 SCHEMA_VERSION，drift 无产出对象。
        ⚠️ 反面教训：真机上能「造出」drift 载荷（直接改 payload_json）**不等于**它会出现；
        本仓曾两次把「设计里有这条分支」当成「运行时走得到」。
        """
        self.assertEqual(
            pl._ONLINE_REASON[pl.REJECT_VERSION_DRIFT], "online_content_gap"
        )

    def test_three_internal_codes_fold_into_one_online_code(self) -> None:
        """content_gap / empty_words / version_drift 三码同归——改动时别只改一个。"""
        for code in (pl.REJECT_CONTENT_GAP, pl.REJECT_EMPTY_WORDS,
                     pl.REJECT_VERSION_DRIFT):
            self.assertEqual(pl._ONLINE_REASON[code], "online_content_gap", code)


class _FakeDb:
    """冒充 Session：只记 `add()` 的 ORM 行（断言落库字段）。"""

    def __init__(self):
        self.added: list = []

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        return None


class TestRejectAck(unittest.TestCase):
    def test_ack_rejected_sends_bare_code(self) -> None:
        """ack 只发纯码：入参原样透传，不得拼 `: detail`，也不得夹带 reject_detail。"""
        captured: dict = {}

        async def _fake_ack(payload_id, action, **kw):
            captured["payload_id"] = payload_id
            captured["action"] = action
            captured["reason"] = kw.get("reason")

        with mock.patch.object(pl.backflow_client, "ack", _fake_ack), \
                mock.patch.object(pl, "SessionLocal", _FakeScoped(None)):
            asyncio.run(pl._ack_rejected("p-1", "online_content_gap"))

        self.assertEqual(captured["action"], "invalidated")
        self.assertEqual(captured["reason"], "online_content_gap")
        self.assertNotIn(":", captured["reason"] or "")

    def test_ack_failure_leaves_pending_without_raising(self) -> None:
        """ack 失败不抛、不改状态——留给幂等分支下轮重放（抛出去会中断整批）。"""
        async def _boom(*_a, **_kw):
            raise pl.backflow_client.BackflowClientError("400")

        marked: list = []
        with mock.patch.object(pl.backflow_client, "ack", _boom), \
                mock.patch.object(pl, "_mark", lambda *a, **kw: marked.append(kw)), \
                mock.patch.object(pl, "SessionLocal", _FakeScoped(None)):
            asyncio.run(pl._ack_rejected("p-1", "offline_cap_gap"))
        self.assertEqual(marked, [])


class TestRejectStoredColumns(unittest.TestCase):
    """`_reject` 落库的两列语义（详设 §3.3 / §5.5）——**本批新钉的不变量**。

    背景：原实现把内部细码写进 `reject_code`（与详设列值域相反），真机复核时才发现。
    该列是 §5.9 requeue 重处理谓词（`rejected(online_content_gap)`）与 §5.8
    `CAP_GAP_PROBE`（`reject_code=='offline_cap_gap'`）的**筛选依据**，存错即谓词失配。
    """

    def _reject_and_capture(self, internal_code: str):
        db = _FakeDb()
        sent: list = []

        async def _fake_ack(payload_id, online_reason):
            sent.append((payload_id, online_reason))

        with mock.patch.object(pl, "_ack_rejected", _fake_ack):
            asyncio.run(pl._reject(db, {"schema_version": "1.0"}, "p-7",
                                   internal_code, "净化后词表为空"))
        return db.added[0], sent

    def test_reject_code_holds_online_code_not_internal(self) -> None:
        """`reject_code` 必须是 online 粗码（详设 §3.3 列值域三码）。"""
        row, _ = self._reject_and_capture(pl.REJECT_EMPTY_WORDS)
        self.assertEqual(row.reject_code, "online_content_gap")
        self.assertIn(row.reject_code, _ONLINE_VOCAB)

    def test_internal_code_survives_in_reject_detail(self) -> None:
        """内部细码不得丢——前置进 detail（§5.5「detail 注明」，与空词表区分）。"""
        row, _ = self._reject_and_capture(pl.REJECT_EMPTY_WORDS)
        self.assertTrue(row.reject_detail.startswith(f"{pl.REJECT_EMPTY_WORDS}: "),
                        row.reject_detail)
        self.assertIn("净化后词表为空", row.reject_detail)

    def test_reject_code_and_ack_reason_are_same_value(self) -> None:
        """落库列与出站 ack 必须同源——两者分叉会让 online 的 link 与本仓记的对不上。"""
        for internal in (pl.REJECT_CONTENT_GAP, pl.REJECT_VERSION_DRIFT,
                         pl.REJECT_EMPTY_WORDS, pl.REJECT_CAP_GAP):
            row, sent = self._reject_and_capture(internal)
            self.assertEqual(sent, [("p-7", row.reject_code)], internal)


class TestReplay(unittest.TestCase):
    def test_rejected_pending_is_replayed(self) -> None:
        """回归护栏：rejected + pending 必须触发重放（缺陷 5 原状是静默 skipped）。"""
        # reject_code 列存的是 **online 粗码**（见 TestRejectStoredColumns）⇒ 重放时原样透传
        row = _InboxRow("rejected", "pending", reject_code="online_content_gap")
        replayed: list = []

        async def _fake_replay(payload_id, code):
            replayed.append((payload_id, code))

        with mock.patch.object(pl, "SessionLocal", _FakeScoped(row)), \
                mock.patch.object(pl, "_ack_rejected", _fake_replay):
            outcome = asyncio.run(pl._process_envelope({"payload_id": "p-9"}))

        self.assertEqual(outcome, "skipped")
        self.assertEqual(replayed, [("p-9", "online_content_gap")])

    def test_rejected_acked_is_not_replayed(self) -> None:
        """已 ack 的驳回不得重发——幂等边界，防止每轮空打 ack。"""
        row = _InboxRow("rejected", "acked", reject_code="online_content_gap")
        replayed: list = []

        async def _fake_replay(payload_id, code):
            replayed.append((payload_id, code))

        with mock.patch.object(pl, "SessionLocal", _FakeScoped(row)), \
                mock.patch.object(pl, "_ack_rejected", _fake_replay):
            outcome = asyncio.run(pl._process_envelope({"payload_id": "p-9"}))

        self.assertEqual(outcome, "skipped")
        self.assertEqual(replayed, [])

    def test_case_created_pending_still_replayed(self) -> None:
        """原有激活重放路径不得被本次改动碰坏。"""
        row = _InboxRow("case_created", "pending", case_id=3618)
        replayed: list = []

        async def _fake_active(payload_id, case_id):
            replayed.append((payload_id, case_id))

        with mock.patch.object(pl, "SessionLocal", _FakeScoped(row)), \
                mock.patch.object(pl, "_ack_active", _fake_active):
            outcome = asyncio.run(pl._process_envelope({"payload_id": "p-9"}))

        self.assertEqual(outcome, "skipped")
        self.assertEqual(replayed, [("p-9", 3618)])


if __name__ == "__main__":
    unittest.main()
