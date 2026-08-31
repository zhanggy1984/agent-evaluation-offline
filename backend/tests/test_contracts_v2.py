"""manifest v2 解析器 / 生成器 / 占位符引用校验 单测（快捷接入 Q1 转正）。

覆盖：
1. schema 模型校验（无 llm 接口拒绝 / prepare 重名拒绝 / 缺字段拒绝）
2. 占位符引用校验（硬错误拒绝 + 软警告）
3. 等价性回归：build_adapter_config(v2) == seed_data 4 家 ADAPTER_CFG（逐字符）
4. 元信息输出（input_fields / requires_auth / warnings）
5. v1 兼容：现有 parse_manifest 对 v2 payload 透明

4 家完整 v2 manifest 直接引用 seed_data.MANIFEST_SNAPSHOTS（Q3 单一真相源，杜绝内联双维护），
`_payload` 深拷贝防止测试原地修改污染快照。迁移回归见 test_seed_data_legacy.py
（派生 adapter_config == 迁移前手写值，逐字符）。
"""
import copy

import pytest

from app.core.contracts import parse_manifest
from app.core.contracts_v2 import build_adapter_config, parse_manifest_v2
from app.seed_data import (
    CC_ADAPTER_CFG, CS_ADAPTER_CFG, GQ_ADAPTER_CFG, MANIFEST_SNAPSHOTS,
    SP_ADAPTER_CFG,
)

# ---------------- 4 家完整 v2 manifest（单一真相源 = seed_data.MANIFEST_SNAPSHOTS） ----------------
# 快照含 interfaces + scenes + contract 段，与 4 家 agent 侧 GET /api/contracts 同构（Q3）。

EXPECTED = {
    "customer-service": CS_ADAPTER_CFG,
    "contract-check": CC_ADAPTER_CFG,
    "smart-procurement": SP_ADAPTER_CFG,
    "good-question": GQ_ADAPTER_CFG,
}


def _payload(name: str) -> dict:
    # 深拷贝：测试内对 payload 的原地修改（del request / 改 type 等）不得污染共享快照
    return copy.deepcopy(MANIFEST_SNAPSHOTS[name])


# ---------------- 1. schema 模型校验 ----------------

def test_parse_valid_v2():
    m, errs = parse_manifest_v2(_payload("customer-service"))
    assert errs == []
    assert m is not None
    assert m.agent == "customer-service"
    assert m.contract.type == "sse"


@pytest.mark.parametrize("name", ["customer-service", "contract-check",
                                  "smart-procurement", "good-question"])
def test_parse_all_four_valid(name):
    m, errs = parse_manifest_v2(_payload(name))
    assert errs == [], f"{name}: {errs}"
    assert m is not None


def test_reject_no_llm_interface():
    payload = _payload("customer-service")
    payload["interfaces"] = [dict(i, llm=False) for i in payload["interfaces"]]
    m, errs = parse_manifest_v2(payload)
    assert m is None
    assert any("无 llm=true 评测接口" in e for e in errs)


def test_reject_duplicate_prepare_name():
    payload = _payload("customer-service")
    payload["contract"]["prepare"].append(payload["contract"]["prepare"][0])
    m, errs = parse_manifest_v2(payload)
    assert m is None
    assert any("prepare 步骤名重复" in e for e in errs)


def test_reject_missing_request():
    payload = _payload("customer-service")
    del payload["contract"]["request"]
    m, errs = parse_manifest_v2(payload)
    assert m is None
    assert any("contract.request" in e for e in errs)


def test_reject_bad_contract_type():
    payload = _payload("customer-service")
    payload["contract"]["type"] = "poll"
    m, errs = parse_manifest_v2(payload)
    assert m is None
    assert any("contract.type" in e for e in errs)


# ---------------- 2. 占位符引用校验（硬错误拒绝 + 软警告） ----------------

def test_reject_unknown_prepare_step():
    payload = _payload("customer-service")
    payload["contract"]["request"]["headers"]["Authorization"] = "Bearer {{prepare.nosuch.token}}"
    draft, errs = build_adapter_config(payload)
    assert draft is None
    assert any("引用不存在的 prepare 步骤" in e for e in errs)


def test_reject_extract_missing_field():
    payload = _payload("customer-service")
    # login extract 只声明 token，误引用 id
    payload["contract"]["request"]["path"] = "/api/s/{sid}"
    payload["contract"]["request"]["headers"]["Authorization"] = "Bearer {{prepare.login.id}}"
    draft, errs = build_adapter_config(payload)
    assert draft is None
    assert any("extract 未声明字段 id" in e for e in errs)
    # 错误信息提示声明 key（['token']）且注明非响应路径，防止 agent 误把 access_token 当引用
    assert any("['token']" in e and "非响应路径" in e for e in errs)


