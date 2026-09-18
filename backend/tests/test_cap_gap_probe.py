"""`runner/cap_gap_probe` R-8 探测态单测（§5.6 谓词 / §5.8 节流）。

**为什么写**：详设给 `offline_cap_gap` 驳回行的恢复入口是「每小时本地重跑自检 + 映射」，
而这条探测态从未落码 ⇒ 映射补齐后那条已 acked 的行**没有任何恢复路径**（重处理谓词按 R-8
收窄、人工 requeue 撞同一谓词且擦掉 online 侧 R2 判据）。本文件锁住新模块的三条语义。

**本文件测不到的（留给真库探针 `tests/integration/cap_gap_probe_probe.py`）**：真库上的
行选择（谓词能否命中真行）、`_activate` 真建 case、连跑两轮的库内残留。宿主单测一律不连库
（`conftest.py` 约定），故此处只测**判据、调用面与节流分支**。
"""
import asyncio
import unittest
from unittest import mock

from app.runner import cap_gap_probe as cgp
from app.runner import pull_loop as pl


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    """只实现 probe_once 用到的三个面：execute / commit / 异步上下文。"""

    def __init__(self, rows):
        self.rows = rows
        self.committed = 0

    async def execute(self, *_a, **_k):
        return _FakeResult(self.rows)

    async def commit(self):
        self.committed += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class _Row:
    """冒充探测态行（rejected + offline_cap_gap + acked）。"""

    def __init__(self, payload_id="p-1", envelope_json=None):
        self.payload_id = payload_id
        self.status = "rejected"
        self.reject_code = "offline_cap_gap"
        self.ack_status = "acked"
        self.envelope_json = (envelope_json if envelope_json is not None
                              else {"payload_id": payload_id})


def _run_probe_once(rows, self_check_ret):
    """跑一轮 probe_once，返回 (stat, ack_mock, activate_mock, 假 session)。"""
    session = _FakeSession(rows)
    ack = mock.AsyncMock()
    case = mock.Mock(id=4076)
    activate = mock.AsyncMock(return_value=case)
    with mock.patch.object(cgp, "SessionLocal", lambda: session), \
            mock.patch.object(cgp, "_self_check", mock.AsyncMock(return_value=self_check_ret)), \
            mock.patch.object(cgp, "_activate", activate), \
            mock.patch.object(cgp, "_ack_active", ack):
        stat = asyncio.run(cgp.probe_once())
    return stat, ack, activate, session


class TestPredicate(unittest.TestCase):
    """谓词值域：三个要素各自钉死（少一个就会把同族另两码误纳进来）。"""

    def test_predicate_exact(self) -> None:
        self.assertEqual(cgp.CAP_GAP_PROBE, ("rejected", "offline_cap_gap", "acked"))

    def test_reject_code_is_cap_gap(self) -> None:
        """第三要素若写成 online_content_gap，本模块会去抢 R-28 那条路的行。"""
        self.assertEqual(cgp.CAP_GAP_PROBE[1], pl.REJECT_CAP_GAP)


class TestSameSource(unittest.TestCase):
    """判据同源：探测态与拉取路径必须**是同一个函数对象**。

    分开写必然分叉，而分叉的后果是「拉取时驳回、探测时放行」——把当初驳回的行按已放宽的
    判据建 case（R-27 装载闸同型）。`assertIs` 是这条的可执行判据：谁哪天抄一份就不绿了。
    """

    def test_self_check_is_same_object(self) -> None:
        self.assertIs(cgp._self_check, pl._self_check)

    def test_activate_and_ack_are_same_objects(self) -> None:
        self.assertIs(cgp._activate, pl._activate)
        self.assertIs(cgp._ack_active, pl._ack_active)


class TestProbeOnce(unittest.TestCase):
    def test_still_missing_is_silent(self) -> None:
        """仍缺 → 静默等轮：零出站、不改状态（R-8 节流语义的核心）。"""
        row = _Row()
        stat, ack, activate, session = _run_probe_once(
            [row], ("offline_cap_gap", "agent 未登记：'x'", None)
        )
        self.assertEqual(stat, {"scanned": 1, "activated": 0, "still_missing": 1})
        ack.assert_not_called()
        activate.assert_not_called()
        self.assertEqual(session.committed, 0)
        self.assertEqual(row.ack_status, "acked")      # 逐项：不被复位
        self.assertEqual(row.status, "rejected")

    def test_healed_activates_once(self) -> None:
        """补齐 → 建 case + **单次** active ack。"""
        row = _Row(payload_id="p-9")
        stat, ack, activate, session = _run_probe_once(
            [row], (None, "", (mock.Mock(), mock.Mock(), ["兜底话术"]))
        )
        self.assertEqual(stat, {"scanned": 1, "activated": 1, "still_missing": 0})
        activate.assert_awaited_once()
        ack.assert_awaited_once_with("p-9", 4076)
        self.assertEqual(session.committed, 1)

    def test_two_rounds_no_duplicate(self) -> None:
        """连跑两轮：第二轮该行已不在谓词命中集（status 转 active/acked+case_id），
        故零重复建 case、零重复 ack —— 真机探针那条「必须连跑两遍」的教训在单测层的代理。
        """
        row = _Row(payload_id="p-9")
        ctx = (mock.Mock(), mock.Mock(), ["兜底话术"])
        stat1, ack1, act1, _ = _run_probe_once([row], (None, "", ctx))
        stat2, ack2, act2, _ = _run_probe_once([], (None, "", ctx))   # 第二轮：谓词不再命中
        self.assertEqual(stat1["activated"], 1)
        self.assertEqual(stat2["scanned"], 0)
        self.assertEqual(ack1.await_count, 1)
        self.assertEqual(act1.await_count, 1)
        act2.assert_not_called()
        ack2.assert_not_called()


class TestLoopGate(unittest.TestCase):
    def test_disabled_returns_without_scanning(self) -> None:
        """backflow_enabled=false ⇒ 直接 return、零出站（自愈要发 active ack 出站）。"""
        with mock.patch("app.core.config.settings.backflow_enabled", False), \
                mock.patch.object(cgp, "probe_once", mock.AsyncMock()) as probe:
            asyncio.run(cgp.cap_gap_probe_loop())
        probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
