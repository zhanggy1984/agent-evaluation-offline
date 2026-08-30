"""B.5 契约探测：对 agent 接口发真实请求，逐字段验证评测契约（§5.1 SSE / §5.2 sync）。

探测与执行的区别：execute_case 聚合结果，探测要「逐字段报告哪个契约字段到达/缺失」。
独立解析原始响应（不依赖 ConfigEngine.parse_sync），保证探测准确、不受平台解析缺陷影响。

探测会真实调用 agent 接口（花 LLM token）——这是验证响应结构契约的唯一可靠方式。
失败不抛出，全部归入 ProbeResult.errors（工具是诊断用，结果全量返回）。
"""
import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field

import httpx

from app.adapters.base import (
    _UPLOADS_DIR, _assert_inside_uploads, request_kwargs, send_request,
)
from app.core.sse_parser import SSEParseError

logger = logging.getLogger(__name__)

# SSE 契约事件（§5.1）：usage/done 必选，其余可选
SSE_MANDATORY = ("usage", "done")


@dataclass
class ProbeResult:
    interface_id: int
    contract_type: str
    ok: bool = False
    http_status: int | None = None
    elapsed_ms: int | None = None
    fields: dict = field(default_factory=dict)   # 逐字段：是否到达/值
    errors: list[str] = field(default_factory=list)
    raw_sample: str | None = None                # 响应样本（截断，诊断用）
    # Q4：输入构造错误（样例文件缺失/路径越界/白名单拒绝）与契约不达标区分——
    # 探测者据此判断「先准备文件」vs「agent 契约有问题」
    input_error: bool = False


def _is_input_error(exc: Exception) -> bool:
    """Q4：输入构造类错误判定（样例文件缺失/不可读/路径越界），与契约不达标区分。

    契约不达标（HTTP 非 200/字段缺失/网络错误）不算输入错误；仅文件缺失、白名单
    拒绝这类「平台侧准备不足」才标 input_error。
    """
    if isinstance(exc, (FileNotFoundError, IsADirectoryError, PermissionError)):
        return True
    if isinstance(exc, ValueError) and "uploads" in str(exc):
        return True  # _assert_inside_uploads 白名单拒绝（base.py 固定消息含 uploads）
    return False


def validate_probe_input(probe_input: dict | None) -> str | None:
    """Q4：文件型探测输入前置校验 → 错误消息或 None。

    平台接入标准：文件型 agent 的探测输入用 `file_path` 键声明平台 uploads 内样例文件
    （cc 类）。缺失/越界/不可读 → 返回可读错误（探测者据此先放文件，不再当契约问题
    排查）；非文件型（无 file_path 键）→ None。
    """
    raw = probe_input.get("file_path") if isinstance(probe_input, dict) else None
    if not raw:
        return None
    path = str(raw)
    try:
        rp = _assert_inside_uploads(path)
    except ValueError as exc:
        return str(exc)
    if not os.path.isfile(rp):
        return f"样例文件不存在: {path}（需先放置到平台 uploads 目录，当前 UPLOADS_DIR={_UPLOADS_DIR}）"
    return None


def _usage_ok(u) -> bool:
    """usage 三分量存在且非负（§5.1/§5.2 token 契约）。"""
    if not isinstance(u, dict):
        return False
    return all(
        isinstance(u.get(k), (int, float)) and u.get(k, 0) >= 0
        for k in ("prompt_tokens", "completion_tokens", "total_tokens")
    )


def _check_sse(seen: dict) -> tuple[dict, list[str]]:
    """SSE 逐事件统计 → (fields, errors)。seen 为各事件类型到达次数。"""
    fields = {
        k: bool(seen.get(k))
        for k in ("meta", "stage", "reasoning", "tool_call", "answer", "usage", "done", "error")
    }
    fields["events"] = sorted(seen)  # 全部到达事件（含协议外事件，诊断用）
    errors = []
    if not fields["usage"]:
        errors.append("未收到 usage 事件（§5.1 必选）")
    if not fields["done"]:
        errors.append("未收到 done 事件（§5.1 必选）")
    if fields["error"]:
        errors.append("收到 error 事件（agent 侧报错）")
    return fields, errors


