"""Q5 用例骨架半自动：build_case_skeleton / build_suite_skeleton 纯函数单测。

上限声明（决策）：expected/golden_answer/reference_docs 永远人工——骨架 expected 必须
留空待填；断言/metrics 给可跑最小集（completeness field_nonempty answer，与 seed 同口径）。
"""
from app.core.skeleton import build_case_skeleton, build_suite_skeleton

MINIMAL_V2 = {
    "agent": "demo", "contract_version": "2.0",
    "interfaces": [
        {"name": "chat", "path": "/chat", "contract_type": "sse", "llm": True},
        {"name": "health", "path": "/health", "contract_type": "sync", "llm": False},
    ],
    "scenes": [
        {"tag": "greeting", "description": "问候"},
        {"tag": "onboarding", "description": "引导"},
    ],
    "contract": {
        "type": "sse", "timeout": 120,
        "request": {"path": "/chat", "method": "POST",
                    "body": {"content": "{{input.content}}", "params": {"k": "{{input.a.b}}"}}},
    },
}


# ---------------- build_case_skeleton ----------------

def test_case_nested_fields_unpack():
    sk = build_case_skeleton(["content", "a.b", "file_path"])
    assert sk["input"] == {"content": "", "a": {"b": ""}, "file_path": ""}


def test_case_conflict_path_promotes_to_dict():
    """同时有 `a` 与 `a.b`：中间层提升为 dict 并回挂父级，嵌套字段不丢（hook 抓到真 bug）。"""
    sk = build_case_skeleton(["a", "a.b"])
    assert sk["input"] == {"a": {"b": ""}}


def test_case_probe_input_merge():
    sk = build_case_skeleton(["content", "file_path"], probe_input={"content": "你好"})
    assert sk["input"]["content"] == "你好"
    assert sk["input"]["file_path"] == ""  # 缺失字段仍空串补全


def test_case_probe_none_safe():
    sk = build_case_skeleton(["content"], probe_input=None)
    assert sk["input"] == {"content": ""}
    sk2 = build_case_skeleton(["content"], probe_input=["bad"])
    assert sk2["input"] == {"content": ""}  # 非 dict 忽略


def test_case_file_type_detection():
    assert build_case_skeleton(["file_path"])["input_type"] == "file"
    assert build_case_skeleton(["content"])["input_type"] == "text"


def test_case_defaults_expected_empty():
    sk = build_case_skeleton(["content"])
    assert sk["expected"] == {}  # 业务知识留空待填
    assert sk["assertions"] == [
        {"dimension": "completeness", "op": "field_nonempty", "args": {"path": "answer"}}]
    assert sk["metrics"] == {"completeness": {"enabled": True}}


def test_case_name_scene_interface():
    sk = build_case_skeleton(["content"], interface_name="chat", scene_tag="greeting")
    assert sk["name"] == "greeting-chat"


def test_case_fresh_lists_not_shared():
    a = build_case_skeleton(["content"])
    b = build_case_skeleton(["content"])
    assert a["assertions"] is not b["assertions"]
    assert a["metrics"] is not b["metrics"]


# ---------------- build_suite_skeleton ----------------

def test_suite_scene_x_llm_interface():
    sk, errs = build_suite_skeleton(MINIMAL_V2, agent_name="demo")
    assert errs == []
    assert sk["agent_name"] == "demo"
    assert sk["suite"]["name"] == "demo 接入示例"
    assert len(sk["cases"]) == 2  # 每 scene 一个（health 非 llm 不生成）
    assert {c["name"] for c in sk["cases"]} == {"greeting-chat", "onboarding-chat"}
    assert len(sk["interfaces"]) == 1  # 只 llm 接口
    assert sk["interfaces"][0]["name"] == "chat"


def test_suite_no_scene_falls_back_interface():
    payload = {**MINIMAL_V2, "scenes": []}
    sk, _ = build_suite_skeleton(payload, agent_name="demo")
    assert len(sk["cases"]) == 1
    assert sk["cases"][0]["name"] == "chat"


def test_suite_v1_no_contract_errors():
    payload = {k: v for k, v in MINIMAL_V2.items() if k != "contract"}
    sk, errs = build_suite_skeleton(payload)
    assert sk is None
    assert errs


def test_suite_probe_input_merged_to_all_cases():
    sk, _ = build_suite_skeleton(MINIMAL_V2, agent_name="demo",
                                 probe_input={"content": "你好"})
    assert all(c["input"]["content"] == "你好" for c in sk["cases"])
    assert all(c["input"]["a"]["b"] == "" for c in sk["cases"])  # 缺值仍空串


# ---------------- 真实 4 家快照（Q3 单一真相源） ----------------

def test_cc_real_manifest_file_type_skeleton():
    """cc 文件型：无 probe_input → file_path 空串；带 probe_input → merge。"""
    from app.seed_data import MANIFEST_SNAPSHOTS

    sk, errs = build_suite_skeleton(MANIFEST_SNAPSHOTS["contract-check"],
                                    agent_name="contract-check")
    assert errs == []
    assert sk["cases"]
    assert all(c["input_type"] == "file" for c in sk["cases"])
    assert all(c["input"]["file_path"] == "" for c in sk["cases"])

    sk2, _ = build_suite_skeleton(
        MANIFEST_SNAPSHOTS["contract-check"], agent_name="cc",
        probe_input={"file_path": "/app/uploads/cc_b1_missing_date.pdf"})
    assert all(c["input"]["file_path"] == "/app/uploads/cc_b1_missing_date.pdf"
               for c in sk2["cases"])


def test_other_agents_skeleton_buildable():
    """cs/gq/sp 快照都能生成骨架（Q3 单一真相源回归：快照可被骨架消费）。"""
    from app.seed_data import MANIFEST_SNAPSHOTS

    for name in ("customer-service", "smart-procurement", "good-question"):
        sk, errs = build_suite_skeleton(MANIFEST_SNAPSHOTS[name], agent_name=name)
        assert errs == [], f"{name} 骨架生成失败: {errs}"
        assert sk["cases"], f"{name} 无骨架 case"
