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
import os
import tempfile
import unittest
from unittest import mock

from app.adapters import base as base_mod
from app.core import probe as probe_mod
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
        """**全部**内部码一个都不能漏——漏了 `_ack_rejected` 会在字典取值处 KeyError。

        ⚠️ 本清单是**枚举**，新增内部码时必须同步加进来：不加的话新码无人钉，
        而本测试照样绿（负向断言的经典失效面）。
        """
        internal = {
            pl.REJECT_CONTENT_GAP,
            pl.REJECT_VERSION_DRIFT,
            pl.REJECT_CAP_GAP,
            pl.REJECT_EMPTY_WORDS,
            pl.REJECT_MISSING_SAMPLE,
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
    """冒充 Session：记 `add()` 的 ORM 行（断言落库字段）。

    `execute` 返回 None 行：`_reject` 现走 `_inbox_put` 的「先查后写」，查无 → 落 add 分支，
    断言面（`added[0]`）与本类建立时一致。
    """

    def __init__(self):
        self.added: list = []

    async def execute(self, *_a, **_kw):
        return _FakeResult(None)

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
                         pl.REJECT_EMPTY_WORDS, pl.REJECT_CAP_GAP,
                         pl.REJECT_MISSING_SAMPLE):
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

    def test_content_gap_acked_is_reprocessed_not_skipped(self) -> None:
        """`online_content_gap` 的已 ack 行必须**重处理**，不是幂等跳过（R-28 订正）。

        本条原名 `test_rejected_acked_is_not_replayed`，钉的是「已 acked 一律跳过」。
        该语义已被 §6.5 判据的 R-8 修订（`error-backflow-phase1.md:358`）取代：判据为
        「仅 `ack_status∈{none,pending}` **或收到 requeue/内容刷新**时复位重处理」，并明写
        原 bullet 的「含已 acked 的 invalidated 闭环」是「v0.2.1 旧语义，勿按旧语义实现」。
        §6.5 第 355 行对 `online_content_gap` 的重处理触发条件**不提 ack_status**。
        真机实证（2026-09-16）：旧语义下 requeue 后的 payload 每轮被重拉、每轮 skipped，
        link 恒 assembled——「现场已修正」永远推不进来。

        注意本条的 `replayed` 非空是**重处理后再驳回**的产物，不是「空打 ack」：下一轮
        该行的 ack_status 已复位，且 online 对同目标 ack 走幂等 noop。
        """
        row = _InboxRow("rejected", "acked", reject_code="online_content_gap")
        replayed: list = []

        async def _fake_replay(payload_id, code):
            replayed.append((payload_id, code))

        with mock.patch.object(pl, "SessionLocal", _FakeScoped(row)), \
                mock.patch.object(pl, "_ack_rejected", _fake_replay):
            outcome = asyncio.run(pl._process_envelope({"payload_id": "p-9"}))

        self.assertEqual(outcome, "rejected")  # 落回自检（信封缺字段 → 再驳回），非 skipped
        self.assertEqual(replayed, [("p-9", "online_content_gap")])

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


class _FakeSessionFull:
    """本类专用：比 `_FakeSession` 多一个 `expunge`（激活路径 commit 后会调）。"""

    async def execute(self, *_a, **_kw):
        return _FakeResult(None)

    async def commit(self):
        return None

    def expunge(self, _obj):
        return None


class _FakeScopedFull:
    def __call__(self):
        return self

    async def __aenter__(self):
        return _FakeSessionFull()

    async def __aexit__(self, *_exc):
        return False


