"""AgentAdapter 抽象（§15.6）+ RequestSpec。

配置型 adapter 由 engine.ConfigEngine 提供默认实现；
代码型 adapter 继承本类覆写（复杂前置/清理逻辑才需要）。
"""
import mimetypes
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

# P0 安全收敛：multipart 文件路径白名单。文件型用例自存于平台 uploads（决策 #12），
# 出站读取一律限定该目录内，防 {case.input.file_path} 路径穿越读容器任意文件（如 .env）。
_UPLOADS_DIR = os.path.realpath(os.environ.get("UPLOADS_DIR", "/app/uploads"))


@dataclass
class RequestSpec:
    method: str
    url: str
    headers: dict
    json: dict | None = None
    # multipart（与 json 互斥）：files 值为「平台容器内文件路径」，data 为文本字段
    files: dict | None = None
    data: dict | None = None
    timeout: float = 120.0


def multipart_files(files: dict) -> dict:
    """{表单字段: 文件路径} → httpx multipart files（读字节 + basename 文件名 + MIME 类型）。

    ConfigEngine 与 probe 共用：一次性读进内存，避免跨 await 持有文件句柄；
    路径须为平台 uploads 目录内的可读路径（P0：realpath 校验防路径穿越）。
    """
    out = {}
    for field, path in files.items():
        rp = _assert_inside_uploads(str(path))
        with open(rp, "rb") as f:
            content = f.read()
        name = os.path.basename(rp)
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        out[field] = (name, content, mime)
    return out


def _assert_inside_uploads(path: str) -> str:
    """校验并返回 realpath：必须在 uploads 目录内，否则拒绝（防 ../ 与软链逃逸）。"""
    rp = os.path.realpath(path)
    if not rp.startswith(_UPLOADS_DIR + os.sep):
        raise ValueError(f"文件路径不在 uploads 目录内: {path}")
    return rp


def request_kwargs(spec: RequestSpec, timeout: float | None = None) -> dict:
    """构造 httpx.request/stream 的发送参数字典。

    files/data 与 json 互斥分流：步骤含 multipart 字段走 files+data，否则走 json。
    """
    kwargs: dict = {
        "headers": spec.headers,
        "timeout": timeout if timeout is not None else spec.timeout,
    }
    if spec.files or spec.data:
        kwargs["data"] = spec.data
        kwargs["files"] = multipart_files(spec.files or {})
    else:
        kwargs["json"] = spec.json
    return kwargs


async def send_request(client: httpx.AsyncClient, spec: RequestSpec,
                       timeout: float | None = None) -> httpx.Response:
    """按 RequestSpec 统一发送（multipart 分流，供 engine/prepare 与 probe 复用）。"""
    return await client.request(spec.method, spec.url, **request_kwargs(spec, timeout))


class AgentAdapter(ABC):
    contract_type: str = "sse"
    # 7.5c 断流续推：支持 Last-Event-ID 续推的 adapter 显式置 True（行为 opt-in，避免既有代码突变）
    supports_resume: bool = False

    @abstractmethod
    def build_request(self, case) -> RequestSpec:
        """由用例构造出站请求（含模板渲染/凭证注入）。"""

    def parse_stream_chunk(self, chunk: bytes) -> list:
        """SSE 变体：chunk → 统一事件列表（answer/reasoning/tool_call/usage/meta/done/error）。"""
        raise NotImplementedError

    def parse_sync(self, resp_json: dict) -> dict:
        """同步变体：响应 JSON → 统一结果 {answer, reasoning, tool_calls, usage, meta}。"""
        raise NotImplementedError

    async def prepare(self, case, client=None) -> None:
        """幂等前置（登录/建 session/上传文件）。配置型默认无操作。

        client 复用 executor 的出站客户端（自带 SSRF 防护）；None 时自建。
        """

    async def cleanup(self, case) -> None:
        """评测后清理 agent 侧测试数据。配置型默认无操作。"""
