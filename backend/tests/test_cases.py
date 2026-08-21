"""5.2 用例管理核心逻辑单测（纯函数，不触 DB/网络/鉴权链）。

只 import app.core.case_rules（宿主无 aiomysql/aiosqlite 时可直接跑）。

覆盖（CLAUDE.md 核心逻辑=业务分支）：
- is_agent_owner：留出集/字段级权限判定（cases 列表/详情过滤依据）
- check_owner_denies_golden：职责分离（owner 禁改 golden_answer/assertions）
- validate_upload：上传校验（扩展名白名单 / 大小上限 / pdf 魔数）

HTTP 行为（viewer 403、留出集列表过滤、状态机落库）由容器 e2e 覆盖（5.2e）。
"""
import unittest

from app.core.case_rules import (
    DEFAULT_MAX_MB, check_owner_denies_golden, is_agent_owner, validate_upload,
)
from app.core.errors import ApiError, E_FILE_SIZE, E_FILE_TYPE
from app.models.agent import Agent
from app.models.user import User


class TestIsAgentOwner(unittest.TestCase):
    def _user(self, uid, role="evaluator"):
        return User(id=uid, username=f"u{uid}", role=role, enabled=True)

    def test_owner_match(self):
        self.assertTrue(is_agent_owner(Agent(id=1, owner_id=5), self._user(5)))

    def test_owner_mismatch(self):
        self.assertFalse(is_agent_owner(Agent(id=1, owner_id=5), self._user(6)))

    def test_owner_none(self):
        self.assertFalse(is_agent_owner(Agent(id=1, owner_id=None), self._user(5)))

    def test_agent_none(self):
        self.assertFalse(is_agent_owner(None, self._user(5)))


class TestCheckOwnerDeniesGolden(unittest.TestCase):
    """owner 禁标/禁改自己 agent 的 golden_answer/assertions（§四-4.5）。"""

    def _user(self, uid, role):
        return User(id=uid, username=f"u{uid}", role=role, enabled=True)

    def test_admin_always_pass(self):
        check_owner_denies_golden(5, self._user(1, "admin"))  # 不应抛

    def test_owner_forbidden(self):
        with self.assertRaises(ApiError):
            check_owner_denies_golden(5, self._user(5, "evaluator"))

    def test_other_evaluator_pass(self):
        check_owner_denies_golden(5, self._user(6, "evaluator"))  # 独立 QA 可标

    def test_owner_none_pass(self):
        check_owner_denies_golden(None, self._user(5, "evaluator"))  # 未指派 owner 不限

    def test_viewer_non_owner_pass_helper(self):
        # helper 自身不拦 viewer；viewer 被 require_role(admin/evaluator) 挡在 API 层
        check_owner_denies_golden(5, self._user(7, "viewer"))


class TestValidateUpload(unittest.TestCase):
    def test_allowed_ext_pass(self):
        self.assertIsNone(validate_upload(".pdf", b"%PDF-1.7 data", 10 * 1024 * 1024))

    def test_reject_unknown_ext(self):
        code, _ = validate_upload(".exe", b"MZ", 10 * 1024 * 1024)
        self.assertEqual(code, E_FILE_TYPE)

    def test_reject_no_ext(self):
        code, _ = validate_upload("", b"data", 10 * 1024 * 1024)
        self.assertEqual(code, E_FILE_TYPE)

    def test_reject_oversize(self):
        code, _ = validate_upload(".txt", b"x" * 11 * 1024 * 1024, 10 * 1024 * 1024)
        self.assertEqual(code, E_FILE_SIZE)

    def test_pdf_magic_mismatch(self):
        code, _ = validate_upload(".pdf", b"not a pdf at all", 10 * 1024 * 1024)
        self.assertEqual(code, E_FILE_TYPE)

    def test_uppercase_ext_not_allowed(self):
        # 扩展名在 handler 层已 lower；白名单不含大写
        code, _ = validate_upload(".PDF", b"%PDF-1.7", 10 * 1024 * 1024)
        self.assertEqual(code, E_FILE_TYPE)

    def test_default_max_mb_sane(self):
        self.assertEqual(DEFAULT_MAX_MB, 50.0)

    # ---- 7.6 A7 二进制魔数补齐（docx/xlsx zip、doc/xls OLE2；改名换后缀防逃逸） ----
    def test_docx_zip_magic_pass(self):
        self.assertIsNone(validate_upload(".docx", b"PK\x03\x04zip-body", 1024 * 1024))

    def test_xlsx_zip_magic_pass(self):
        self.assertIsNone(validate_upload(".xlsx", b"PK\x03\x04zip-body", 1024 * 1024))

    def test_doc_ole2_magic_pass(self):
        self.assertIsNone(validate_upload(".doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest", 1024 * 1024))

    def test_xls_ole2_magic_pass(self):
        self.assertIsNone(validate_upload(".xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest", 1024 * 1024))

    def test_docx_magic_mismatch_rejected(self):
        # 改名换后缀：内容是文本却声称 .docx → 魔数不匹配拒绝
        code, _ = validate_upload(".docx", b"plain text pretending docx", 1024 * 1024)
        self.assertEqual(code, E_FILE_TYPE)

    def test_doc_magic_mismatch_rejected(self):
        code, _ = validate_upload(".doc", b"PK\x03\x04not-ole2", 1024 * 1024)
        self.assertEqual(code, E_FILE_TYPE)

    def test_xlsx_magic_mismatch_rejected(self):
        code, _ = validate_upload(".xlsx", b"%PDF-fake", 1024 * 1024)
        self.assertEqual(code, E_FILE_TYPE)

    def test_text_ext_no_magic(self):
        # 文本类 txt/csv/json 无强魔数，宽松放行（task.md 验收口径）
        self.assertIsNone(validate_upload(".txt", b"hello", 1024 * 1024))
        self.assertIsNone(validate_upload(".csv", b"a,b,c\n1,2,3", 1024 * 1024))
        self.assertIsNone(validate_upload(".json", b'{"a": 1}', 1024 * 1024))


if __name__ == "__main__":
    unittest.main()
