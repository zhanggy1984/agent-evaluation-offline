"""6.5 通用 LLM 客户端（OpenAI 兼容 /chat/completions，非流式）。

与 judge/client.py 同模式但独立配置（llm.* 与 judge_llm.* 解耦，密钥走 env LLM_API_KEY）：
- 配置：llm.base_url / llm.model_name（system_config global，is_hot）+ LLM_API_KEY（env，不落盘）
- SSRF：复用 AllowlistAsyncClient，allow_hosts 取 llm_allowlist（模型厂商域名白名单）
- 输出强约束：response_format=json_object，容错解析 JSON（剥 ```json 包裹）

顶层纯函数（is_configured/parse_json）宿主单测直接测；LlmClient 持 httpx。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select

from app.core.http import AllowlistAsyncClient

logger = logging.getLogger(__name__)


class LlmError(Exception):
    """LLM 调用失败（未配置/SSRF 白名单/HTTP/解析）。生成骨架单场景失败不中断整体。"""


def is_configured(api_key: str, base_url: str, model_name: str) -> bool:
    """LLM 可用判定：密钥 + base_url/model 齐备。未配置 → 生成骨架明确报错。"""
    return bool(api_key and base_url and model_name)


def parse_json(text: str) -> Any:
    """容错解析 LLM 输出为 JSON。容忍 ```json 包裹；非法 → LlmError（不把脏输出当骨架）。"""
    raw = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL)
    if not m:
        m = re.search(r"```(?:json)?\s*(\[.*\])\s*```", raw, re.DOTALL)
    if m:
        raw = m.group(1)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise LlmError(f"LLM 输出非 JSON: {raw[:200]!r} ({e})")


def _validate_allowlist(base_url: str, allowlist: list[str]) -> None:
    """base_url host 必须在 llm_allowlist（模型厂商域名）内，否则拒绝（SSRF §15.3）。"""
    host = urlparse(base_url).hostname
    if host is None:
        raise LlmError(f"llm base_url 缺少 host: {base_url}")
    if host not in set(allowlist):
        raise LlmError(f"llm base_url 不在 llm_allowlist 白名单: {host}")


class LlmClient:
    """OpenAI 兼容 LLM 客户端。每次按配置构造（is_hot 热生效，与 JudgeClient 同模式）。"""

    def __init__(self, *, base_url: str, model: str, api_key: str,
                 allowlist: list[str], timeout: float = 120.0) -> None:
        _validate_allowlist(base_url, allowlist)
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self._http = AllowlistAsyncClient(allow_hosts=allowlist, allow_cidrs=[], timeout=timeout)

    async def generate_json(self, messages: list[dict], max_tokens: int = 1500) -> Any:
        """调一次 LLM，返回解析后的 JSON（dict/list）。失败抛 LlmError。"""
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        try:
            resp = await self._http.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
        except LlmError:
            raise
        except Exception as e:
            raise LlmError(f"llm 请求异常: {type(e).__name__}: {e}") from e
        if resp.status_code != 200:
            raise LlmError(f"llm HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            body = resp.json()
            content = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError) as e:
            raise LlmError(f"llm 响应结构非法: {resp.text[:300]!r}") from e
        return parse_json(content)


async def get_llm_config(db) -> dict:
    """LLM 全局配置唯一读取点（system_config + env LLM_API_KEY）。

    与 get_overfit_config 同为唯一读取点：生成骨架统一读，防口径分叉。
    """
    from app.models import SystemConfig
    keys = {"llm.base_url", "llm.model_name", "llm_allowlist"}
    rows = (await db.execute(select(SystemConfig).where(SystemConfig.key.in_(keys)))).scalars().all()
    cfg = {r.key: r.value for r in rows}
    from app.core.config import settings
    return {
        "api_key": settings.llm_api_key,
        "base_url": (cfg.get("llm.base_url") or "").strip(),
        "model": (cfg.get("llm.model_name") or "").strip(),
        "allowlist": cfg.get("llm_allowlist") or [],
        "timeout": 120.0,
    }