def test_reject_poll_step_field_ref():
    payload = _payload("contract-check")
    # wait_done 是纯 poll 步骤（无 extract），引用其产物字段应拒绝
    payload["contract"]["request"]["path"] = "/api/tasks/{{prepare.wait_done.status}}/result"
    draft, errs = build_adapter_config(payload)
    assert draft is None
    assert any("extract 未声明字段 status" in e for e in errs)


def test_reject_prepare_ref_without_field():
    payload = _payload("customer-service")
    payload["contract"]["request"]["headers"]["Authorization"] = "Bearer {{prepare.login}}"
    draft, errs = build_adapter_config(payload)
    assert draft is None
    assert any("prepare 引用必须形如" in e for e in errs)


def test_reject_unknown_domain():
    payload = _payload("customer-service")
    payload["contract"]["request"]["headers"]["Authorization"] = "Bearer {{foo.bar}}"
    draft, errs = build_adapter_config(payload)
    assert draft is None
    assert any("未知占位符域: foo.bar" in e for e in errs)


def test_reject_unknown_domain_in_sse():
    # sse 段同样经 _render 渲染，坏占位符必须被校验拦截（不得 crash / 静默生成坏 adapter）
    payload = _payload("good-question")
    payload["contract"]["sse"]["field_map"]["bad"] = "{{foo.bar}}"
    draft, errs = build_adapter_config(payload)
    assert draft is None
    assert any("未知占位符域: foo.bar" in e for e in errs)


def test_reject_prepare_missing_in_sse():
    payload = _payload("good-question")
    payload["contract"]["sse"]["field_map"]["bad"] = "{{prepare.nosuch.token}}"
    draft, errs = build_adapter_config(payload)
    assert draft is None
    assert any("引用不存在的 prepare 步骤" in e for e in errs)


def test_warning_unreferenced_extract_still_generates():
    payload = _payload("customer-service")
    payload["contract"]["prepare"].append(
        {"name": "extra", "method": "POST", "path": "/api/extra",
         "extract": {"y": "y"}})  # 声明但从未被引用
    draft, errs = build_adapter_config(payload)
    assert errs == []
    assert draft is not None
    assert any("prepare.extra.extract 声明但未被任何引用使用" in w for w in draft.warnings)


# ---------------- 3. 等价性回归：build_adapter_config(v2) == seed_data ADAPTER_CFG ----------------

@pytest.mark.parametrize("name", ["customer-service", "contract-check",
                                  "smart-procurement", "good-question"])
def test_equivalence_with_seed_data(name):
    draft, errs = build_adapter_config(_payload(name))
    assert errs == [], f"{name}: {errs}"
    assert draft is not None
    assert draft.adapter_config == EXPECTED[name], f"{name} 与 seed_data ADAPTER_CFG 不等价"


# ---------------- 4. 元信息输出 ----------------

def test_input_fields():
    expected = {
        "customer-service": ["content"],
        "contract-check": ["file_path"],
        "smart-procurement": ["bid_id", "dimension_id", "question"],
        "good-question": ["content"],
    }
    for name, fields in expected.items():
        draft, errs = build_adapter_config(_payload(name))
        assert errs == []
        assert draft.input_fields == fields, f"{name} input_fields 不符"


def test_requires_auth():
    # Q9 起 4 家全部引用 {{auth.*}}（cc 补了 login prepare，需凭证换 JWT）
    assert build_adapter_config(_payload("customer-service"))[0].requires_auth is True
    assert build_adapter_config(_payload("contract-check"))[0].requires_auth is True
    assert build_adapter_config(_payload("smart-procurement"))[0].requires_auth is True
    assert build_adapter_config(_payload("good-question"))[0].requires_auth is True


def test_input_fields_nested_path_collected_full():
    payload = _payload("customer-service")
    payload["contract"]["request"]["body"]["nested"] = {"a": {"b": "{{input.doc.a.b}}"}}
    draft, errs = build_adapter_config(payload)
    assert errs == []
    assert "doc.a.b" in draft.input_fields  # 嵌套路径收全路径（Q5 用例模板逐级展开的契约）


# ---------------- 5. v1 兼容：现有 parse_manifest 对 v2 payload 透明 ----------------

def test_v1_parse_manifest_accepts_v2_payload():
    # 向后兼容：pydantic 默认 extra=ignore，contract 段对 v1 解析透明
    for name in EXPECTED:
        m, errs = parse_manifest(_payload(name))
        assert errs == [], f"{name}: v2 payload 被 v1 parse_manifest 拒绝 → {errs}"
        assert m is not None
        assert len(m.interfaces) == len(MANIFEST_SNAPSHOTS[name]["interfaces"])
        assert [s.tag for s in m.scenes] == [s["tag"] for s in MANIFEST_SNAPSHOTS[name]["scenes"]]
