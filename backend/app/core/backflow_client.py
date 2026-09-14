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

from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.core.http import build_agent_client

PULL_PATH = "/api/v1/pull/payloads"
ACK_PATH = "/api/v1/pull/ack"

# 单次拉取上限（online 侧契约 limit ≤ 100）
PULL_LIMIT = 100
TIMEOUT = 5.0


class BackflowClientError(Exception):
    """出站失败（网络/非 2xx）。调用方按「本轮跳过、下轮重试」处理，不吞不掉。"""


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.evaluator_service_secret}"}


def _base() -> str:
    return settings.backflow_online_base.rstrip("/")


def _client():
    """出站客户端。**必须把目标主机塞进 allow_hosts**，不能只靠 CIDR。

    原因（实测，`core/http.py:106-118` 的既有行为）：`AllowlistAsyncClient.send` 对
    **不在 `allow_hosts` 但可解析**的主机名走「把 URL host 换成解析后的 IP + 补回原始
    Host」的路径；而补回写法是 `dict(request.headers)`（键已小写为 `host`）再赋
    `["Host"]` ⇒ 请求带上**两个大小写不同的 Host 头** ⇒ h11 抛
    `LocalProtocolError: Found multiple Host: headers`，请求根本发不出去。
    实测对照：`host.docker.internal`（在白名单内，走 `http.py:85-86` 直接返回、不重写）
    返回 200；`obs-backend`（不在白名单）必失败。而本集成的基址正是**容器名**。

    故这里按配置基址的 hostname 显式入白名单，走不重写的那条分支。
    代价（承认）：该分支**跳过 IP 解析校验**（`http.py:85-86` 只做 denied 检查）。
    基址是运维配置的固定值、非用户输入，可接受；但**根因在 `core/http.py`**，
    正确修法是让 send 复用原 Host 项而不是新加一个，属 A 级改动，未在本批动。
    登记见 `error-backflow-task.md` **O-F.9 / R-26**（影响面 = 一切容器名出站，不限本特性）。
    """
    host = urlparse(_base()).hostname
    return build_agent_client(extra_hosts=[host] if host else ())


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
            raise BackflowClientError(f"拉取请求失败：{type(exc).__name__}") from exc
    if resp.status_code != 200:
        raise BackflowClientError(f"拉取返回 {resp.status_code}（鉴权或契约不符）")
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
            raise BackflowClientError(f"ack 请求失败：{type(exc).__name__}") from exc
    if resp.status_code != 200:
        raise BackflowClientError(f"ack 返回 {resp.status_code}")
