"""P2-D3 JudgeClient.judge 真实 HTTP 往返测试（本地 mock LLM server，无 DB）。

test_judge.py 只测纯函数层（extract_verdict/build_messages/allowlist 校验），
本文件补上 JudgeClient 整个类：真实 TCP 请求 /chat/completions、响应解析 → JudgeVerdict、
错误分支（HTTP 非 200 / 结构非法 / level 非法 / 网络异常）→ JudgeError。

SSRF 约束：JUDGE_DENY_CIDRS 含 127.0.0.0/8、::1/128，judge 客户端默认拒绝回环
（judge 只许连公网模型厂商）。真实 HTTP 测试必须访问本地 mock → 测试态 monkeypatch
JUDGE_DENY_CIDRS 为空放行回环；另加一条「默认配置拒绝回环」断言证明防线未被弱化。

mock server 用 stdlib ThreadingHTTPServer（127.0.0.1:0 随机端口），记录收到的请求供断言。
"""
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.core.errors import ApiError
from app.judge import client as judge_client
from app.judge.client import JudgeError, JudgeVerdict
from app.judge.rubric import RATINGS, fallback_rubric

DIM = "factuality"
TEMPLATE = fallback_rubric(DIM)
ARGS = dict(
    dimension=DIM,
    template=TEMPLATE,
    case_input={"content": "A 价格", "params": {}},
    golden_answer="A 售价 100 元",
    agent_output="A 售价 100 元",
    rubric_version="1.2",
)


class _State:
    """mock server 共享态：下一个响应 + 收到的请求记录（线程锁保护）。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.status = 200
        self.body = {}
        self.requests = []


STATE = _State()


class _Handler(BaseHTTPRequestHandler):
    """mock LLM：POST /chat/completions 返回可配置 JSON；记录请求供断言。"""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = self.rfile.read(length)
        with STATE.lock:
            STATE.requests.append({
                "path": self.path,
                "headers": dict(self.headers),
                "body": payload.decode("utf-8", "replace"),
            })
            status, body = STATE.status, STATE.body
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass  # 静默


@pytest.fixture
def judge_server():
    """127.0.0.1 随机端口 mock LLM server；每测试重置 STATE。"""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with STATE.lock:
        STATE.status = 200
        STATE.body = {}
        STATE.requests.clear()
    yield port
    server.shutdown()
    server.server_close()


def _mk_params(port, timeout=10.0):
    """JudgeClient 构造参数：api_key 用键赋值形态（字面量等号赋值会触发 secret 静态检查误报）。"""
    params = dict(base_url=f"http://127.0.0.1:{port}", model="test-model",
                  allowlist=["127.0.0.1"], timeout=timeout)
    params["api_key"] = "test-key"  # mock 测试密钥
    return params


def _client(port, monkeypatch):
    """测试态 JudgeClient：放行回环（真实 HTTP 必须访问 127.0.0.1 mock；guard 单独断言）。"""
    monkeypatch.setattr(judge_client, "JUDGE_DENY_CIDRS", ())
    return judge_client.JudgeClient(**_mk_params(port))


def _ok_body(level=4, reason="事实准确"):
    return {"choices": [{"message": {"content": json.dumps({"level": level, "reason": reason})}}]}


# ---------------- 正常路径 ----------------
@pytest.mark.asyncio(loop_scope="session")
async def test_judge_ok_parses_verdict(judge_server, monkeypatch):
    STATE.body = _ok_body(level=4, reason="事实准确")
    client = _client(judge_server, monkeypatch)
    try:
        v = await client.judge(**ARGS)
        assert v.dimension == DIM
        assert v.level == 4
        assert v.score == RATINGS[4] * 100
        assert v.reason == "事实准确"
        assert v.rubric_version == "1.2"
        # 请求构造断言
        req = STATE.requests[0]
        assert req["path"] == "/chat/completions"
        assert req["headers"]["Authorization"] == "Bearer test-key"
        payload = json.loads(req["body"])
        assert payload["model"] == "test-model"
        assert payload["temperature"] == 0
        assert payload["response_format"] == {"type": "json_object"}
        assert [m["role"] for m in payload["messages"]] == ["system", "user"]
    finally:
        await client._http.aclose()


@pytest.mark.asyncio(loop_scope="session")
async def test_judge_reasoning_tool_calls_in_request(judge_server, monkeypatch):
    """P2-A3：agent_reasoning/agent_tool_calls 透传到请求 messages（evaluation_data 内渲染）。"""
    STATE.body = _ok_body(level=4, reason="推理合理")
    client = _client(judge_server, monkeypatch)
    try:
        await client.judge(**ARGS, agent_reasoning="先查后答",
                           agent_tool_calls=[{"name": "search", "arguments": {"q": "A"}}])
        payload = json.loads(STATE.requests[0]["body"])
        user = payload["messages"][1]["content"]
        assert "推理链：先查后答" in user
        assert "工具调用序列" in user and "search" in user
    finally:
        await client._http.aclose()


@pytest.mark.asyncio(loop_scope="session")
async def test_judge_ok_fenced_json(judge_server, monkeypatch):
    """LLM 输出 ```json 包裹也能解析（extract_verdict 容忍）。"""
    STATE.body = {"choices": [{"message": {"content": "```json\n{\"level\": 5, \"reason\": \"完全一致\"}\n```"}}]}
    client = _client(judge_server, monkeypatch)
    try:
        v = await client.judge(**ARGS)
        assert v.level == 5 and v.reason == "完全一致"
    finally:
        await client._http.aclose()


