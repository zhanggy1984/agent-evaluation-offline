"""Q2/Q3 scaffold discover 支持 v2：识别 / adapter 草案块 / 落库校验 / 漂移对比（纯函数）单测。

端点层（discover HTTP + 落库写库）依赖 DB，走集成测试；此处覆盖可单测的纯逻辑：
_is_v2_manifest / _adapter_block / _confirm_adapter_check / _contract_drift / _adapter_drift。
"""
import copy

import pytest

from app.api.scaffold import (
    _adapter_block, _adapter_drift, _confirm_adapter_check, _contract_drift, _is_v2_manifest,
)

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


# ---------------- Q3：adapter 漂移对比（_contract_drift / _adapter_drift） ----------------

SNAP_CONTRACT = {"type": "sse", "timeout": 120, "prepare": [], "request": {}}


def test_contract_drift_no_diff():
    assert _contract_drift(dict(SNAP_CONTRACT), dict(SNAP_CONTRACT)) == []


def test_contract_drift_runtime_missing_key():
    runtime = {k: v for k, v in SNAP_CONTRACT.items() if k != "prepare"}
    out = _contract_drift(runtime, SNAP_CONTRACT)
    assert any("缺少平台快照段" in e and "prepare" in e for e in out)


def test_contract_drift_runtime_extra_key():
    runtime = {**SNAP_CONTRACT, "extra": 1}
    out = _contract_drift(runtime, SNAP_CONTRACT)
    assert any("多出平台快照外段" in e and "extra" in e for e in out)


def test_contract_drift_no_snapshot():
    out = _contract_drift(dict(SNAP_CONTRACT), None)
    assert any("平台无 manifest 快照" in e for e in out)


def test_contract_drift_runtime_v1_no_contract():
    out = _contract_drift(None, dict(SNAP_CONTRACT))
    assert any("无 contract 段" in e for e in out)


def test_adapter_drift_matches_stored_snapshot():
    payload = copy.deepcopy(MINIMAL_V2)
    stored = {"_manifest_v2": {"contract": copy.deepcopy(payload["contract"])}}
    assert _adapter_drift(payload, stored) == []


def test_adapter_drift_detects_agent_change():
    payload = copy.deepcopy(MINIMAL_V2)
    stored = {"_manifest_v2": {"contract": copy.deepcopy(payload["contract"])}}
    payload["contract"]["timeout"] = 999
    payload["contract"]["extra"] = 1  # agent 新增快照没有的段
    out = _adapter_drift(payload, stored)
    assert any("多出平台快照外段" in e and "extra" in e for e in out)
    # 只新增不缺失 → 无「缺少平台快照段」误报
    assert not any("缺少平台快照段" in e for e in out)


def test_adapter_drift_no_snapshot_returns_hint():
    out = _adapter_drift(copy.deepcopy(MINIMAL_V2), {})
    assert any("平台无 manifest 快照" in e for e in out)


def test_adapter_drift_v1_runtime_no_contract():
    payload = copy.deepcopy(MINIMAL_V2)
    del payload["contract"]
    stored = {"_manifest_v2": {"contract": dict(SNAP_CONTRACT)}}
    out = _adapter_drift(payload, stored)
    assert any("无 contract 段" in e for e in out)


def test_adapter_drift_non_dict_payload_no_crash():
    # agent 端点返回 list/string：不得 AttributeError（Q3 集成测试实证抓到的 500）
    assert _adapter_drift(["not", "a", "dict"], {"_manifest_v2": {"contract": SNAP_CONTRACT}})
    assert _adapter_drift("raw string", {"_manifest_v2": {"contract": SNAP_CONTRACT}})


def test_adapter_drift_non_dict_stored_config_no_crash():
    out = _adapter_drift(copy.deepcopy(MINIMAL_V2), ["bad", "stored"])
    assert any("平台无 manifest 快照" in e for e in out)
