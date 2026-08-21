"""4.3 初始化脚手架核心逻辑单测：标准契约解析 + 差异对比。

纯函数（parse_manifest / diff_manifest / unique_name），不触网络/DB。
核心逻辑（CLAUDE.md）：契约强校验分支与 diff 归类是核心，逐条覆盖失败路径。
"""
import unittest

from app.core.contracts import diff_manifest, parse_manifest, unique_name

MANIFEST_OK = {
    "agent": "customer-service",
    "contract_version": "1.0",
    "interfaces": [
        {"name": "chat", "path": "/api/v1/sessions/{sid}/messages", "method": "POST",
         "contract_type": "sse", "llm": True},
        {"name": "login", "path": "/api/v1/auth/login", "method": "POST",
         "llm": False},
    ],
    "scenes": [
        {"tag": "greeting", "description": "问候与闲聊"},
        {"tag": "order_query", "description": "订单查询"},
    ],
}


class TestParseManifest(unittest.TestCase):
    def test_ok(self):
        m, errors = parse_manifest(MANIFEST_OK)
        self.assertIsNotNone(m)
        self.assertEqual(errors, [])
        self.assertEqual(m.agent, "customer-service")
        self.assertEqual(len(m.interfaces), 2)
        self.assertEqual(len(m.scenes), 2)

    def test_not_dict(self):
        m, errors = parse_manifest("not-a-dict")
        self.assertIsNone(m)
        self.assertTrue(errors)

    def test_missing_agent(self):
        payload = {k: v for k, v in MANIFEST_OK.items() if k != "agent"}
        m, errors = parse_manifest(payload)
        self.assertIsNone(m)
        self.assertTrue(any("agent" in e for e in errors))

    def test_llm_true_without_contract_type(self):
        payload = dict(MANIFEST_OK)
        payload["interfaces"] = [
            {"name": "chat", "path": "/x", "method": "POST", "llm": True},
        ]
        m, errors = parse_manifest(payload)
        self.assertIsNone(m)
        self.assertTrue(any("contract_type" in e for e in errors))

    def test_llm_false_without_contract_type_ok(self):
        payload = dict(MANIFEST_OK)
        payload["interfaces"] = [
            {"name": "login", "path": "/api/v1/auth/login", "method": "POST", "llm": False},
        ]
        m, errors = parse_manifest(payload)
        self.assertIsNotNone(m)
        self.assertEqual(errors, [])

    def test_path_not_slash(self):
        payload = dict(MANIFEST_OK)
        payload["interfaces"] = [
            {"name": "chat", "path": "api/v1/chat", "method": "POST", "contract_type": "sse"},
        ]
        m, errors = parse_manifest(payload)
        self.assertIsNone(m)
        self.assertTrue(any("path" in e for e in errors))

    def test_dup_method_path(self):
        payload = dict(MANIFEST_OK)
        payload["interfaces"] = [
            {"name": "a", "path": "/x", "method": "POST", "contract_type": "sse"},
            {"name": "b", "path": "/x", "method": "post", "contract_type": "sse"},
        ]
        m, errors = parse_manifest(payload)
        self.assertIsNone(m)
        self.assertTrue(any("重复" in e for e in errors))

    def test_scene_tag_too_long(self):
        payload = dict(MANIFEST_OK)
        payload["scenes"] = [{"tag": "x" * 65, "description": ""}]
        m, errors = parse_manifest(payload)
        self.assertIsNone(m)
        self.assertTrue(any("tag" in e for e in errors))


class TestDiffManifest(unittest.TestCase):
    def test_four_categories(self):
        m, _ = parse_manifest(MANIFEST_OK)
        existing = [
            {"id": 1, "name": "chat", "path": "/api/v1/sessions/{sid}/messages",
             "method": "POST", "contract_type": "sse", "enabled": True},
            {"id": 2, "name": "old_api", "path": "/api/v1/legacy", "method": "GET",
             "contract_type": "sync", "enabled": True},
            {"id": 3, "name": "disabled_old", "path": "/api/v1/gone", "method": "POST",
             "contract_type": "sse", "enabled": False},
        ]
        d = diff_manifest(m, existing)
        # chat 已在 existing 且无差异 → 不进 added
        self.assertEqual([i["path"] for i in d["added"]], [])
        self.assertEqual(len(d["existing"]), 1)
        self.assertEqual(d["existing"][0]["changed"], [])
        # 库中 enabled 但清单未声明 → missing
        self.assertEqual([i["path"] for i in d["missing"]], ["/api/v1/legacy"])
        # login llm=false → auxiliary
        self.assertEqual(len(d["auxiliary"]), 1)
        self.assertEqual(d["auxiliary"][0]["name"], "login")

    def test_added_when_empty_lib(self):
        m, _ = parse_manifest(MANIFEST_OK)
        d = diff_manifest(m, [])
        self.assertEqual(len(d["added"]), 1)
        self.assertEqual(d["added"][0]["name"], "chat")

    def test_changed_fields(self):
        m, _ = parse_manifest(MANIFEST_OK)
        existing = [
            {"id": 1, "name": "chat_old", "path": "/api/v1/sessions/{sid}/messages",
             "method": "POST", "contract_type": "sync", "enabled": True},
        ]
        d = diff_manifest(m, existing)
        self.assertEqual(len(d["existing"]), 1)
        self.assertIn("name", d["existing"][0]["changed"])
        self.assertIn("contract_type", d["existing"][0]["changed"])


class TestUniqueName(unittest.TestCase):
    def test_no_conflict(self):
        self.assertEqual(unique_name("chat", {"login"}), "chat")

    def test_conflict_appends(self):
        self.assertEqual(unique_name("chat", {"chat", "login"}), "chat_2")

    def test_multiple_conflicts(self):
        self.assertEqual(unique_name("chat", {"chat", "chat_2"}), "chat_3")


if __name__ == "__main__":
    unittest.main()
