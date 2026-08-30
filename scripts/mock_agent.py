#!/usr/bin/env python3
"""本地 mock agent（§5.1 SSE 强契约），用于跑通评测流程、冻结契约。

模拟 customer-service 类 SSE 变体：meta → reasoning → tool_call → answer×n → usage → done。
宿主机启动：python scripts/mock_agent.py [port]
容器内访问：http://host.docker.internal:PORT/v1/chat
仅标准库，无外部依赖。
"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9000


def now_ts() -> int:
    return int(time.time() * 1000)


# Q6：v2 manifest 标准端点（GET /api/contracts），供 verify_agent.py / wizard 自测消费。
# 仿 customer-service 简化版：SSE、无 prepare、input 域仅 content。
MANIFEST = {
    "agent": "mock", "contract_version": "2.0",
    "interfaces": [
        {"name": "chat", "path": "/v1/chat", "method": "POST",
         "contract_type": "sse", "llm": True, "description": "对话评测接口（mock SSE）"},
    ],
    "scenes": [{"tag": "greeting", "description": "问候与闲聊"}],
    "contract": {
        "type": "sse", "timeout": 120,
        "request": {"path": "/v1/chat", "method": "POST",
                    "body": {"query": "{{input.content}}"}},
    },
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/api/contracts":
            self.send_error(404)
            return
        body = json.dumps(MANIFEST, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/v1/chat":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            req = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            req = {"query": raw.decode("utf-8", errors="replace")}
        query = str(req.get("query", ""))

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def emit(event: str, data: dict) -> None:
            payload = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")
            self.wfile.write(payload)
            self.wfile.flush()

        emit("meta", {"agent": "mock", "model": "mock-model", "interface": "/v1/chat",
                      "contract_version": "2.0", "git_sha": "mock-sha", "knowledge_version": "kv-1",
                      "ts": now_ts()})
        time.sleep(0.1)
        # §3.3：reasoning/answer 双字段（content+delta）并存，单帧流式相等
        emit("reasoning", {"content": "先分析用户问题，检索相关文档。",
                           "delta": "先分析用户问题，检索相关文档。", "ts": now_ts()})
        emit("tool_call", {"id": "t-1", "name": "search", "args": {"q": query},
                           "result": {"hits": ["doc-1", "doc-2"]}, "status": "ok", "ts": now_ts()})
        emit("answer", {"content": "你好，", "delta": "你好，", "ts": now_ts()})
        time.sleep(0.05)
        emit("answer", {"content": "这是 mock agent 的最终回答。",
                        "delta": "这是 mock agent 的最终回答。", "ts": now_ts()})
        emit("usage", {"prompt_tokens": 12, "completion_tokens": 9,
                       "total_tokens": 21, "ts": now_ts()})
        emit("done", {"ts": now_ts()})

    def log_message(self, *args):  # 静默访问日志
        pass


if __name__ == "__main__":
    print(f"mock agent SSE 服务启动: http://localhost:{PORT}/v1/chat")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
