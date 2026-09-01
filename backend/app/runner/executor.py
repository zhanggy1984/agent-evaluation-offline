"""单用例执行器（§8 / §15.4）。

流程：build_request → 发送 → 流式/同步解析 → ResultAssembler → 契约校验（done/usage）。
失败不向上抛（技术失败标 error，不计入总分），全部归类为 error_type。
熔断/限流由 orchestrator 在调度层负责，本模块纯执行。
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field, replace

import httpx

from app.adapters.base import (MAX_ERROR_BODY, MAX_SSE_BODY, AdapterHTTPError,
                               AgentAdapter, read_body_capped, request_kwargs, send_request)
from app.core.assembler import ResultAssembler
from app.core.sse_parser import SSEParseError

logger = logging.getLogger(__name__)

# 技术失败 error_type（§8 统一）
ERROR_NO_DONE = "no_done"          # SSE 流未收到 done
ERROR_NO_USAGE = "no_usage"        # 未收到 usage（契约违反）
ERROR_TIMEOUT = "timeout"          # 单用例超时
ERROR_POOL_TIMEOUT = "pool_error"  # 本地连接池耗尽（P1-2：PoolTimeout，与上游慢区分）
ERROR_HTTP = "http_error"          # 非 2xx（3xx/5xx，可重试技术失败）
ERROR_HTTP_CLIENT = "http_client_error"  # HTTP 4xx 业务错（重试无益，不重试不熔断）
ERROR_CONNECT = "connect_error"    # 建连/网络错误
ERROR_SSE_PARSE = "sse_parse_error"
ERROR_BODY_TOO_LARGE = "body_too_large"  # 响应体超上限（P1-1：SSE 累计 >8MB），疑似 agent 异常输出
ERROR_CONTRACT = "contract_error"  # 其他契约/解析异常

# 可重试的技术失败（指数退避重试适用）。B2：HTTP 4xx 业务错（ERROR_HTTP_CLIENT）不在
# 此集——400 参数错/401 未授权/404 不存在重试不会改善（agent bug/配置错），且不累计熔断
# （对齐 orchestrator「契约类错误是 agent bug，不熔断以免修契约前全被打死」语义）；5xx/3xx
# 走 ERROR_HTTP 属临时服务故障，可重试 + 熔断累计。
# 7.5c：no_done（SSE 断流）纳入——断流=没收到 done，续推失败后整 case 重试兜底。
RETRYABLE_ERRORS = {ERROR_TIMEOUT, ERROR_CONNECT, ERROR_SSE_PARSE, ERROR_HTTP, ERROR_NO_DONE}

# 7.5c SSE 断流续推上限（防无限续推；续推失败退化为 no_done → orchestrator 重试）
_MAX_SSE_RESUME = 1


@dataclass
class CaseOutcome:
    unified: dict = field(default_factory=dict)
    timing: dict = field(default_factory=dict)
    status_code: int | None = None
    error_type: str | None = None
    error_detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.error_type is None


def _fail(error_type: str, detail: str) -> CaseOutcome:
    return CaseOutcome(error_type=error_type, error_detail=detail)


async def execute_case(
    adapter: AgentAdapter,
    client: httpx.AsyncClient,
    case,
    timeout_s: float = 120.0,
) -> CaseOutcome:
    """执行单个用例。outcome.ok 为 True 时 unified/timing 有效。"""
    try:
        await adapter.prepare(case, client)
    except AdapterHTTPError as exc:
        # P0-2：prepare HTTP 错误按状态码分流——4xx（429 除外）是凭证/配置错归 contract 不重试；
        # 5xx/429 临时服务故障归 ERROR_HTTP 可重试（orchestrator 按 RETRYABLE_ERRORS 重试 + 熔断累计）。
        if 400 <= exc.status_code < 500 and exc.status_code != 429:
            logger.warning("prepare HTTP %s case=%s: %s", exc.status_code,
                           getattr(case, "id", None), exc)
            return _fail(ERROR_CONTRACT, f"prepare: {exc}")
        logger.warning("prepare 临时失败 HTTP %s case=%s: %s", exc.status_code,
                       getattr(case, "id", None), exc)
        return _fail(ERROR_HTTP, f"prepare: {exc}")
    except httpx.PoolTimeout as exc:
        # P1-2：本地连接池耗尽（并发超 limits 上限）与上游慢区分——平台自身容量问题，
        # 重试无益（池仍饱和）且会放大负载 → pool_error 非可重试、不累计熔断
        logger.warning("prepare 连接池耗尽 case=%s: %s", getattr(case, "id", None), exc)
        return _fail(ERROR_POOL_TIMEOUT, f"prepare: {exc}")
    except httpx.TimeoutException as exc:
        # P0-2：prepare 超时 → 可重试（ERROR_TIMEOUT），不再误判 agent contract bug
        logger.warning("prepare 超时 case=%s: %s", getattr(case, "id", None), exc)
        return _fail(ERROR_TIMEOUT, f"prepare: {exc}")
    except httpx.RequestError as exc:
        # P0-2：prepare 建连/网络错 → 可重试（ERROR_CONNECT）
        logger.warning("prepare 网络错 case=%s: %s", getattr(case, "id", None), exc)
        return _fail(ERROR_CONNECT, f"prepare: {exc}")
    except Exception as exc:  # 前置失败（模板/提取等契约错）：配置/凭证错误，不熔断
        logger.warning("prepare 失败 case=%s: %s", getattr(case, "id", None), exc)
        return _fail(ERROR_CONTRACT, f"prepare: {exc}")

    try:
        spec = adapter.build_request(case)
    except Exception as exc:  # 模板渲染/配置错误
        logger.warning("build_request 失败 case=%s: %s", getattr(case, "id", None), exc)
        return _fail(ERROR_CONTRACT, f"build_request: {exc}")

    start = time.perf_counter()
    assembler = ResultAssembler()
    status_code: int | None = None
    resume_tries = 0
    try:
        if adapter.contract_type == "sse":
            while True:
                spec_for_send = spec
                if resume_tries > 0 and getattr(adapter, "last_event_id", None):
                    # 7.5c 续推：带 Last-Event-ID 头从断点续传（标准契约，agent 侧按需适配）
                    spec_for_send = replace(
                        spec, headers={**spec.headers, "Last-Event-ID": adapter.last_event_id})
                stream_bytes = 0
                async with client.stream(
                    spec_for_send.method, spec_for_send.url,
                    **request_kwargs(spec_for_send, timeout_s),
                ) as resp:
                    status_code = resp.status_code
                    if status_code != 200:
                        # P1-1：错误体限长读取（原 aread() 全量缓冲，错误体无上限时内存暴涨）
                        body = (await read_body_capped(resp, MAX_ERROR_BODY))[:200]
                        err = ERROR_HTTP_CLIENT if 400 <= status_code < 500 else ERROR_HTTP
                        return _fail(err, f"HTTP {status_code}: {body}")
                    async for chunk in resp.aiter_bytes():
                        stream_bytes += len(chunk)
                        if stream_bytes > MAX_SSE_BODY:
                            # P1-1：SSE 累计超限 → 非重试技术失败（agent 异常输出，重试复现）
                            return _fail(
                                ERROR_BODY_TOO_LARGE,
                                f"SSE 流累计超 {MAX_SSE_BODY // 1024 // 1024}MB，疑似 agent 异常输出")
                        elapsed = time.perf_counter() - start
                        for ev in adapter.parse_stream_chunk(chunk):
                            assembler.on_event(ev, elapsed)
                # 流自然结束未收 done：支持续推且有断点 → 重发续传（上限内）；否则退出走契约校验
                if (assembler.done_elapsed is None and resume_tries < _MAX_SSE_RESUME
                        and getattr(adapter, "supports_resume", False)
                        and getattr(adapter, "last_event_id", None)):
                    resume_tries += 1
                    logger.info("SSE 断流，续推 last_event_id=%s", adapter.last_event_id)
                    continue
                break
        else:
            resp = await send_request(client, spec, timeout=timeout_s)
            status_code = resp.status_code
            if status_code != 200:
                # P1-1：resp.text 全量解码超大错误体徒耗内存 → 只解码前 200 字节
                err = ERROR_HTTP_CLIENT if 400 <= status_code < 500 else ERROR_HTTP
                return _fail(err, f"HTTP {status_code}: {resp.content[:200].decode('utf-8', errors='replace')}")
            try:
                unified = adapter.parse_sync(resp.json())
            except Exception as exc:
                return _fail(ERROR_CONTRACT, f"parse_sync: {exc}")
            assembler.ingest_sync(unified, time.perf_counter() - start)
    except httpx.PoolTimeout as exc:
        # P1-2：必须先于 httpx.TimeoutException（PoolTimeout 是其子类）——本地池耗尽
        # 归 pool_error 而非误标上游慢 timeout；非可重试（平台容量问题，重试放大负载）
        return _fail(ERROR_POOL_TIMEOUT, f"连接池耗尽: {exc}")
    except asyncio.TimeoutError:
        return _fail(ERROR_TIMEOUT, f"单用例超时（{timeout_s}s）")
    except httpx.TimeoutException:
        return _fail(ERROR_TIMEOUT, f"网络超时（{timeout_s}s）")
    except SSEParseError as exc:
        return _fail(ERROR_SSE_PARSE, str(exc))
    except httpx.RequestError as exc:
        return _fail(ERROR_CONNECT, f"网络错误: {exc}")
    except Exception as exc:  # 未知异常兜底：归类技术失败，不中断 run
        logger.exception("用例执行未知异常 case=%s", getattr(case, "id", None))
        return _fail(ERROR_CONTRACT, f"未知异常: {exc}")

    # ---- 契约校验 ----
    if adapter.contract_type == "sse" and assembler.done_elapsed is None:
        return _fail(ERROR_NO_DONE, "SSE 流未收到 done 事件（断流）")
    if assembler.usage is None:
        return _fail(ERROR_NO_USAGE, "未收到 usage（违反 §5.1 契约）")

    return CaseOutcome(
        unified=assembler.to_unified(),
        timing=assembler.timing_entry(),
        status_code=status_code,
    )
