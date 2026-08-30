"""Q2 scaffold discover 支持 v2：识别 / adapter 草案块 / 落库校验（纯函数部分）单测。

端点层（discover HTTP + 落库写库）依赖 DB，走集成测试；此处覆盖可单测的纯逻辑：
_is_v2_manifest / _adapter_block / _confirm_adapter_check。
"""
import copy

import pytest

from app.api.scaffold import _adapter_block, _confirm_adapter_check, _is_v2_manifest

MINIMAL_V2 = {
    "agent": "demo", "contract_version": "2.0",
    "interfaces": [
        {"name": "chat", "path": "/chat", "contract_type": "sse", "llm": True},
    ],
    "scenes": [{"tag": "greeting", "description": "问候"}],
    "contract": {
        "type": "sse", "timeout": 120,
        "request": {"path": "/chat", "method": "POST",
                    "body": {"content": "{{input.content}}"}},
    },
}


# ---------------- 识别 ----------------

def test_is_v2_manifest_with_contract():
    assert _is_v2_manifest(copy.deepcopy(MINIMAL_V2)) is True


def test_is_v2_manifest_without_contract():
    payload = copy.deepcopy(MINIMAL_V2)
    del payload["contract"]
    assert _is_v2_manifest(payload) is False


def test_is_v2_manifest_contract_none():
    payload = copy.deepcopy(MINIMAL_V2)
    payload["contract"] = None
    assert _is_v2_manifest(payload) is False


# ---------------- adapter 草案块（软语义） ----------------

def test_adapter_block_valid():
    block = _adapter_block(copy.deepcopy(MINIMAL_V2))
    assert block["valid"] is True
    assert block["draft"]["contract_type"] == "sse"
    assert block["draft"]["request"]["body"] == {"content": "{case.input.content}"}
    assert block["input_fields"] == ["content"]
    assert block["requires_auth"] is False
    assert block["warnings"] == []
    assert block["errors"] == []


def test_adapter_block_hard_error_soft_semantics():
    # 未知占位符域 → 硬错误，但块仍返回 valid=false（不抛），错误可读
    payload = copy.deepcopy(MINIMAL_V2)
    payload["contract"]["request"]["body"]["bad"] = "{{foo.bar}}"
    block = _adapter_block(payload)
    assert block["valid"] is False
    assert block["draft"] is None
    assert block["input_fields"] == []
    assert block["requires_auth"] is False
    assert any("未知占位符域: foo.bar" in e for e in block["errors"])


def test_adapter_block_rejects_no_llm_interface():
    payload = copy.deepcopy(MINIMAL_V2)
    payload["interfaces"] = [dict(payload["interfaces"][0], llm=False)]
    block = _adapter_block(payload)
    assert block["valid"] is False
    assert any("无 llm=true 评测接口" in e for e in block["errors"])


# ---------------- 落库校验 ----------------

def test_confirm_check_success():
    draft, errs = _confirm_adapter_check(copy.deepcopy(MINIMAL_V2))
    assert errs == []
    assert draft is not None
    assert draft.adapter_config["request"]["path"] == "/chat"
    assert draft.requires_auth is False


def test_confirm_check_rejects_v1_no_contract():
    payload = copy.deepcopy(MINIMAL_V2)
    del payload["contract"]
    draft, errs = _confirm_adapter_check(payload)
    assert draft is None
    assert any("无 contract 段" in e for e in errs)


def test_confirm_check_rejects_hard_error():
    payload = copy.deepcopy(MINIMAL_V2)
    payload["contract"]["request"]["headers"] = {"Authorization": "Bearer {{prepare.nosuch.tok}}"}
    draft, errs = _confirm_adapter_check(payload)
    assert draft is None
    assert any("引用不存在的 prepare 步骤" in e for e in errs)
