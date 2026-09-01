"""通用配置引擎（配置型 adapter 解释器，§15.6 / §20）。

adapter_config 以声明式 JSON 描述「请求构造 + 响应解析」，本引擎解释执行。
schema：
{
  "contract_type": "sse" | "sync",
  "timeout": 120,
  "prepare": [ ... ],                       # 可选前置步骤：普通 HTTP / multipart 上传 / 轮询
  "reset": {"path": "/admin/reset", ...},   # 可选：seed 重置（POST 返回 data_id，{reset.data_id} 域）
  "request": {
    "path": "/v1/chat",                     # 相对 agent.base_url
    "method": "POST",
    "headers": {"Authorization": "Bearer {auth.token}"},
    "body": {"query": "{case.input}", "stream": true},
    "files": {"file": "{case.input.file_path}"},  # 可选：multipart 文件上传（值=平台容器内路径）
    "data": {"type": "contract"}                  # 可选：multipart 文本字段
  },
  "sse": {"field_map": {"agent_event": "unified_event"}},  # 默认身份（强契约）
  "sync": {"answer": "answer", "reasoning": "reasoning",
           "tool_calls": "tool_calls", "usage": "usage", "meta": "meta"}
}
变量域：{case.input} {case.input_turns} {case.expected.*} {auth.*} {interface.path}
路径段支持 dict 键与 list 下标（如 items.0.review_id）。
整串命中且值为 dict/list 时原样替换；否则字符串插值。
"""
import asyncio
import logging
import re
import time
from typing import Any

from app.adapters.base import AdapterHTTPError, AgentAdapter, RequestSpec, send_request
from app.core.http import build_agent_client
from app.core.sse_parser import SSEEvent, SSEParser

logger = logging.getLogger(__name__)

_VAR = re.compile(r"\{([a-zA-Z0-9_.]+)\}")


def _get_path(ctx: dict, path: str) -> Any:
    cur = ctx
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            raise KeyError(path)
    return cur


def _poll_hit(data: dict, until: dict) -> bool:
    """until 条件是否全部命中（值允许单值或 list，key 支持点号路径）。

    until 为空视为恒真；任一路径缺失或值不匹配即返回 False。
    """
    for path, allowed in until.items():
        try:
            val = _get_path(data, path)
        except KeyError:
            return False
        if isinstance(allowed, list):
            if val not in allowed:
                return False
        elif val != allowed:
            return False
    return True


