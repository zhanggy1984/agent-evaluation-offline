"""ConfigEngine 通用能力单元测试：multipart files + 声明式 poll 轮询（B.5 cc 收尾）。

核心逻辑（CLAUDE.md）：prepare 步骤的执行分支（普通/multipart/poll）是引擎核心，
用 unittest + fake client 喂模拟响应，不触真实网络、不读真实文件（multipart 用例除外）。
"""
from asyncio_util import run_in_isolated_loop
import json
import os
import shutil
import tempfile
import types
import unittest

import app.adapters.base as base
from app.adapters.base import RequestSpec, request_kwargs
from app.adapters.engine import ConfigEngine, _poll_hit, render_template


class _FakeAgent:
    def __init__(self, base_url="http://agent.local"):
        self.base_url = base_url
        self.id = 1  # reset() 日志引用 agent.id（生产 Agent 行锁/追溯）


class _FakeInterface:
    contract_type = "sync"
    path = "/v1/chat"
    method = "POST"


class _SeqClient:
    """按调用顺序弹出预置响应的 fake httpx.AsyncClient。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[tuple] = []

    async def request(self, method, url, headers=None, json=None, timeout=None, **kw):
        self.calls.append((method, url, {**({"json": json} if json is not None else {}), **kw}))
        if not self._responses:
            raise AssertionError("fake client 响应耗尽（请求数超出预期）")
        return self._responses.pop(0)


def _run(coro):
    return run_in_isolated_loop(coro)


class _UploadsTestCase(unittest.TestCase):
    """multipart 测试基类：模拟 uploads 目录。

    P0 安全收敛后 multipart 路径限定 uploads 目录内（adapters.base 白名单校验），
    测试文件统一建在临时 uploads 目录下并同步 _UPLOADS_DIR。
    """

    def setUp(self):
        self._uploads = tempfile.mkdtemp(prefix="uploads_tmp_")
        self._orig_uploads_dir = base._UPLOADS_DIR
        base._UPLOADS_DIR = os.path.realpath(self._uploads)

    def tearDown(self):
        base._UPLOADS_DIR = self._orig_uploads_dir
        shutil.rmtree(self._uploads, ignore_errors=True)

    def upload_file(self, suffix=".pdf", content=b"%PDF-1.4"):
        """在模拟 uploads 目录内建文件，返回路径（唯一名，避免同目录多次建文件撞名）。"""
        path = os.path.join(self._uploads, f"case{len(os.listdir(self._uploads))}{suffix}")
        with open(path, "wb") as f:
            f.write(content)
        return path


class TestPollHit(unittest.TestCase):
    """until 条件判定（值可单值或 list，key 支持点号路径）。"""

    def test_single_value(self):
        self.assertTrue(_poll_hit({"status": "SUCCESS"}, {"status": "SUCCESS"}))
        self.assertFalse(_poll_hit({"status": "RUNNING"}, {"status": "SUCCESS"}))

    def test_list_value(self):
        data = {"status": "WAITING_REVIEW"}
        self.assertTrue(_poll_hit(data, {"status": ["SUCCESS", "WAITING_REVIEW"]}))
        self.assertFalse(_poll_hit({"status": "PENDING"}, {"status": ["SUCCESS", "WAITING_REVIEW"]}))

    def test_nested_path(self):
        self.assertTrue(_poll_hit({"data": {"status": "done"}}, {"data.status": "done"}))
        self.assertFalse(_poll_hit({"data": {"status": "x"}}, {"data.status": "done"}))

    def test_missing_path(self):
        self.assertFalse(_poll_hit({"status": "x"}, {"data.status": "done"}))

    def test_empty_until_is_true(self):
        self.assertTrue(_poll_hit({}, {}))


class TestPreparePoll(unittest.TestCase):
    def _engine(self, timeout=5.0):
        cfg = {
            "prepare": [{
                "name": "wait", "poll": {
                    "path": "/api/tasks/9",
                    "until": {"status": ["SUCCESS", "FAILED"]},
                    "interval": 0.01, "timeout": timeout,
                }}],
        }
        return ConfigEngine(_FakeAgent(), _FakeInterface(), cfg, {})

    def test_poll_hits_terminal(self):
        eng = self._engine()
        client = _SeqClient([
            types.SimpleNamespace(status_code=200, json=lambda: {"status": "PENDING", "progress": 10}),
            types.SimpleNamespace(status_code=200, json=lambda: {"status": "RUNNING", "progress": 50}),
            types.SimpleNamespace(status_code=200, json=lambda: {"status": "SUCCESS", "progress": 100}),
        ])
        _run(eng.prepare(None, client))
        self.assertEqual(eng._prepare_ctx["wait"]["status"], "SUCCESS")
        self.assertEqual(len(client.calls), 3)  # PENDING → RUNNING → SUCCESS 各一次
        self.assertTrue(eng._prepared)

    def test_poll_extract(self):
        cfg = {"prepare": [{"name": "wait", "poll": {
            "path": "/api/tasks/9", "until": {"status": "SUCCESS"}, "interval": 0.01,
            "extract": {"id": "id"}}}]}
        eng = ConfigEngine(_FakeAgent(), _FakeInterface(), cfg, {})
        client = _SeqClient([
            types.SimpleNamespace(status_code=200,
                                  json=lambda: {"status": "SUCCESS", "id": 9})])
        _run(eng.prepare(None, client))
        self.assertEqual(eng._prepare_ctx["wait"]["id"], 9)

    def test_poll_timeout(self):
        eng = self._engine(timeout=0.02)
        client = _SeqClient([
            types.SimpleNamespace(status_code=200, json=lambda: {"status": "PENDING"})
            for _ in range(50)])
        with self.assertRaisesRegex(RuntimeError, "轮询超时"):
            _run(eng.prepare(None, client))

    def test_poll_http_error_immediate(self):
        eng = self._engine()
        client = _SeqClient([types.SimpleNamespace(status_code=500,
                                                   text="boom",
                                                   json=lambda: {})])
        with self.assertRaisesRegex(RuntimeError, "轮询 HTTP 500"):
            _run(eng.prepare(None, client))


class TestPrepareMultipart(_UploadsTestCase):
    def _upload_cfg(self, file_path_var="{case.input.file_path}"):
        return {
            "prepare": [{
                "name": "upload", "method": "POST", "path": "/api/files/upload",
                "files": {"file": file_path_var},
                "data": {"type": "contract"},
                "extract": {"task_id": "task_id"},
            }],
        }

    def test_files_sent_via_multipart(self):
        path = self.upload_file(content=b"%PDF-1.4 fake contract")
        case = types.SimpleNamespace(input={"file_path": path}, input_turns=None, expected={})
        eng = ConfigEngine(_FakeAgent(), _FakeInterface(), self._upload_cfg(), {})
        client = _SeqClient([types.SimpleNamespace(status_code=200, json=lambda: {"task_id": 42})])
        _run(eng.prepare(case, client))
        method, url, kw = client.calls[0]
        self.assertEqual((method, url), ("POST", "http://agent.local/api/files/upload"))
        self.assertIn("files", kw)      # multipart 走 files（非 json）
        self.assertNotIn("json", kw)
        self.assertEqual(kw["data"], {"type": "contract"})
        # files 值 = (文件名, 字节, mime)
        self.assertEqual(kw["files"]["file"][0], os.path.basename(path))
        self.assertEqual(kw["files"]["file"][1], b"%PDF-1.4 fake contract")
        self.assertEqual(eng._prepare_ctx["upload"]["task_id"], 42)

    def test_prepare_can_reference_case_input(self):
        """prepare 步骤可引用 {case.*}（文件型上传的基础）。"""
        path = self.upload_file()
        case = types.SimpleNamespace(input={"file_path": path}, input_turns=None, expected={})
        eng = ConfigEngine(_FakeAgent(), _FakeInterface(), self._upload_cfg(), {})
        client = _SeqClient([types.SimpleNamespace(status_code=200, json=lambda: {"task_id": 1})])
        _run(eng.prepare(case, client))
        method, url, kw = client.calls[0]
        self.assertEqual(kw["files"]["file"][0], os.path.basename(path))


class TestRequestKwargs(_UploadsTestCase):
    def test_json_mode(self):
        spec = RequestSpec("POST", "u", {}, json={"q": "hi"})
        kw = request_kwargs(spec)
        self.assertEqual(kw["json"], {"q": "hi"})
        self.assertNotIn("files", kw)

    def test_multipart_mode(self):
        path = self.upload_file(suffix=".txt", content=b"abc")
        spec = RequestSpec("POST", "u", {}, files={"f": path}, data={"k": "v"})
        kw = request_kwargs(spec)
        self.assertIn("files", kw)
        self.assertNotIn("json", kw)
        self.assertEqual(kw["data"], {"k": "v"})
        self.assertEqual(kw["files"]["f"][1], b"abc")


class TestUploadsPathGuard(_UploadsTestCase):
    """P0-4 负向用例：uploads 内放行，穿越 / 越界绝对路径 / 软链逃逸一律拒绝。"""

    def _assert_rejected(self, path: str):
        with self.assertRaisesRegex(ValueError, "不在 uploads 目录内"):
            base._assert_inside_uploads(path)

    def test_accepts_inside_uploads(self):
        path = self.upload_file()
        self.assertEqual(base._assert_inside_uploads(path), os.path.realpath(path))

    def test_rejects_parent_dir_traversal(self):
        # ../ 穿越：realpath 落到 uploads 外 → 拒绝
        self._assert_rejected(os.path.join(self._uploads, "..", "escape.txt"))

    def test_rejects_absolute_path_outside_uploads(self):
        outside = tempfile.mkdtemp(prefix="outside_tmp_")
        try:
            self._assert_rejected(os.path.join(outside, "secret.txt"))
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_rejects_symlink_escape(self):
        # 软链逃逸：uploads 内链接指向外部文件 → realpath 解析到外部 → 拒绝
        outside = tempfile.mkdtemp(prefix="outside_tmp_")
        try:
            target = os.path.join(outside, "secret.txt")
            with open(target, "w", encoding="utf-8") as f:
                f.write("secret")
            link = os.path.join(self._uploads, "link.txt")
            try:
                os.symlink(target, link)
            except (OSError, NotImplementedError):
                self.skipTest("当前无创建 symlink 权限（Windows 需管理员）")
            self._assert_rejected(link)
        finally:
            shutil.rmtree(outside, ignore_errors=True)


class TestRenderFilesTemplate(unittest.TestCase):
    def test_file_path_variable_renders(self):
        ctx = {"case": {"input": {"file_path": "/app/uploads/a.pdf"}}}
        self.assertEqual(render_template("{case.input.file_path}", ctx), "/app/uploads/a.pdf")


class TestResetSeed(unittest.TestCase):
    """7.6 B1/B6 reset(seed)：seed 注入 body、data_id 点号提取、产物留底、无段返回 None。"""

    def _case(self, seed="abc123"):
        return types.SimpleNamespace(input={"seed": seed}, input_turns=None, expected={})

    def _cfg(self, **over):
        cfg = {
            "reset": {"path": "/admin/reset", "seed_field": "seed",
                      "body": {"agent_id": "demo-1"}, "extract_data_id": "data.data_id"},
        }
        cfg["reset"].update(over)
        return cfg

    def test_reset_injects_seed_and_returns_data_id(self):
        eng = ConfigEngine(_FakeAgent(), _FakeInterface(), self._cfg(), {})
        client = _SeqClient([types.SimpleNamespace(
            status_code=200, json=lambda: {"code": 0, "data": {"data_id": "RID-77"}})])
        data_id = _run(eng.reset(self._case(), client))
        self.assertEqual(data_id, "RID-77")
        method, url, kw = client.calls[0]
        self.assertEqual((method, url), ("POST", "http://agent.local/admin/reset"))
        # seed_field 注入当前 case 的 seed（覆盖 body 已有键）
        self.assertEqual(kw["json"]["seed"], "abc123")
        self.assertEqual(kw["json"]["agent_id"], "demo-1")
        # 产物留底供 {reset.data_id}/{reset.resp} 模板引用
        self.assertEqual(eng._reset_ctx["data_id"], "RID-77")
        self.assertEqual(eng._reset_ctx["resp"]["data"]["data_id"], "RID-77")

    def test_reset_no_seed_field_defaults_to_seed(self):
        # 无 seed_field 与 extract_data_id → 各自取缺省 "seed" / "data_id"
        eng = ConfigEngine(_FakeAgent(), _FakeInterface(),
                           {"reset": {"path": "/admin/reset", "body": {}}}, {})
        client = _SeqClient([types.SimpleNamespace(status_code=200, json=lambda: {"data_id": 9})])
        self.assertEqual(_run(eng.reset(self._case(), client)), "9")  # 非字符串强转 str
        self.assertEqual(client.calls[0][2]["json"]["seed"], "abc123")

    def test_reset_extract_nested_path(self):
        eng = ConfigEngine(_FakeAgent(), _FakeInterface(),
                           self._cfg(extract_data_id="result.items.0.review_id"), {})
        client = _SeqClient([types.SimpleNamespace(status_code=200, json=lambda: {
            "result": {"items": [{"review_id": "REV-1"}]}})])
        self.assertEqual(_run(eng.reset(self._case(), client)), "REV-1")

    def test_reset_no_cfg_returns_none(self):
        eng = ConfigEngine(_FakeAgent(), _FakeInterface(), {}, {})
        self.assertIsNone(_run(eng.reset(self._case(), client=_SeqClient([]))))
        self.assertEqual(eng._reset_ctx, {})  # 未 reset 保持空

    def test_reset_http_error_raises(self):
        eng = ConfigEngine(_FakeAgent(), _FakeInterface(), self._cfg(), {})
        client = _SeqClient([types.SimpleNamespace(status_code=500, text="boom", json=lambda: {})])
        with self.assertRaisesRegex(RuntimeError, "reset HTTP 500"):
            _run(eng.reset(self._case(), client))

    def test_reset_missing_data_id_raises(self):
        eng = ConfigEngine(_FakeAgent(), _FakeInterface(),
                           self._cfg(extract_data_id="data.data_id"), {})
        client = _SeqClient([types.SimpleNamespace(status_code=200, json=lambda: {"data": {}})])
        with self.assertRaisesRegex(RuntimeError, "缺失 data_id"):
            _run(eng.reset(self._case(), client))

    def test_reset_data_id_available_in_request_context(self):
        """reset 产物可被 build_request 的 {reset.data_id} 模板引用。"""
        eng = ConfigEngine(_FakeAgent(), _FakeInterface(), {
            "reset": {"path": "/admin/reset"},
            "request": {"path": "/v1/chat", "method": "POST",
                        "body": {"query": "q", "doc_id": "{reset.data_id}"}},
        }, {})
        client = _SeqClient([types.SimpleNamespace(status_code=200, json=lambda: {"data_id": 42})])
        _run(eng.reset(self._case(), client))
        spec = eng.build_request(self._case())
        self.assertEqual(spec.json["doc_id"], "42")


if __name__ == "__main__":
    unittest.main()