class TestInputWiringGate(unittest.TestCase):
    """R-27 装载闸：`evidence.input` 形状与 agent adapter 模板不自洽 ⇒ 驳回，**不建单**。

    背景（2026-09-15 真机实证）：渲染侧对不可达占位**静默原样保留**（`engine._sub`），
    被测 agent 收到的是字面量 `{case.input.content}`，它回兜底话术 ⇒ `keyword_not_contains`
    恒 pass ⇒ 假绿经 ⑤环写入 online。回归用例的输入不进请求时，判定与该输入无关。
    故与 `REJECT_EMPTY_WORDS` 同型论证 fail-closed。

    **模板取真实 agent 的实测形态**（真库读得，非构造）：
      good-question / customer-service → `request.body` 的 `{case.input.content}`
      smart-procurement                → `request.body` 的 `{case.input.question}`
      contract-check                   → **`request` 无 body**，占位只在 prepare 的 upload
                                         步骤 `{"files": {"file": "{case.input.file_path}"}}`
    """

    # ---- 纯函数层：判据与渲染同源（同一枚 _VAR 与同一个 _get_path） ----

    def test_content_template_accepts_matching_object(self) -> None:
        cfg = {"request": {"body": {"stream": True, "content": "{case.input.content}"}}}
        self.assertEqual(pl.check_input_wiring(cfg, {"content": "你好"}), [])

    def test_question_template_rejects_content_key(self) -> None:
        """smart-procurement 形态：字段名是 `question`，硬编码 `.content` 对它**必然**失败。"""
        cfg = {"request": {"body": {"question": "{case.input.question}"}}}
        bad = pl.check_input_wiring(cfg, {"content": "你好"})
        self.assertEqual(len(bad), 1)
        self.assertIn("case.input.question", bad[0])

    def test_bare_string_input_is_rejected(self) -> None:
        """采集侧**显式支持** str input（online `consumer/state.py` 只判 `is not None`）
        ⇒ 裸字符串是合法采集形态，不是不可能事件。"""
        cfg = {"request": {"body": {"content": "{case.input.content}"}}}
        self.assertTrue(pl.check_input_wiring(cfg, "你好"))

    def test_prepare_step_placeholders_are_scanned(self) -> None:
        """**不能只扫 `request.body`**：contract-check 的占位只在 prepare 的 upload 步骤里。

        只扫 request.body 会把 contract-check 误判成「0 条路径」而驳回一个合法配置。
        """
        cfg = {
            "request": {"path": "/api/tasks/{prepare.upload.task_id}/result", "method": "GET"},
            "prepare": [{"name": "upload", "files": {"file": "{case.input.file_path}"}}],
        }
        self.assertEqual(pl.check_input_wiring(cfg, {"file_path": "/uploads/a.pdf"}), [])
        self.assertTrue(pl.check_input_wiring(cfg, {"content": "你好"}))

    def test_no_input_placeholder_at_all_is_rejected(self) -> None:
        """0 条路径**也**驳回：输入压根不进请求 ⇒ 判定与输入无关（fail-closed）。"""
        for cfg in ({}, None, {"request": {"path": "/healthz", "method": "GET"}}):
            self.assertTrue(pl.check_input_wiring(cfg, {"content": "你好"}), cfg)

    def test_whole_input_placeholder_always_passes(self) -> None:
        """整串 `{case.input}` 恒通过——渲染侧对标量走 `str` 替换、对结构化值原样替换，
        **两种都不落占位**，故不存在「输入没进去」的情形。"""
        cfg = {"request": {"body": {"q": "{case.input}"}}}
        self.assertEqual(pl.check_input_wiring(cfg, "裸串"), [])
        self.assertEqual(pl.check_input_wiring(cfg, {"content": "你好"}), [])

    def test_present_but_none_key_is_not_unreachable(self) -> None:
        """键在而值为 `None` **不算**不可达——渲染出的是 `"None"` 字符串，不是占位符。

        这是与「键缺/中途非 dict」的分界；判宽了会误驳合法载荷。
        """
        cfg = {"request": {"body": {"content": "{case.input.content}"}}}
        self.assertEqual(pl.check_input_wiring(cfg, {"content": None}), [])

    def test_input_turns_is_not_an_input_path(self) -> None:
        """`{case.input_turns.*}` 不是 `case.input.*`——回流只产 `input`，不产 `turns`。"""
        cfg = {"request": {"body": {"q": "{case.input_turns.0.content}"}}}
        self.assertTrue(pl.check_input_wiring(cfg, {"content": "你好"}))

    # ---- 集成层：驳回必须发生在建单之前（否则会留下孤儿 suite/case） ----

    def _run(self, adapter_config, input_value):
        env = {
            "payload_id": "p-r27",
            "source": {"agent": "good-question", "interface": "chat"},
            "evidence": {"input": input_value},
            "no_fallback_config": {"words": ["抱歉"]},
        }
        activated: list = []
        rejected: list = []

        async def _fake_reject(_db, _env, _pid, code, detail):
            rejected.append((code, detail))
            return "rejected"

        async def _fake_activate(*a, **_kw):
            activated.append(a)
            return mock.Mock(id=4242)

        agent = mock.Mock(adapter_config=adapter_config)
        with mock.patch.object(pl, "SessionLocal", _FakeScopedFull()), \
                mock.patch.object(pl, "validate_envelope", lambda _e: (True, [], "")), \
                mock.patch.object(pl, "resolve_agent", lambda *_a: _async(agent)), \
                mock.patch.object(pl, "resolve_interface", lambda *_a: _async(mock.Mock())), \
                mock.patch.object(pl, "sanitize_words", lambda _w: ["抱歉"]), \
                mock.patch.object(pl, "check_input_wiring", pl.check_input_wiring), \
                mock.patch.object(pl, "_reject", _fake_reject), \
                mock.patch.object(pl, "_activate", _fake_activate), \
                mock.patch.object(pl, "_ack_active", lambda *_a, **_kw: _async(None)):
            outcome = asyncio.run(pl._process_envelope(env))
        return outcome, activated, rejected

    def test_rejects_before_creating_case(self) -> None:
        """不自洽 ⇒ 驳回，且 `_activate` **一次都没被调用**（不得留孤儿 suite/case）。"""
        outcome, activated, rejected = self._run(
            {"request": {"body": {"question": "{case.input.question}"}}}, {"content": "你好"}
        )
        self.assertEqual(outcome, "rejected")
        self.assertEqual(activated, [])
        self.assertEqual(rejected[0][0], pl.REJECT_CONTENT_GAP)
        self.assertIn("case.input.question", rejected[0][1])

    def test_activates_when_shape_matches(self) -> None:
        """**反向对照（不可省）**：同结构、同输入，只因字段名对上 ⇒ 必须走激活。

        无此对照，「驳回通过」可能只是闸门把一切都拒了——那样测试是假的。
        """
        outcome, activated, rejected = self._run(
            {"request": {"body": {"content": "{case.input.content}"}}}, {"content": "你好"}
        )
        self.assertEqual(outcome, "activated")
        self.assertEqual(rejected, [])
        self.assertEqual(len(activated), 1)