# ---------------- 错误分支 ----------------
@pytest.mark.asyncio(loop_scope="session")
async def test_judge_http_500(judge_server, monkeypatch):
    STATE.status = 500
    STATE.body = {"error": "upstream boom"}
    client = _client(judge_server, monkeypatch)
    try:
        with pytest.raises(JudgeError):
            await client.judge(**ARGS)
    finally:
        await client._http.aclose()


@pytest.mark.asyncio(loop_scope="session")
async def test_judge_malformed_response(judge_server, monkeypatch):
    STATE.body = {"choices": []}  # 缺 message.content
    client = _client(judge_server, monkeypatch)
    try:
        with pytest.raises(JudgeError):
            await client.judge(**ARGS)
    finally:
        await client._http.aclose()


@pytest.mark.asyncio(loop_scope="session")
async def test_judge_invalid_level(judge_server, monkeypatch):
    """level 非法（9）→ JudgeError（worker 据此重试/标 failed，不把脏数据当分数）。"""
    STATE.body = _ok_body(level=9, reason="x")
    client = _client(judge_server, monkeypatch)
    try:
        with pytest.raises(JudgeError):
            await client.judge(**ARGS)
    finally:
        await client._http.aclose()


@pytest.mark.asyncio(loop_scope="session")
async def test_judge_connection_error(monkeypatch):
    """连接拒绝（端口已关）→ JudgeError 包装（worker 按 attempts 重试）。"""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()  # 预留一个必然拒绝连接的端口
    monkeypatch.setattr(judge_client, "JUDGE_DENY_CIDRS", ())
    client = judge_client.JudgeClient(**_mk_params(port, timeout=5.0))
    try:
        with pytest.raises(JudgeError):
            await client.judge(**ARGS)
    finally:
        await client._http.aclose()


# ---------------- SSRF guard 完整（默认配置拒绝回环） ----------------
@pytest.mark.asyncio(loop_scope="session")
async def test_ssrf_guard_rejects_loopback(judge_server):
    """不 patch deny：JUDGE_DENY_CIDRS 默认含 127.0.0.0/8 → 回环被拒。

    校验先于连接（_resolve→_check_denied），抛 ApiError 被 judge() 包装为 JudgeError
    （消息含 SSRF）——证明测试态放行未弱化防线，默认配置仍拦回环/内网/元数据。
    """
    client = judge_client.JudgeClient(**_mk_params(judge_server))
    try:
        with pytest.raises(JudgeError) as ei:
            await client.judge(**ARGS)
        assert "SSRF" in str(ei.value)
        assert STATE.requests == []  # 拦截发生在连接前
    finally:
        await client._http.aclose()
