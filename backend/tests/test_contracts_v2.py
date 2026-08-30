"""manifest v2 解析器 / 生成器 / 占位符引用校验 单测（快捷接入 Q1 转正）。

覆盖：
1. schema 模型校验（无 llm 接口拒绝 / prepare 重名拒绝 / 缺字段拒绝）
2. 占位符引用校验（硬错误拒绝 + 软警告）
3. 等价性回归：build_adapter_config(v2) == seed_data 4 家 ADAPTER_CFG（逐字符）
4. 元信息输出（input_fields / requires_auth / warnings）
5. v1 兼容：现有 parse_manifest 对 v2 payload 透明

4 家 v1 manifest + v2 contract 段内联自 verify_manifest_v2.py（转正后该脚本可删，
测试自包含）。expected 直接从 app.seed_data 引用（单一真相源）。
"""
import copy

import pytest

from app.core.contracts import parse_manifest
from app.core.contracts_v2 import build_adapter_config, parse_manifest_v2
from app.seed_data import (
    CC_ADAPTER_CFG, CS_ADAPTER_CFG, GQ_ADAPTER_CFG, GQ_LIBRARY_ID,
    SP_ADAPTER_CFG,
)

# ---------------- 4 家 v1 manifest（内联自 4 家 agent contracts.py） ----------------

V1_MANIFESTS = {
    "customer-service": {
        "agent": "customer-service", "contract_version": "1.0",
        "interfaces": [
            {"name": "chat", "path": "/api/v1/sessions/{sid}/messages", "method": "POST",
             "contract_type": "sse", "llm": True, "description": "客服会话对话"},
            {"name": "login", "path": "/api/v1/auth/login", "method": "POST",
             "llm": False, "description": "会话鉴权"},
        ],
        "scenes": [
            {"tag": "greeting", "description": "问候与闲聊"},
            {"tag": "order_query", "description": "订单查询"},
        ],
    },
    "contract-check": {
        "agent": "contract-check", "contract_version": "1.0",
        "interfaces": [
            {"name": "result", "path": "/api/tasks/{task_id}/result", "method": "GET",
             "contract_type": "sync", "llm": True, "description": "合同校验结果"},
            {"name": "upload", "path": "/api/files/upload", "method": "POST",
             "llm": False, "description": "上传合同文件"},
        ],
        "scenes": [
            {"tag": "missing_date", "description": "缺失生效日期"},
            {"tag": "single_party", "description": "单方签署"},
        ],
    },
    "smart-procurement": {
        "agent": "smart-procurement", "contract_version": "1.0",
        "interfaces": [
            {"name": "chat", "path": "/api/v1/reviews/{review_id}/chat", "method": "POST",
             "contract_type": "sse", "llm": True, "description": "评审对话"},
            {"name": "score", "path": "/api/v1/reviews/{review_id}/score", "method": "POST",
             "contract_type": "sse", "llm": True, "description": "AI 评分"},
            {"name": "login", "path": "/api/v1/auth/login", "method": "POST",
             "llm": False, "description": "专家/管理员鉴权"},
        ],
        "scenes": [
            {"tag": "tech_scheme", "description": "技术方案评审"},
            {"tag": "price", "description": "报价评审"},
        ],
    },
    "good-question": {
        "agent": "good-question", "contract_version": "1.0",
        "interfaces": [
            {"name": "chat", "path": "/api/chat/{session_id}", "method": "POST",
             "contract_type": "sse", "llm": True, "description": "知识问答"},
            {"name": "login", "path": "/api/auth/login", "method": "POST",
             "llm": False, "description": "会话鉴权"},
        ],
        "scenes": [
            {"tag": "greeting", "description": "问候与闲聊"},
            {"tag": "doc_qa", "description": "文档检索问答"},
        ],
    },
}

# ---------------- 4 家 v2 contract 段（内联自 verify_manifest_v2.py，由 seed_data ADAPTER_CFG 反写） ----------------