class TestMissingSampleFileGate(unittest.TestCase):
    """批 A 存在闸：`evidence.input.file_path` 引用的文件在平台 uploads 内**不存在** ⇒ 驳回。

    背景（2026-09-21 真机实证）：cc 的回流 case 4085 载荷里 `file_path` =
    `/app/uploads/cc_gen_good.pdf`，**平台 uploads 里没有这个文件**（只有 `cc_good.pdf`）。
    装载期形状闸只判键可达性（`check_input_wiring`），**不判文件在不在** ⇒ 坏路径被原样
    收下、建单；直到 run 期 `multipart_files` 打开文件才 `FileNotFoundError`，该 case 记
    `na` + `contract_error`——**而装载期一声不吭**。同 suite 的另 6 条因文件恰好存在而全 pass，
    构成组内对照（唯一变量 = 文件在不在）。

    判据**复用探测期同一条**（`core/probe.py:validate_probe_input`：逃逸白名单 + isfile），
    不新增私有实现——两处必须同源，否则「探测拦、装载放」就是 R-27 那类分叉的翻版。

    **驳回码为何是 `offline_cap_gap` 而非 `content_gap`**：文件在**离线侧**的 uploads 里，
    online admin 补不了。落 `online_content_gap` 意味着 `_needs_reprocess` 对它「任意
    ack_status 都重处理」⇒ 每轮重拉、每轮同一原因驳回，**无限循环**。见
    `test_missing_sample_file_does_not_loop_after_ack`。
    """

    # cc 真实形态（真库读得）：占位只在 prepare 的 upload 步骤里，`request` 无 body。
    _CC_CFG = {
        "prepare": [{"name": "upload", "files": {"file": "{case.input.file_path}"}}],
        "request": {"path": "/api/tasks/{prepare.upload.task_id}/result", "method": "GET"},
    }

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.uploads = os.path.realpath(self._tmp.name)
        # `_assert_inside_uploads` 读的是 `base` 模块内的全局；`probe` 另持一份仅用于
        # 报错消息 ⇒ 两处都要 patch，否则出现「判据用新目录、消息里报旧目录」的分叉。
        self._p1 = mock.patch.object(base_mod, "_UPLOADS_DIR", self.uploads)
        self._p2 = mock.patch.object(probe_mod, "_UPLOADS_DIR", self.uploads)
        self._p1.start()
        self._p2.start()

    def tearDown(self) -> None:
        self._p1.stop()
        self._p2.stop()
        self._tmp.cleanup()

    def _put(self, name: str) -> str:
        path = os.path.join(self.uploads, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("x")
        return path

    def _run(self, adapter_config, input_value):
        env = {
            "payload_id": "p-batch-a",
            "source": {"agent": "contract-check", "interface": "result"},
            "evidence": {"input": input_value},
            "no_fallback_config": {"words": ["抱歉"]},
        }
        activated: list = []
        rejected: list = []

        async def _fake_reject(_db, _env, _pid, code, detail):
            rejected.append((code, detail))
            return "rejected"

        async def _fake_activate(*a, **_kw):
            activated.append(a)
            return mock.Mock(id=4242)

        agent = mock.Mock(adapter_config=adapter_config)
        with mock.patch.object(pl, "SessionLocal", _FakeScopedFull()), \
                mock.patch.object(pl, "validate_envelope", lambda _e: (True, [], "")), \
                mock.patch.object(pl, "resolve_agent", lambda *_a: _async(agent)), \
                mock.patch.object(pl, "resolve_interface", lambda *_a: _async(mock.Mock())), \
                mock.patch.object(pl, "sanitize_words", lambda _w: ["抱歉"]), \
                mock.patch.object(pl, "_reject", _fake_reject), \
                mock.patch.object(pl, "_activate", _fake_activate), \
                mock.patch.object(pl, "_ack_active", lambda *_a, **_kw: _async(None)):
            outcome = asyncio.run(pl._process_envelope(env))
        return outcome, activated, rejected

    # ---- 正 / 负 ----

    def test_missing_file_is_rejected_before_creating_case(self) -> None:
        """文件不存在 ⇒ 驳回，且 `_activate` 一次都没被调用（不得留孤儿 case）。"""
        ghost = os.path.join(self.uploads, "cc_gen_good.pdf")  # 故意不创建
        outcome, activated, rejected = self._run(
            self._CC_CFG, {"task_id": 668, "file_path": ghost}
        )
        self.assertEqual(outcome, "rejected")
        self.assertEqual(activated, [])
        self.assertEqual(rejected[0][0], pl.REJECT_MISSING_SAMPLE)
        self.assertIn("样例文件不存在", rejected[0][1])

    def test_existing_file_activates(self) -> None:
        """**反向对照（不可省）**：同结构、同输入键，只因文件真在 ⇒ 必须走激活。

        无此对照，「驳回」可能只是闸门把一切都拒了——那样其余断言全是假的。
        """
        outcome, activated, rejected = self._run(
            self._CC_CFG, {"task_id": 669, "file_path": self._put("cc_good.pdf")}
        )
        self.assertEqual(outcome, "activated")
        self.assertEqual(rejected, [])
        self.assertEqual(len(activated), 1)

    def test_non_file_input_is_unaffected(self) -> None:
        """**防误伤的关键负例**：cs/gq/sp 的输入无 `file_path` 键 ⇒ 本闸必须完全不介入。

        `validate_probe_input` 对无 `file_path` 的输入返回 None（「非文件型」），这是
        本闸不波及另三家的**唯一依据**——若哪天有人把它改成「无 file_path 即报错」，
        本用例是唯一会红的判据。
        """
        cfg = {"request": {"body": {"content": "{case.input.content}"}}}
        outcome, activated, rejected = self._run(cfg, {"content": "你好"})
        self.assertEqual(outcome, "activated")
        self.assertEqual(rejected, [])

    def test_escape_outside_uploads_is_rejected(self) -> None:
        """越界路径同样驳回（复用 `_assert_inside_uploads` 的逃逸闸，非新写）。"""
        outcome, _activated, rejected = self._run(
            self._CC_CFG, {"file_path": os.path.join(self._tmp.name, "..", "evil.pdf")}
        )
        self.assertEqual(outcome, "rejected")
        self.assertEqual(rejected[0][0], pl.REJECT_MISSING_SAMPLE)

    # ---- 顺序：形状闸在前 ----

    def test_wiring_gate_runs_first(self) -> None:
        """`file_path` 未被模板引用时它是**无关字段**，不得因它不存在而驳回。

        顺序反了会把「输入压根不进请求」误报成「样例文件不存在」，把结构问题指成数据问题。
        """
        cfg = {"request": {"body": {"content": "{case.input.content}"}}}
        outcome, _activated, rejected = self._run(
            cfg, {"file_path": os.path.join(self.uploads, "nope.pdf")}
        )
        self.assertEqual(outcome, "rejected")
        self.assertEqual(rejected[0][0], pl.REJECT_CONTENT_GAP)  # 形状闸的码，不是存在闸的
        self.assertNotIn("样例文件不存在", rejected[0][1])

    # ---- 驳回码的落库与「不循环」语义 ----

    def test_reject_code_maps_to_cap_gap(self) -> None:
        """内部细码映射到 `offline_cap_gap`（R2 自愈例外判的就是该列严格等值）。"""
        self.assertEqual(
            pl._ONLINE_REASON[pl.REJECT_MISSING_SAMPLE], "offline_cap_gap"
        )
        self.assertIn(pl._ONLINE_REASON[pl.REJECT_MISSING_SAMPLE], _ONLINE_VOCAB)

    def test_missing_sample_file_does_not_loop_after_ack(self) -> None:
        """**本批的核心不变量**：已 acked 的 `missing_sample_file` 行**不得**被反复重处理。

        若误落 `content_gap`（online_content_gap），`_needs_reprocess` 对它是「任意
        ack_status 都 True」⇒ admin 在 online 侧无论怎么 requeue，文件都不会自己出现，
        于是每轮重拉、每轮同一原因驳回，**无限循环**。落 `offline_cap_gap` 则只在
        `ack_status∈{none,pending}` 复位。
        """
        row = _InboxRow("rejected", "acked",
                        reject_code=pl._ONLINE_REASON[pl.REJECT_MISSING_SAMPLE])
        self.assertFalse(pl._needs_reprocess(row))

        # 反向钉住：同一行若是「未 ack」则**必须**复位（首次驳回/对账未闭环要能重放）
        row_pending = _InboxRow("rejected", "pending",
                                reject_code=pl._ONLINE_REASON[pl.REJECT_MISSING_SAMPLE])
        self.assertTrue(pl._needs_reprocess(row_pending))

    def test_content_gap_would_have_looped(self) -> None:
        """对照：证明上面那条**不是自动成立的**——同样的行落到 content_gap 就会循环。"""
        row = _InboxRow("rejected", "acked", reject_code="online_content_gap")
        self.assertTrue(pl._needs_reprocess(row))


class TestTransientSet(unittest.TestCase):
    """瞬态集的值域 —— **只列有实测支撑的类型**，且必须与「禁止纳入」严格分开。

    划界判据 = 「错误的成因是否可能由输入内容决定」。名单内的实测依据：
      llm_timeout    ← 簇 3861 同输入「4 fail → 2 pass」（2026-09-16）
      llm_connection ← 14 条簇全部用原输入跑通（S1 前无替换通道）
    这两条覆盖全库 18 条簇的 100%（2026-09-21 实测）。
    """

    def test_exact_membership(self) -> None:
        """**逐字钉死**：放宽哪怕一项，产生的是**静默假绿**（换了输入 ⇒ pass ⇒ 判「已修复」，
        而真实用户的文件仍会让它挂）——不可见、无人会红。故用相等而非包含。"""
        self.assertEqual(pl.TRANSIENT_ERROR_TYPES, frozenset({"llm_timeout", "llm_connection"}))

    def test_content_related_types_are_excluded(self) -> None:
        """内容相关 / 兜底值域**必须**在名单外（纳错方向不可见，代价不对称）。"""
        for t in ("llm_context_exceeded", "llm_interface_business", "external_non_llm",
                  "llm_other", "llm_empty_response", "llm_parse_error"):
            self.assertNotIn(t, pl.TRANSIENT_ERROR_TYPES, t)

    def test_zero_observation_types_not_yet_included(self) -> None:
        """零发生的三个**刻意未纳入**（不是「已排除」）——将来纳入时本条会红，提醒补实测依据。

        若哪天它们真产生了簇，处置 = 加进集合 + 更新本条 + 补一条实测记录。
        """
        for t in ("llm_rate_limit", "db_error", "redis_error"):
            self.assertNotIn(t, pl.TRANSIENT_ERROR_TYPES, t)


class TestInputSubstitution(unittest.TestCase):
    """S1 分流：文件缺失时，**瞬态类**错误换平台样例继续回归；其余干净驳回。

    对照实验（2026-09-21 真机）：cc 同一 suite 的 6 条 case 用 `/app/uploads/b1_missing_date.pdf`
    （平台**有**）全 pass，第 7 条用 `cc_gen_good.pdf`（平台**没有**）记 `na`——唯一变量是
    文件在不在，**与错误类型无关**。故本批要修的不是「怎么让 cc 通过」，而是
    「文件不在时怎么办」：瞬态类换样例是**有效回归**，内容相关类换样例是**掩盖真问题**。
    """

    _CC_CFG = {
        "prepare": [{"name": "upload", "files": {"file": "{case.input.file_path}"}}],
        "request": {"path": "/api/tasks/{prepare.upload.task_id}/result", "method": "GET"},
    }
    _SAMPLE = "/app/uploads/cc_b1_missing_date.pdf"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.uploads = os.path.realpath(self._tmp.name)
        self._p1 = mock.patch.object(base_mod, "_UPLOADS_DIR", self.uploads)
        self._p2 = mock.patch.object(probe_mod, "_UPLOADS_DIR", self.uploads)
        self._p1.start()
        self._p2.start()

    def tearDown(self) -> None:
        self._p1.stop()
        self._p2.stop()
        self._tmp.cleanup()

    def _run(self, *, input_value, error_type="llm_connection", sample=_SAMPLE):
        env = {
            "payload_id": "p-batch-b",
            "source": {"agent": "contract-check", "interface": "result"},
            "evidence": {"input": input_value},
            "no_fallback_config": {"words": ["抱歉"]},
        }
        if error_type is not None:
            env["source"]["error_type"] = error_type

        activated: list = []
        rejected: list = []

        async def _fake_reject(_db, _env, _pid, code, detail):
            rejected.append((code, detail))
            return "rejected"

        async def _fake_activate(_db, _env, _pid, _agent, _iface, _words, inp):
            activated.append(inp)
            return mock.Mock(id=4242)

        async def _fake_sample(_db, _agent):
            return sample

        with mock.patch.object(pl, "SessionLocal", _FakeScopedFull()), \
                mock.patch.object(pl, "validate_envelope", lambda _e: (True, [], "")), \
                mock.patch.object(pl, "resolve_agent",
                                  lambda *_a: _async(mock.Mock(adapter_config=self._CC_CFG))), \
                mock.patch.object(pl, "resolve_interface", lambda *_a: _async(mock.Mock())), \
                mock.patch.object(pl, "sanitize_words", lambda _w: ["抱歉"]), \
                mock.patch.object(pl, "_resolve_sample_file", _fake_sample), \
                mock.patch.object(pl, "_reject", _fake_reject), \
                mock.patch.object(pl, "_activate", _fake_activate), \
                mock.patch.object(pl, "_ack_active", lambda *_a, **_kw: _async(None)):
            outcome = asyncio.run(pl._process_envelope(env))
        return outcome, activated, rejected

    def _ghost(self):
        return os.path.join(self.uploads, "cc_gen_good.pdf")   # 故意不创建

    # ---- 正路径 ----

    def test_transient_error_gets_sample_substituted(self) -> None:
        """瞬态类 + 文件缺失 ⇒ 建单，且落库 input 是**样例路径** + 显式降级凭据。"""
        outcome, activated, rejected = self._run(
            input_value={"task_id": 668, "file_path": self._ghost()}
        )
        self.assertEqual(outcome, "activated")
        self.assertEqual(rejected, [])
        self.assertEqual(activated[0]["file_path"], self._SAMPLE)
        self.assertEqual(activated[0]["_substituted_from"], self._ghost())
        self.assertEqual(activated[0]["task_id"], 668)   # 其余键原样保留

    def test_content_related_error_is_rejected(self) -> None:
        """内容相关类 ⇒ **不替换**，干净驳回（换样例只会掩盖真问题）。"""
        outcome, activated, rejected = self._run(
            input_value={"task_id": 668, "file_path": self._ghost()},
            error_type="llm_context_exceeded",
        )
        self.assertEqual(outcome, "rejected")
        self.assertEqual(activated, [])
        self.assertEqual(rejected[0][0], pl.REJECT_MISSING_SAMPLE)

    def test_llm_other_is_rejected(self) -> None:
        """**兜底值域必须驳回** —— 纳入它等于把未知成因一律当瞬态，静默假绿。"""
        outcome, _activated, rejected = self._run(
            input_value={"task_id": 668, "file_path": self._ghost()},
            error_type="llm_other",
        )
        self.assertEqual(outcome, "rejected")

    def test_legacy_envelope_without_error_type_is_rejected(self) -> None:
        """旧信封（S1 前组装、无 `error_type`）⇒ 按「不可替换」处理，**不崩**。

        这是向后兼容的判据：online 必须先用上新信封，替换才生效。
        """
        outcome, activated, rejected = self._run(
            input_value={"task_id": 668, "file_path": self._ghost()},
            error_type=None,
        )
        self.assertEqual(outcome, "rejected")
        self.assertEqual(activated, [])
        self.assertEqual(rejected[0][0], pl.REJECT_MISSING_SAMPLE)

    def test_no_sample_available_is_rejected(self) -> None:
        """取不到可用样例 ⇒ fail-closed 驳回，**不得**假装替换成功。

        若这里放行，坏路径会原样落库 ⇒ 退化成批 A 之前那个静默 `na`。
        """
        outcome, activated, rejected = self._run(
            input_value={"task_id": 668, "file_path": self._ghost()},
            sample=None,
        )
        self.assertEqual(outcome, "rejected")
        self.assertEqual(activated, [])
        self.assertIn("无可用样例文件可替换", rejected[0][1])

    # ---- 双向钉住：不能「一律替换」 ----

    def test_existing_file_is_not_substituted(self) -> None:
        """文件**在**时不得替换 —— 否则每次回流都换成样例，「用现场输入回归」直接失效。"""
        real = os.path.join(self.uploads, "cc_good.pdf")
        with open(real, "w", encoding="utf-8") as fh:
            fh.write("x")
        outcome, activated, rejected = self._run(input_value={"file_path": real})
        self.assertEqual(outcome, "activated")
        self.assertEqual(rejected, [])
        self.assertEqual(activated[0]["file_path"], real)
        self.assertNotIn("_substituted_from", activated[0])

    # ---- 分叉点护栏（本批最关键的一条）----

    def test_activate_receives_substituted_input(self) -> None:
        """**`_activate` 收到的必须是替换后的输入**，而不是信封里的原值。

        `evidence.input` 在装载链里被读两次（`_self_check` 校验 / `_activate` 建单）。
        只在自检里替换、`_activate` 仍重读信封 ⇒ **替换生效在错的地方**：落库的还是坏路径，
        run 期照旧 `na`，**而没有任何判据会红**。故替换值随 `ctx` 走，本条钉住它。
        """
        outcome, activated, _rejected = self._run(
            input_value={"task_id": 668, "file_path": self._ghost()}
        )
        self.assertEqual(outcome, "activated")
        self.assertNotEqual(activated[0]["file_path"], self._ghost(),
                            "_activate 拿到了原（坏）路径 —— 替换没生效在落库的那个值上")


class TestActivatePersistsGivenInput(unittest.TestCase):
    """**真跑 `_activate`**（不打桩），断言它建出的 case 落的是**给它的那个** input。

    为什么必须真跑：本文件其余用例都把 `_activate` 打桩，只断言「它**收到**了替换后的
    input」。而「收到」与「落库」之间还有一次赋值（`input=input_value`）—— 打桩把这段
    整个跳过了，属于 [[mock-boundary-hides-wiring-break]]：mock 掉哪层就验不到那层之后。
    本批整个 S1 的价值就落在这一次赋值上（落错 ⇒ 跑的还是坏路径 ⇒ 静默 `na`），
    而它在打桩的单测里**永远不会红**。

    真机取证（2026-09-21）已证 `_self_check` 在真库真数据上返回替换值；本条补上后半截。
    """

    _SAMPLE = "/app/uploads/cc_b1_missing_date.pdf"
    _GHOST = "/app/uploads/cc_gen_good.pdf"

    def test_real_activate_persists_the_substituted_input(self) -> None:
        added: list = []
        substituted = {"task_id": 668, "file_path": self._SAMPLE,
                       "_substituted_from": self._GHOST}

        class _Scalars:
            def first(self):
                return mock.Mock(id=77)      # 已有 error suite ⇒ 不走建 suite 分支

        class _Result:
            def scalars(self):
                return _Scalars()

        class _Db:
            async def execute(self, *_a, **_k):
                return _Result()

            def add(self, obj):
                added.append(obj)

            async def flush(self):
                for o in added:
                    if getattr(o, "id", None) is None:
                        o.id = 9001        # 冒充 DB 回填主键

        envelope = {"schema_version": "1.0", "case_type": "regression_error",
                    "evidence": {"input": {"task_id": 668, "file_path": self._GHOST}}}
        with mock.patch.object(pl, "_inbox_put",
                               lambda *_a, **_kw: _async(None)):
            case = asyncio.run(pl._activate(
                _Db(), envelope, "p-seam", mock.Mock(id=1, name="contract-check"),
                mock.Mock(id=5), ["抱歉"], substituted,
            ))

        self.assertEqual(case.input, substituted)
        # 留档必须保持**原文**：它是出站⑤环 trigger_signal_id 的唯一取值来源，不得被替换污染
        self.assertEqual(case.backflow_envelope, envelope)
        self.assertEqual(
            case.backflow_envelope["evidence"]["input"]["file_path"], self._GHOST,
            "backflow_envelope 被替换污染 ⇒ ⑤环 trigger_signal_id 会取到平台样例路径")


class TestResolveSampleFile(unittest.TestCase):
    """样例来源：该 agent **非 error suite** 的、文件**实存**的首条文件型用例。

    两条约束各自对应一个真实的失败模式，故都钉住：
      排除 error suite —— 那些 case 的 file_path 正是要替换掉的坏路径，纳入即循环论证；
      逐个验到存在为止 —— 样例文件被删时不得沿用，否则替换后的路径依旧不存在。
    """

    _SAMPLE = "/app/uploads/ok.pdf"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.uploads = os.path.realpath(self._tmp.name)
        self._p1 = mock.patch.object(base_mod, "_UPLOADS_DIR", self.uploads)
        self._p2 = mock.patch.object(probe_mod, "_UPLOADS_DIR", self.uploads)
        self._p1.start()
        self._p2.start()
        self.sample = os.path.join(self.uploads, "ok.pdf")
        with open(self.sample, "w", encoding="utf-8") as fh:
            fh.write("x")

    def tearDown(self) -> None:
        self._p1.stop()
        self._p2.stop()
        self._tmp.cleanup()

    def _resolve(self, inputs):
        """inputs = 候选用例的 input 列表（按 id 升序）→ 解析结果。

        `_resolve_sample_file` 收 `db` 直接调用（不经 `SessionLocal`），故此处只需一个
        `.execute(...).scalars().all()` 可用的假 session。
        """
        cases = [mock.Mock(input=i, input_type="file", status="active") for i in inputs]

        class _ScalarsAll:
            def all(self):
                return cases

        class _ResultAll:
            def scalars(self):
                return _ScalarsAll()

        class _Db:
            async def execute(self, *_a, **_k):
                return _ResultAll()

        return asyncio.run(pl._resolve_sample_file(_Db(), mock.Mock(id=1)))

    def test_returns_first_existing(self) -> None:
        got = self._resolve([{"file_path": self.sample}])
        self.assertEqual(got, self.sample)

    def test_skips_non_file_and_missing_then_finds_one(self) -> None:
        """非文件型（无 file_path）与不存在的**都要跳过**，不能取首条就返回。"""
        gone = os.path.join(self.uploads, "gone.pdf")
        got = self._resolve([{"content": "你好"}, {"file_path": gone},
                             {"file_path": self.sample}])
        self.assertEqual(got, self.sample)

    def test_returns_none_when_all_missing(self) -> None:
        """全部不可用 ⇒ None（调用方据此 fail-closed 驳回）。"""
        gone = os.path.join(self.uploads, "gone.pdf")
        self.assertIsNone(self._resolve([{"file_path": gone}, {"content": "x"}]))

    def test_deleted_sample_file_makes_it_none(self) -> None:
        """往返：样例文件删掉后必须解析为 None —— 防「假装替换成功」。"""
        self.assertEqual(self._resolve([{"file_path": self.sample}]), self.sample)
        os.remove(self.sample)
        self.assertIsNone(self._resolve([{"file_path": self.sample}]))


def _async(value):
    """把值包成 awaitable（用于 patch 掉 async 依赖）。"""
    async def _coro():
        return value
    return _coro()


if __name__ == "__main__":
    unittest.main()
