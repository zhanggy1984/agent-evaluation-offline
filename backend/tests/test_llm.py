"""7.1 LLM 模块纯函数单测（app/core/llm.py）。

is_configured / parse_json / _validate_allowlist 顶层纯函数宿主直接测；
LlmClient 持 httpx（真实请求与响应解析归 7.2 集成，此处不触发网络）。
"""
import pytest

from app.core.llm import LlmError, _validate_allowlist, is_configured, parse_json


def test_is_configured():
    assert is_configured("key", "https://api.example.com", "gpt-4o") is True
    assert is_configured("", "https://api.example.com", "gpt-4o") is False
    assert is_configured("key", "", "gpt-4o") is False
    assert is_configured("key", "https://api.example.com", "") is False


def test_parse_json_plain():
    assert parse_json('{"a": 1}') == {"a": 1}
    assert parse_json('  [1, 2]  ') == [1, 2]


def test_parse_json_fence():
    text = '```json\n{"model": "x", "cases": [{"name": "n"}]}\n```'
    assert parse_json(text)["model"] == "x"
    # 无 json 语言标记的裸围栏也要能剥
    assert parse_json('```\n{"a": 1}\n```') == {"a": 1}


def test_parse_json_invalid():
    with pytest.raises(LlmError):
        parse_json("这不是 JSON")
    # 围栏内非法内容同样报 LlmError（不把脏输出当骨架）
    with pytest.raises(LlmError):
        parse_json('```json\n{"a": \n```')


def test_validate_allowlist_ok():
    # 命中白名单不抛
    _validate_allowlist("https://api.deepseek.com/v1", ["api.deepseek.com"])


def test_validate_allowlist_reject():
    # SSRF：host 不在白名单 → 拒绝
    with pytest.raises(LlmError):
        _validate_allowlist("https://evil.example.com/v1", ["api.deepseek.com"])


def test_validate_allowlist_no_host():
    with pytest.raises(LlmError):
        _validate_allowlist("not-a-url", ["api.deepseek.com"])
