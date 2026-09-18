"""`core/backflow_client` 契约单测（批 B #232）。

**为什么这几条值得写**：本文件的断言全部来自**真机实测翻出来的缺陷**，不是照着实现抄的。
批 B 开工时全量套件 825 passed 对这 3 个新文件**零判别力**——首次真拉才暴露下面两处：

1. `case_id` 传 int → online `PullAckRequest.case_id: str | None` 判 **422**（与批 C 已预警的
   `run_id` 同型陷阱）。→ `test_ack_case_id_serialized_as_str`
2. ~~`build_agent_client()` **只给 CIDR 不给 allow_hosts** 时，容器名基址会被
   `AllowlistAsyncClient.send` 走「换成解析 IP + 补 Host」的重写路径，产出两个大小写不同的
   Host 头 → h11 `LocalProtocolError`，**请求根本发不出去**。~~
   **✅ 根因已于 2026-09-15 批 4 修复**（`core/http.py` 的 `send()` 改为摘掉原 Host 项再加，
   而非叠加），当时的绕过手段（把 hostname 塞进 `allow_hosts`）**已撤除**。
   → 护栏改为 `test_base_host_not_special_cased`（**方向反转**：再塞白名单即变红）；
   Host 去重本身的判据在 `tests/test_security.py`。**R-26 / O-F.9**。

用 `MockTransport` 而不是真出站：这两条断言的是**请求形状**（载荷字段类型 / 白名单入参），
不是连通性；连通性由批 B 的真 online 端到端验收负责（两者不可互相替代）。
"""
import json
import unittest
from unittest import mock

import httpx

from app.core import backflow_client as bc
from app.core.config import settings


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_base = settings.backflow_online_base
        self._orig_secret = settings.evaluator_service_secret
        settings.backflow_online_base = "http://obs-backend:8000/"
        settings.evaluator_service_secret = "unit-test-secret"

    def tearDown(self) -> None:
        settings.backflow_online_base = self._orig_base
        settings.evaluator_service_secret = self._orig_secret

    def _patch_client(self, captured: list, *, status: int = 200, body: dict | None = None):
        """把 `_client` 换成 MockTransport 客户端，并记录发出的请求。"""
        def _handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(status, json=body if body is not None else {})

        def _factory():
            return httpx.AsyncClient(transport=httpx.MockTransport(_handler))

        return mock.patch.object(bc, "_client", _factory)


class TestBackflowClient(_Base):
    def test_ack_case_id_serialized_as_str(self) -> None:
        """case_id 必须以 str 出网——传 int 会被 online pydantic 判 422（实测）。"""
        import asyncio

        captured: list = []
        with self._patch_client(captured, body={"payload_id": "p1", "offline_status": "active"}):
            asyncio.run(bc.ack("p1", "active", case_id=3618))
        body = json.loads(captured[0].content)
        self.assertIsInstance(body["case_id"], str)
        self.assertEqual(body["case_id"], "3618")

    def test_ack_omits_optional_fields_when_absent(self) -> None:
        """invalidated 驳回可只带 payload_id+action；不得注入 null 键。"""
        import asyncio

        captured: list = []
        with self._patch_client(captured, body={"payload_id": "p1", "offline_status": "assembled"}):
            asyncio.run(bc.ack("p1", "invalidated"))
        body = json.loads(captured[0].content)
        self.assertEqual(body, {"payload_id": "p1", "action": "invalidated"})

    def test_pull_body_matches_online_contract(self) -> None:
        """schema_version/case_type 是 online 的硬校验；缺省不带增量锚。"""
        import asyncio

        captured: list = []
        with self._patch_client(captured, body={"payloads": [], "next_token": None}):
            asyncio.run(bc.pull_payloads())
        body = json.loads(captured[0].content)
        self.assertEqual(body["schema_version"], "1.0")
        self.assertEqual(body["case_type"], "regression_error")
        self.assertEqual(body["limit"], bc.PULL_LIMIT)
        self.assertNotIn("since_ts", body)
        self.assertNotIn("next_token", body)

    def test_pull_passes_incremental_anchors(self) -> None:
        import asyncio

        captured: list = []
        with self._patch_client(captured, body={"payloads": [], "next_token": None}):
            asyncio.run(bc.pull_payloads(since_ts="2026-09-14T00:00:00Z", next_token="tok"))
        body = json.loads(captured[0].content)
        self.assertEqual(body["since_ts"], "2026-09-14T00:00:00Z")
        self.assertEqual(body["next_token"], "tok")

    def test_bearer_header_uses_evaluator_secret(self) -> None:
        import asyncio

        captured: list = []
        with self._patch_client(captured, body={"payloads": [], "next_token": None}):
            asyncio.run(bc.pull_payloads())
        self.assertEqual(
            captured[0].headers["Authorization"], f"Bearer {settings.evaluator_service_secret}"
        )

    def test_non_200_raises_client_error(self) -> None:
        """401 的症状长得像「对端没有载荷」，必须显式抛而不是当空集吞掉。"""
        import asyncio

        captured: list = []
        with self._patch_client(captured, status=401, body={"detail": "unauthorized"}), \
                self.assertRaises(bc.BackflowClientError):
            asyncio.run(bc.pull_payloads())

    def test_base_host_not_special_cased(self) -> None:
        """基址 host **不得**再被塞进 allow_hosts —— 护栏方向已随 R-26 修复反转。

        原护栏（`test_base_host_passed_to_allowlist`）护的是「把 hostname 入白名单就能发出去」，
        那是**绕开** `core/http.py` 双 Host 头缺陷的手段，代价 = 跳过 IP 解析校验。
        根因已修（2026-09-15 批 4：`send()` 摘掉重复 Host 项而非叠加），故绕过撤除 ——
        本集成恢复完整的「解析全部 IP 并校验在内网段内」。

        回归护栏（新方向）：若有人重新引入 `extra_hosts=[hostname]`（无论出于何种理由），
        本断言立即变红 —— 那会静默撤掉本集成的 SSRF 解析校验这一层。
        容器名基址「发得出去」这一属性，现由 `tests/test_security.py` 的 Host 去重用例保证。
        """
        captured: dict = {}

        def _fake_build(extra_hosts=(), extra_cidrs=()):
            captured["hosts"] = list(extra_hosts)
            return object()

        with mock.patch.object(bc, "build_agent_client", _fake_build):
            bc._client()
        self.assertEqual(captured["hosts"], [])

    def test_trailing_slash_does_not_double(self) -> None:
        """基址带尾斜杠时不得拼出 `//api/v1/...`。"""
        import asyncio

        captured: list = []
        with self._patch_client(captured, body={"payloads": [], "next_token": None}):
            asyncio.run(bc.pull_payloads())
        self.assertEqual(str(captured[0].url), "http://obs-backend:8000/api/v1/pull/payloads")


if __name__ == "__main__":
    unittest.main()
