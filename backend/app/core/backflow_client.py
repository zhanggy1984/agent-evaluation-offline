"""回流出站客户端（批 B #232）：向 online 拉取载荷、回写激活状态。

**鉴权对端契约（online 实现为准，非文档转述）**：`/api/v1/pull/payloads` 与
`/api/v1/pull/ack` 共用 `require_evaluator`——`Authorization: Bearer <evaluator_service_secret>`，
fail-closed，**不接平台 JWT**。错 secret 得 401，而症状看起来像「对端没有载荷」。

**出站必须走 `AllowlistAsyncClient`**（SSRF 白名单是本仓既定安全约束，不能绕）。
⚠️ 但**光靠 CIDR 不够**——基址是容器名，必须同时入 `allow_hosts`，否则请求发不出去；
详见 `_client()` 的实测说明（这是本批开工时「SSRF 已验通过」那一步的**反例**：
那次预检用的是裸 httpx，没走真客户端，等于没验）。

超时 5s：本客户端只服务后台循环与收尾，长超时只会拖住循环而不换来可用性。
"""
from __future__ import annotations

import httpx

from app.core.config import settings
from app.core.http import build_agent_client

PULL_PATH = "/api/v1/pull/payloads"
ACK_PATH = "/api/v1/pull/ack"
PUSH_PATH = "/api/v1/backflow/regression-results"  # 结果推送（online router prefix=/backflow，
# 外层再挂 /api/v1——由 pull 走 /api/v1/pull/... 同型印证）

# 单次拉取上限（online 侧契约 limit ≤ 100）
PULL_LIMIT = 100
TIMEOUT = 5.0


class BackflowClientError(Exception):
    """出站失败（网络/非 2xx）。调用方按「本轮跳过、下轮重试」处理，不吞不掉。

    `status_code` = **结构化**的失败码（`None` = 网络层失败，无 HTTP 响应）。它的存在是为了让
    调用方能**按类别**决定可重试性（`runner/error_push._is_retryable`）：鉴权类 4xx 是确定性拒绝，
    重试只是白等退避；而网络抖动与 5xx 值得重试。**不把码揉进消息串**——那是调用方要解析字符串，
    等于把契约签在文案上。
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.evaluator_service_secret}"}


def _base() -> str:
    return settings.backflow_online_base.rstrip("/")


def _client():
    """出站客户端。基址是**容器名**（如 `obs-backend`）时走「解析→校验→IP 直连+透传 Host」。

    R-26（2026-09-15 批 4 已修）：此前这里按基址 hostname 显式塞进 `allow_hosts`，只为绕开
    `core/http.py` 双 Host 头缺陷（该分支不重写 URL，故不触发）。**绕过的代价是跳过 IP 解析
    校验**（`http.py` 的白名单 host 分支只做 deny 检查，而 agent 客户端的 deny 集为空）。
    根因已修（原 Host 项复用 + 摘掉重复项），故**绕过一并撤除** —— 本集成恢复完整的
    「解析全部 IP 并校验在内网段内」这一层。
    """
    return build_agent_client()


async def pull_payloads(
    *, since_ts: str | None = None, next_token: str | None = None, limit: int = PULL_LIMIT
) -> dict:
    """拉取一批待判载荷。返回 `{"payloads": [...], "next_token": str | None}`。

    增量锚双生效：`since_ts`（时间下界）+ `next_token`（keyset 翻页），二者由 online 侧
    自行取交。本函数只透传，不自行记游标——游标持久化属调用方（pull_loop）职责。
    """
    body: dict = {"schema_version": "1.0", "case_type": "regression_error", "limit": limit}
    if since_ts:
        body["since_ts"] = since_ts
    if next_token:
        body["next_token"] = next_token

    base = _base()
    async with _client() as client:
        try:
            resp = await client.post(
                f"{base}{PULL_PATH}", json=body, headers=_headers(), timeout=TIMEOUT
            )
        except httpx.HTTPError as exc:
            raise BackflowClientError(f"拉取请求失败：{type(exc).__name__}", None) from exc
    if resp.status_code != 200:
        raise BackflowClientError(
            f"拉取返回 {resp.status_code}（鉴权或契约不符）", resp.status_code)
    return resp.json()


async def ack(
    payload_id: str, action: str, *, case_id: int | None = None, reason: str | None = None
) -> None:
    """回写激活结果。`action ∈ {draft, active, invalidated}`。

    调用时机是**铁律**：必须在本地事务 **commit 之后**才发（P1 §「先落库再 ack」）——
    反过来会出现「online 认为已激活、offline 库里没有」的静默丢失。
    """
    body: dict = {"payload_id": payload_id, "action": action}
    if case_id is not None:
        # **必须序列化为 str**：online `PullAckRequest.case_id` 是 `str | None`，传 int 被
        # pydantic 判 422（实测踩到过）。收口放在客户端而非调用点——这是本仓与 online 的
        # 线上契约，每个调用方各记一次必漏；批 C 的 `run_id` 是同一型陷阱，此处一并对齐。
        body["case_id"] = str(case_id)
    if reason:
        body["reason"] = reason

    base = _base()
    async with _client() as client:
        try:
            resp = await client.post(
                f"{base}{ACK_PATH}", json=body, headers=_headers(), timeout=TIMEOUT
            )
        except httpx.HTTPError as exc:
            raise BackflowClientError(f"ack 请求失败：{type(exc).__name__}", None) from exc
    if resp.status_code != 200:
        raise BackflowClientError(f"ack 返回 {resp.status_code}", resp.status_code)


async def push_results(body: dict) -> dict:
    """推送一个簇的 run 结果（§10.1）。返回 online 回执
    `{accepted, duplicated, run_record_id, links_advanced, cases_dropped}`。

    **鉴权与 pull/ack 同一 secret**（§10.2「三出站端点共用、无 scope 分置」，与 online 侧
    三端点同用 `require_evaluator` 的实现一致）⇒ 直接复用 `_headers()`。

    序列化口径由调用方（`runner/error_push.assemble_payload`）负责——`run_id`/`case_id` 须
    `str`、`trigger_signal_id` 须 `int`，本函数只透传。
    """
    base = _base()
    async with _client() as client:
        try:
            resp = await client.post(
                f"{base}{PUSH_PATH}", json=body, headers=_headers(), timeout=TIMEOUT
            )
        except httpx.HTTPError as exc:
            raise BackflowClientError(f"结果推送请求失败：{type(exc).__name__}", None) from exc
    if resp.status_code != 200:
        raise BackflowClientError(f"结果推送返回 {resp.status_code}", resp.status_code)
    return resp.json()
