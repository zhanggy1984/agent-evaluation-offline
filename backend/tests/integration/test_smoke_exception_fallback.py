"""异常兜底专项冒烟（真库端到端，P0-1 / P0-2 收敛路径实证）。

单测已覆盖分类逻辑；本文件验证两条「端到端收敛」路径在真实 worker/orchestrator +
真库下成立：

- P0-1 judge 配置错：_drain_once 一轮 → pending 任务**快速 failed**（非卡 processing
  死循环）+ 显式 score_run 触发，run 从 scoring 收敛到终态（不再永久卡 scoring）。
- P0-2 agent prepare 5xx：归类可重试 http_error + _call_once 指数退避重试（mock
  统计 login 请求次数证明重试发生），重试耗尽后 case 标技术失败（非 contract/agent
  bug），run 收敛 partial_failed，且不误走 probe_failed 拦截。

真库 + 真实 worker/orchestrator + 可控 mock 上游（本地 http.server，SSRF 默认
白名单 127/8 放行）。
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("aiomysql")

from sqlalchemy import select

from pytest_asyncio import fixture as async_fixture

from app.core.db import SessionLocal
from app.judge import worker
from app.models import CaseVersion, EvalResult, EvalRun, JudgeTask, SystemConfig
from app.runner.orchestrator import orchestrator
from helpers import create_chain, make_run


# ---------------- mock agent 上游（冒烟②） ----------------
def _sse_bytes(*frames) -> bytes:
    out = b""
    for typ, data, eid in frames:
        out += f"event: {typ}\nid: {eid}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()
    return out


# 探测需标准 SSE 契约（usage/done 必选）才通过；执行段因 prepare 5xx 到不了 chat
_SSE_BYTES = _sse_bytes(
    ("answer", {"delta": "你好"}, "a1"),
    ("usage", {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}, "u1"),
    ("done", {"ts": 1.0}, "d1"),
)


class _Handler(BaseHTTPRequestHandler):
    login_hits = 0  # POST /api/auth/login 累计次数（冒烟② 断言重试用）

    def log_message(self, *args):  # 静音 access log
        pass

    def _read_body(self) -> None:
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)

    def _send_json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        self._read_body()
        if self.path == "/api/auth/login":
            _Handler.login_hits += 1
            # 第 1 次成功（probe 用）；之后 5xx（执行段初始 + 重试都撞 500）
            if _Handler.login_hits == 1:
                self._send_json(200, {"ok": True})
            else:
                self._send_json(500, {"error": "internal boom"})
        elif self.path == "/v1/chat":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(_SSE_BYTES)))
            self.end_headers()
            self.wfile.write(_SSE_BYTES)
        else:
            self._send_json(404, {"error": "not found"})


class _AgentMockServer:
    def __init__(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.httpd.server_address[1]

    def start(self) -> None:
        _Handler.login_hits = 0
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture()
def agent_mock_server():
    srv = _AgentMockServer()
    srv.start()
    yield srv
    srv.stop()


@async_fixture
async def judge_cfg_backup():
    """judge system_config 三行现场保存/恢复（全局配置不污染其他测试）。"""
    keys = ["judge_llm.base_url", "judge_llm.model_name", "llm_allowlist"]
    saved = {}
    async with SessionLocal() as db:
        for k in keys:
            row = await db.get(SystemConfig, k)
            saved[k] = (row is not None, row.value if row else None)
    yield
    async with SessionLocal() as db:
        for k, (exists, val) in saved.items():
            row = await db.get(SystemConfig, k)
            if exists:
                if row is not None:
                    row.value = val
            elif row is not None:
                await db.delete(row)
        await db.commit()


async def _set_judge_cfg(db, *, base_url: str, model: str, allowlist: list) -> None:
    for k, v in [("judge_llm.base_url", base_url), ("judge_llm.model_name", model),
                 ("llm_allowlist", allowlist)]:
        row = await db.get(SystemConfig, k)
        if row is not None:
            row.value = v
        else:
            db.add(SystemConfig(key=k, value=v))
    await db.commit()


# ---------------- 冒烟① P0-1：judge 配置错 → 快速 failed + score_run 收敛 ----------------
@pytest.mark.asyncio(loop_scope="session")
async def test_judge_allowlist_config_error_fast_fails_and_converges(env, db, judge_cfg_backup):
    # scoring run + 每 case 的 case_version/eval_result + pending judge task
    ch = await create_chain(db, case_names=["smoke-judge"])
    run = make_run(ch["agent"].id, ch["suite"].id)
    run.status = "scoring"
    db.add(run)
    await db.flush()
    cv = CaseVersion(
        case_id=ch["cases"][0].id, version_no=1, content_hash="0" * 64,
        snapshot={"input": ch["cases"][0].input, "expected": {},
                  "metrics": {"completeness": {"enabled": True}}})
    db.add(cv)
    await db.flush()
    db.add(EvalResult(run_id=run.id, case_id=ch["cases"][0].id, case_version_id=cv.id,
                      pass_fail="pass", answer="冒烟答案"))
    task = JudgeTask(run_id=run.id, case_id=ch["cases"][0].id,
                     dimension_code="factuality", status="pending")
    db.add(task)
    await db.commit()
    env.agents.append(ch["agent"]); env.interfaces.append(ch["iface"])
    env.suites.append(ch["suite"]); env.cases.extend(ch["cases"]); env.runs.append(run)
    run_id = run.id
    case_id = ch["cases"][0].id

    # 配置错：judge base_url host 不在 llm_allowlist（P0-1 触发条件）
    async with SessionLocal() as db:
        await _set_judge_cfg(db, base_url="http://llm-invalid.example/v1",
                             model="fake-model", allowlist=["llm-ok.example"])

    await worker._drain_once()

    async with SessionLocal() as db:
        # JudgeTask 复合主键 (run_id, case_id, dimension_code)，无单列 id
        t = await db.get(JudgeTask, (run_id, case_id, "factuality"))
        r = await db.get(EvalRun, run_id)
    # 快速 failed（非卡 processing/pending 死循环），租约清空
    assert t.status == "failed"
    assert t.claim_id is None and t.lease_until is None
    # score_run 被 P0-1 分支显式触发 → run 从 scoring 收敛到终态（非卡死）
    assert r.status in ("completed", "partial_failed", "scoring_failed")
    assert r.status != "scoring"


# ---------------- 冒烟② P0-2：agent prepare 5xx → 重试 → 技术失败收敛 ----------------
@pytest.mark.asyncio(loop_scope="session")
async def test_prepare_5xx_retries_then_http_error(env, db, agent_mock_server):
    cfg = {
        "contract_type": "sse",
        "timeout": 5,
        "prepare": [{"name": "login", "method": "POST", "path": "/api/auth/login",
                     "headers": {"Content-Type": "application/json"},
                     "body": {"user": "u", "pass": "p"}}],
        "request": {"path": "/v1/chat", "method": "POST",
                    "headers": {}, "body": {"q": "hi"}},
    }
    ch = await create_chain(db, case_names=["smoke-5xx"])
    ch["agent"].base_url = f"http://127.0.0.1:{agent_mock_server.port}"
    ch["agent"].adapter_config = cfg
    run = make_run(ch["agent"].id, ch["suite"].id, run_config={
        "case_timeout": 5, "max_retries": 1, "run_timeout": 300,
        "breaker_failure_threshold": 100})
    db.add(run)
    await db.commit()
    env.agents.append(ch["agent"]); env.interfaces.append(ch["iface"])
    env.suites.append(ch["suite"]); env.cases.extend(ch["cases"]); env.runs.append(run)
    run_id = run.id

    await orchestrator.start_run(run_id)

    async with SessionLocal() as db:
        r = await db.get(EvalRun, run_id)
        res = (await db.execute(select(EvalResult).where(
            EvalResult.run_id == run_id))).scalars().first()
    # 收敛：1 case error → partial_failed；probe 通过（未误走 probe_failed 拦截）
    assert r.status == "partial_failed"
    assert r.run_config.get("fail_reason") != "probe_failed"
    # P0-2 分流：5xx 是可重试技术失败（http_error），不是 contract/agent bug
    assert res is not None and res.pass_fail == "error"
    assert res.error_type == "http_error"
    assert "prepare.login" in res.error_detail and "500" in res.error_detail
    # 重试证据：probe 1 次 login(200) + 执行段首败 1 次(500) + _call_once 重试 1 次(500)
    assert _Handler.login_hits >= 3