def render_template(value: Any, ctx: dict) -> Any:
    """递归渲染 {var} 占位；整串命中且值为结构化对象时原样替换。"""
    if isinstance(value, dict):
        return {k: render_template(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [render_template(v, ctx) for v in value]
    if isinstance(value, str):
        m = _VAR.fullmatch(value.strip())
        if m:
            try:
                resolved = _get_path(ctx, m.group(1))
                if isinstance(resolved, (dict, list)):
                    return resolved
            except KeyError:
                pass

        def _sub(mm):
            try:
                return str(_get_path(ctx, mm.group(1)))
            except KeyError:
                return mm.group(0)  # 未解析占位原样保留（由响应侧兜底或报错）

        return _VAR.sub(_sub, value)
    return value


class ConfigEngine(AgentAdapter):
    """配置型 adapter：从 adapter_config 声明渲染请求、解析响应。"""

    # 7.5c 配置型走标准 SSE 契约（含 id: 帧），默认支持 Last-Event-ID 续推
    supports_resume = True

    def __init__(self, agent, interface, adapter_config: dict, secrets: dict) -> None:
        self.agent = agent
        self.interface = interface
        self.cfg = adapter_config or {}
        self.secrets = secrets or {}
        self.contract_type = self.cfg.get("contract_type") or interface.contract_type or "sse"
        self._parser = SSEParser()
        self._prepare_ctx: dict = {}   # prepare 步骤产物（{prepare.xxx} 模板域），幂等只执行一次
        self._prepared = False
        self._reset_ctx: dict = {}     # reset(seed) 产物（{reset.data_id}/{reset.resp} 模板域）

    @property
    def last_event_id(self) -> str | None:
        """最近一次收到的 SSE 事件 id（断流续推 Last-Event-ID 依据）。"""
        return self._parser.last_id

    # ---------------- 前置步骤（声明式 prepare） ----------------
    async def prepare(self, case, client=None) -> None:
        """执行 adapter_config.prepare 声明的前置步骤序列（§15.6 配置型实现）。

        adapter_config.prepare: [
          {"name": "login", "method": "POST", "path": "/api/auth/login",
           "headers": {...}, "body": {...},
           "extract": {"token": "access_token"}},        # 响应.access_token → {prepare.login.token}
          {"name": "upload", "method": "POST", "path": "/api/files/upload",
           "files": {"file": "{case.input.file_path}"},  # multipart 文件上传（平台容器内路径）
           "data": {"type": "contract"},                 # multipart 文本字段
           "extract": {"task_id": "task_id"}},
          {"name": "wait_done", "poll": {                # 声明式轮询（异步任务型 agent 通用原语）
              "path": "/api/tasks/{prepare.upload.task_id}",
              "until": {"status": ["WAITING_REVIEW", "SUCCESS", "FAILED", "CANCELLED"]},
              "interval": 2, "timeout": 300}},
          ...
        ]
        每步整响应留底为 {prepare.<name>}；extract 字段存 {prepare.<name>.<key>}。
        步骤 body/headers/files/data 可引用前序步骤产物、{auth.*}（凭证域）与
        {case.*}（用例域，文件型上传需 {case.input.file_path}）。
        poll 步骤：GET path 直到 until 条件（点号路径，值可单值或 list）全部命中，
        未命中按 interval 轮询，超过 poll.timeout 抛错；HTTP 错误立即抛（非重试语义）。
        幂等：同一 adapter 实例只执行一次（重试/重复调用时复用首次产物）。
        """
        if self._prepared:
            return
        steps = self.cfg.get("prepare") or []
        if not steps:
            self._prepared = True
            return
        base = (self.agent.base_url or "").rstrip("/")
        owned = client is None
        if owned:
            client = build_agent_client()
        try:
            for step in steps:
                name = step.get("name")
                if not name:
                    raise RuntimeError("prepare 步骤缺 name")
                # 步骤自身渲染引用前序 prepare 产物 + 凭证域 + 用例域
                ctx = {"prepare": self._prepare_ctx, "auth": self.secrets,
                       "case": self._case_ctx(case)}
                if "poll" in step:
                    await self._exec_poll(step, name, base, client, ctx)
                else:
                    await self._exec_request(step, name, base, client, ctx)
                logger.debug("prepare.%s 完成", name)
            self._prepared = True
        finally:
            if owned:
                await client.aclose()

    async def _exec_request(self, step, name, base, client, ctx) -> None:
        """执行单个普通 HTTP prepare 步骤（支持 multipart files/data）。"""
        spec = RequestSpec(
            method=(step.get("method") or "POST").upper(),
            url=f"{base}/{step.get('path', '').lstrip('/')}",
            headers=render_template(step.get("headers") or {}, ctx),
            json=render_template(step.get("body"), ctx),
            files=render_template(step.get("files"), ctx),
            data=render_template(step.get("data"), ctx),
            timeout=float(self.cfg.get("timeout", 120.0)),
        )
        resp = await send_request(client, spec)
        if resp.status_code >= 400:
            # P0-2：HTTP 错误抛 AdapterHTTPError（携带 status_code）供 executor 分流
            # （5xx/429 可重试、4xx 契约不重试）；继承 RuntimeError 兼容既有断言。
            raise AdapterHTTPError(
                f"prepare.{name} HTTP {resp.status_code}: {resp.text[:200]}",
                resp.status_code)
        try:
            data = resp.json()
        except Exception as exc:
            raise RuntimeError(f"prepare.{name} 响应非 JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError(
                f"prepare.{name} 响应应为 JSON 对象，实际 {type(data).__name__}")
        self._store(step, name, data)

    async def _exec_poll(self, step, name, base, client, ctx) -> None:
        """声明式轮询步骤：GET path 直到 until 条件命中，整响应留底 {prepare.<name>}。"""
        poll = step["poll"]
        url = f"{base}/{render_template(poll.get('path'), ctx).lstrip('/')}"
        headers = render_template(poll.get("headers") or {}, ctx)
        interval = float(poll.get("interval", 2.0))
        timeout = float(poll.get("timeout", self.cfg.get("timeout", 120.0)))
        until = poll.get("until") or {}
        deadline = time.monotonic() + timeout
        last: dict | None = None
        while True:
            resp = await client.request("GET", url, headers=headers,
                                        timeout=min(interval + 10, timeout + 1))
            if resp.status_code >= 400:
                raise AdapterHTTPError(
                    f"prepare.{name} 轮询 HTTP {resp.status_code}: {resp.text[:200]}",
                    resp.status_code)
            try:
                last = resp.json()
            except Exception as exc:
                raise RuntimeError(f"prepare.{name} 轮询响应非 JSON: {exc}") from exc
            if not isinstance(last, dict):
                raise RuntimeError(f"prepare.{name} 轮询响应应为 JSON 对象")
            if _poll_hit(last, until):
                break
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"prepare.{name} 轮询超时（{timeout}s），最后 status={last.get('status')}")
            await asyncio.sleep(interval)
        self._store(step, name, last)

    def _store(self, step, name, data) -> None:
        """步骤产物留底 {prepare.<name>} + extract 提取 {prepare.<name>.<key>}。"""
        self._prepare_ctx[name] = data
        for key, path in (step.get("extract") or {}).items():
            try:
                data[key] = _get_path(data, path)
            except KeyError as exc:
                raise RuntimeError(f"prepare.{name}.{key} 提取失败: {exc}") from exc

    # ---------------- reset(seed) 前置（7.6 B1） ----------------
    async def reset(self, case, client=None) -> str | None:
        """执行 adapter_config.reset 声明的 seed 重置：POST 返回真实 data_id。

        reset 段 schema：
          {"path": "/admin/reset", "method": "POST", "headers": {...},
           "body": {...},                    # 可含 {case.input.seed}、{auth.*} 占位
           "seed_field": "seed",             # body 里放 seed 值的键（缺省 "seed"）
           "extract_data_id": "data_id"}     # 响应里 data_id 的点号路径（缺省 "data_id"）

        无 reset 段 → 返回 None（跳过，向后兼容）。调用方负责 per-agent 锁保证串行；
        每次调用独立 reset（不缓存），产物留底 {reset.data_id}/{reset.resp} 供 build_request
        模板引用（如请求体带 {reset.data_id}）。agent 侧未实现 reset 前此段不声明即可。
        """
        reset_cfg = self.cfg.get("reset")
        if not reset_cfg:
            return None
        base = (self.agent.base_url or "").rstrip("/")
        owned = client is None
        if owned:
            client = build_agent_client()
        try:
            seed_field = reset_cfg.get("seed_field") or "seed"
            body = dict(reset_cfg.get("body") or {})
            body[seed_field] = "{case.input.seed}"  # 每次 reset 注入当前 case 的 seed
            ctx = {"case": self._case_ctx(case), "auth": self.secrets}
            spec = RequestSpec(
                method=(reset_cfg.get("method") or "POST").upper(),
                url=f"{base}/{reset_cfg.get('path', '/admin/reset').lstrip('/')}",
                headers=render_template(reset_cfg.get("headers") or {}, ctx),
                json=render_template(body, ctx),
                timeout=float(self.cfg.get("timeout", 120.0)),
            )
            resp = await send_request(client, spec)
            if resp.status_code >= 400:
                raise AdapterHTTPError(
                    f"reset HTTP {resp.status_code}: {resp.text[:200]}", resp.status_code)
            data = resp.json()
            if not isinstance(data, dict):
                raise RuntimeError("reset 响应应为 JSON 对象")
            extract = reset_cfg.get("extract_data_id") or "data_id"
            try:
                data_id = _get_path(data, extract)
            except KeyError as exc:
                raise RuntimeError(f"reset 响应缺失 data_id（{extract}）: {exc}") from exc
            if data_id is None:
                raise RuntimeError(f"reset 响应 data_id 为空（{extract}）")
            self._reset_ctx = {"data_id": str(data_id), "resp": data}
            logger.info("reset 完成 agent=%s data_id=%s", self.agent.id, data_id)
            return str(data_id)
        finally:
            if owned:
                await client.aclose()

    # ---------------- 请求构造 ----------------
    def build_request(self, case) -> RequestSpec:
        req = self.cfg.get("request") or {}
        base = (self.agent.base_url or "").rstrip("/")
        path = req.get("path") or self.interface.path
        ctx = self._context(case)
        return RequestSpec(
            method=(req.get("method") or self.interface.method or "POST").upper(),
            # path 同样过模板渲染：动态路径（如 {prepare.session.id}）需替换后再拼 URL
            url=render_template(f"{base}/{path.lstrip('/')}", ctx),
            headers=render_template(req.get("headers") or {}, ctx),
            json=render_template(req.get("body"), ctx),
            files=render_template(req.get("files"), ctx),
            data=render_template(req.get("data"), ctx),
            timeout=self.cfg.get("timeout", 120.0),
        )

    def _case_ctx(self, case) -> dict:
        return {
            "input": getattr(case, "input", None),
            "input_turns": getattr(case, "input_turns", None),
            "expected": getattr(case, "expected", None),
        }

    def _context(self, case) -> dict:
        return {
            "case": self._case_ctx(case),
            "auth": self.secrets,
            "prepare": self._prepare_ctx,  # 前置步骤产物（{prepare.xxx}）
            "reset": self._reset_ctx,      # reset(seed) 产物（{reset.data_id}；未 reset 时空 dict）
            "interface": {"path": self.interface.path, "method": self.interface.method},
        }

    # ---------------- 响应解析 ----------------
    def parse_stream_chunk(self, chunk: bytes) -> list:
        field_map = (self.cfg.get("sse") or {}).get("field_map") or {}
        out = []
        for ev in self._parser.feed(chunk):
            out.append(SSEEvent(type=field_map.get(ev.type, ev.type), data=ev.data, id=ev.id))
        return out

    def parse_sync(self, resp_json: dict) -> dict:
        sync = self.cfg.get("sync") or {}

        def pick(key: str, default: Any) -> Any:
            path = sync.get(key, key)
            try:
                return _get_path(resp_json, path)
            except KeyError:
                return default

        return {
            "answer": pick("answer", ""),
            "reasoning": pick("reasoning", None),
            "tool_calls": pick("tool_calls", []),
            "usage": pick("usage", None),
            "meta": pick("meta", None),
        }