V2_CONTRACTS = {
    "customer-service": {
        "type": "sse", "timeout": 120,
        "prepare": [
            {"name": "login", "method": "POST", "path": "/api/v1/auth/login",
             "body": {"username": "{{auth.username}}", "password": "{{auth.password}}"},
             "extract": {"token": "access_token"}},
            {"name": "session", "method": "POST", "path": "/api/v1/sessions",
             "headers": {"Authorization": "Bearer {{prepare.login.token}}"},
             "extract": {"id": "session_id"}},
        ],
        "request": {
            "path": "/api/v1/sessions/{{prepare.session.id}}/messages", "method": "POST",
            "headers": {"Authorization": "Bearer {{prepare.login.token}}",
                        "Content-Type": "application/json"},
            "body": {"content": "{{input.content}}"},
        },
    },
    "contract-check": {
        "type": "sync", "timeout": 300,
        "prepare": [
            {"name": "upload", "method": "POST", "path": "/api/files/upload",
             "files": {"file": "{{input.file_path}}"},
             "extract": {"task_id": "task_id"}},
            {"name": "wait_done", "poll": {
                "path": "/api/tasks/{{prepare.upload.task_id}}",
                "until": {"status": ["WAITING_REVIEW", "SUCCESS", "FAILED", "CANCELLED"]},
                "interval": 2, "timeout": 300}},
        ],
        "request": {"path": "/api/tasks/{{prepare.upload.task_id}}/result", "method": "GET"},
    },
    "smart-procurement": {
        "type": "sse", "timeout": 120,
        "prepare": [
            {"name": "login", "method": "POST", "path": "/api/v1/auth/login",
             "body": {"username": "{{auth.username}}", "password": "{{auth.password}}"},
             "extract": {"token": "access_token"}},
            {"name": "review", "method": "POST", "path": "/api/v1/reviews",
             "headers": {"Authorization": "Bearer {{prepare.login.token}}",
                         "Content-Type": "application/json"},
             "body": {"bid_id": "{{input.bid_id}}", "dimension_id": "{{input.dimension_id}}"},
             "extract": {"review_id": "review_id"}},
        ],
        "request": {
            "path": "/api/v1/reviews/{{prepare.review.review_id}}/chat", "method": "POST",
            "headers": {"Authorization": "Bearer {{prepare.login.token}}",
                        "Content-Type": "application/json"},
            "body": {"question": "{{input.question}}"},
        },
    },
    "good-question": {
        "type": "sse", "timeout": 180,
        "prepare": [
            {"name": "login", "method": "POST", "path": "/api/auth/login",
             "body": {"username": "{{auth.username}}", "password": "{{auth.password}}"},
             "extract": {"token": "access_token"}},
            {"name": "session", "method": "POST", "path": "/api/sessions",
             "headers": {"Authorization": "Bearer {{prepare.login.token}}"},
             "body": {"library_id": GQ_LIBRARY_ID},
             "extract": {"id": "id"}},
        ],
        "request": {
            "path": "/api/chat/{{prepare.session.id}}", "method": "POST",
            "headers": {"Authorization": "Bearer {{prepare.login.token}}",
                        "Content-Type": "application/json"},
            "body": {"content": "{{input.content}}", "stream": True},
        },
        "sse": {"field_map": {"token": "answer"}},
    },
}

EXPECTED = {
    "customer-service": CS_ADAPTER_CFG,
    "contract-check": CC_ADAPTER_CFG,
    "smart-procurement": SP_ADAPTER_CFG,
    "good-question": GQ_ADAPTER_CFG,
}


def _payload(name: str) -> dict:
    # 深拷贝：测试内对 payload 的原地修改（del request / 改 type 等）不得污染共享样例
    return {**copy.deepcopy(V1_MANIFESTS[name]), "contract": copy.deepcopy(V2_CONTRACTS[name])}


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
    # 4 家里只有 contract-check 不引用 {{auth.*}}（无需凭证）
    assert build_adapter_config(_payload("customer-service"))[0].requires_auth is True
    assert build_adapter_config(_payload("contract-check"))[0].requires_auth is False
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
        assert len(m.interfaces) == len(V1_MANIFESTS[name]["interfaces"])
        assert [s.tag for s in m.scenes] == [s["tag"] for s in V1_MANIFESTS[name]["scenes"]]