def _check_sync(resp: dict) -> tuple[dict, list[str]]:
    """同步 JSON 变体逐字段（§5.2）→ (fields, errors)。"""
    usage = resp.get("usage")
    timing = resp.get("timing")
    tool_calls = resp.get("tool_calls")
    fields = {
        "answer": bool(resp.get("answer")),
        "reasoning": "reasoning" in resp,
        "tool_calls": isinstance(tool_calls, list),
        "usage": _usage_ok(usage),
        "usage_detail": usage if _usage_ok(usage) else None,
        "timing_start": bool(timing and timing.get("start_ts") is not None),
        "timing_end": bool(timing and timing.get("end_ts") is not None),
    }
    errors = []
    if not fields["answer"]:
        errors.append("answer 缺失或为空（§5.2 必选）")
    if not fields["usage"]:
        errors.append("usage 缺失或三分量非法（§5.2 必选）")
    if not (fields["timing_start"] and fields["timing_end"]):
        errors.append("timing 缺 start_ts/end_ts（§5.2 必选）")
    return fields, errors


async def probe_interface(adapter, client, case, timeout_s: float = 120.0) -> ProbeResult:
    """对单个接口探测。adapter duck-typing（contract_type/prepare/build_request/parse_*）。

    成功 + 契约达标 → ok=True；任何错误（网络/HTTP 非 200/契约字段缺失）→ ok=False 且 errors 明细。
    """
    pr = ProbeResult(interface_id=getattr(adapter.interface, "id", 0),
                     contract_type=adapter.contract_type)
    try:
        await adapter.prepare(case, client)
    except Exception as exc:
        pr.errors.append(f"prepare: {exc}")
        pr.input_error = _is_input_error(exc)  # Q4：文件缺失/越界 → 输入错误
        return pr
    try:
        spec = adapter.build_request(case)
    except Exception as exc:
        pr.errors.append(f"build_request: {exc}")
        pr.input_error = _is_input_error(exc)  # Q4
        return pr

    start = time.perf_counter()
    first_token_ms: int | None = None
    try:
        if adapter.contract_type == "sse":
            seen: dict[str, int] = {}
            async with client.stream(spec.method, spec.url,
                                     **request_kwargs(spec, timeout_s)) as resp:
                pr.http_status = resp.status_code
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", errors="replace")[:200]
                    pr.errors.append(f"HTTP {resp.status_code}: {body}")
                    pr.raw_sample = body
                else:
                    async for chunk in resp.aiter_bytes():
                        try:
                            events = adapter.parse_stream_chunk(chunk)
                        except SSEParseError as exc:
                            pr.errors.append(f"SSE 解析失败: {exc}")
                            break
                        for ev in events:
                            seen[ev.type] = seen.get(ev.type, 0) + 1
                            if pr.raw_sample is None and ev.data:
                                pr.raw_sample = json.dumps(ev.data, ensure_ascii=False)[:500]
                            if ev.type == "answer" and first_token_ms is None:
                                first_token_ms = int((time.perf_counter() - start) * 1000)
                    fields, errors = _check_sse(seen)
                    fields["ttft_ms"] = first_token_ms
                    pr.fields = fields
                    pr.errors.extend(errors)
        else:
            resp = await send_request(client, spec, timeout=timeout_s)
            pr.http_status = resp.status_code
            if resp.status_code != 200:
                pr.errors.append(f"HTTP {resp.status_code}: {resp.text[:200]}")
                pr.raw_sample = resp.text[:200]
            else:
                try:
                    data = resp.json()
                except Exception as exc:
                    pr.errors.append(f"响应非 JSON: {exc}")
                    pr.raw_sample = resp.text[:200]
                else:
                    pr.raw_sample = json.dumps(data, ensure_ascii=False)[:500]
                    if not isinstance(data, dict):
                        pr.errors.append(f"响应应为 JSON 对象，实际 {type(data).__name__}")
                    else:
                        fields, errors = _check_sync(data)
                        pr.fields = fields
                        pr.errors.extend(errors)
    except asyncio.TimeoutError:
        pr.errors.append(f"探测超时（{timeout_s}s）")
    except httpx.TimeoutException:
        pr.errors.append(f"网络超时（{timeout_s}s）")
    except httpx.RequestError as exc:
        pr.errors.append(f"网络错误: {exc}")
    except Exception as exc:  # 未知异常兜底：诊断类工具不抛
        logger.exception("probe interface %s 未知异常", pr.interface_id)
        pr.errors.append(f"未知异常: {exc}")
        pr.input_error = _is_input_error(exc)  # Q4：multipart 读文件缺失等输入错误归位

    pr.elapsed_ms = int((time.perf_counter() - start) * 1000)
    pr.ok = not pr.errors and pr.http_status == 200
    return pr
