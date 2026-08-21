"""6.5 用例骨架生成纯逻辑单测（只 import app.core.skeleton_gen，零 DB/LLM 依赖，宿主直接跑）。

与 test_dashboard 同模式：不触 app.core.db / llm 客户端，只验证 prompt 组装与容错归一。
"""
from app.core.skeleton_gen import (
    align_cases, build_gen_messages, normalize_case, parse_cases,
)

AGENT = {"name": "customer-service", "adapter_type": "config"}
INTERFACES = [{"name": "chat", "method": "POST",
               "path": "/api/v1/sessions/{sid}/messages", "contract_type": "sse"}]
SCENE = {"scene_tag": "order_query", "description": "订单查询"}
SAMPLES = [{"input_type": "text", "input": {"content": "你好"}}]


def _msgs():
    return build_gen_messages(agent=AGENT, interfaces=INTERFACES, scene=SCENE, samples=SAMPLES)


# ---------------- build_gen_messages ----------------
def test_build_messages_roles():
    msgs = _msgs()
    assert [m["role"] for m in msgs] == ["system", "user"]


def test_build_messages_injects_context():
    user = _msgs()[1]["content"]
    assert "customer-service" in user
    assert "/api/v1/sessions/{sid}/messages" in user
    assert "order_query" in user and "订单查询" in user
    assert '{"content": "你好"}' in user  # 输入样例注入


def test_build_messages_injection_guard():
    user = _msgs()[1]["content"]
    assert "<reference_data>" in user and "</reference_data>" in user
    assert "不予执行" in user


def test_build_messages_no_golden_answer():
    system = _msgs()[0]["content"]
    assert "黄金答案" in system and "不" in system  # 明确约束不生成黄金答案/断言
    assert "JSON 数组" in system


def test_build_messages_requires_interface_field():
    system = _msgs()[0]["content"]
    # 反查对齐的输入契约：每 case 必须带 interface 字段且对应接口清单
    assert '"interface"' in system and "接口清单" in system


# ---------------- parse_cases ----------------
def test_parse_cases_fence():
    text = '```json\n[{"name": "a", "input": {"content": "x"}}]\n```'
    assert parse_cases(text) == [{"name": "a", "input": {"content": "x"}}]


def test_parse_cases_bare_array():
    assert parse_cases('[{"name": "a"}]') == [{"name": "a"}]


def test_parse_cases_non_array_returns_empty():
    assert parse_cases('{"obj": 1}') == []


def test_parse_cases_invalid_returns_empty():
    assert parse_cases("这不是 JSON") == []
    assert parse_cases("") == []


def test_parse_cases_filters_non_dict():
    assert parse_cases('[{"name": "a"}, 1, "x"]') == [{"name": "a"}]


# ---------------- normalize_case ----------------
def test_normalize_invalid_input_type_fallback():
    item = normalize_case({"name": "订单查询", "input_type": "video", "input": {}})
    assert item["input_type"] == "text"


def test_normalize_golden_answer_forced_empty():
    item = normalize_case({"name": "a", "expected": {"golden_answer": "x"},
                           "assertions": [{"op": "eq"}], "metrics": {"f": {"enabled": True}}})
    assert item["expected"] == {} and item["assertions"] == [] and item["metrics"] == {}
    assert "status" not in item  # status 由端点统一设 draft


def test_normalize_conversation_turns():
    item = normalize_case({"name": "多轮", "input_type": "conversation", "input": {"content": "第一轮"},
                           "input_turns": [{"turn": 1, "content": "你好"}, {"bad": 1}]})
    assert item["input_type"] == "conversation"
    assert item["input_turns"] == [{"turn": 1, "content": "你好"}]


def test_normalize_text_ignores_turns():
    item = normalize_case({"name": "a", "input_type": "text", "input": {"content": "x"},
                           "input_turns": [{"turn": 1, "content": "y"}]})
    assert item["input_turns"] is None


def test_normalize_file_placeholder():
    item = normalize_case({"name": "合同校验", "input_type": "file", "input": {}})
    assert item["input_type"] == "file" and item["file_ref"] is None


def test_normalize_string_input_wrapped():
    item = normalize_case({"name": "a", "input": "你好"})
    assert item["input"] == {"content": "你好"}


def test_normalize_missing_name_skipped():
    assert normalize_case({"input": {}}) is None
    assert normalize_case({"name": "   "}) is None


def test_normalize_truncation():
    item = normalize_case({"name": "长" * 200, "description": "描" * 600, "input": {}})
    assert len(item["name"]) <= 128
    assert len(item["description"]) <= 500


# ---------------- align_cases（interface 反查对齐 + 顺序回落 + 精确缺失报告） ----------------
IFACES = [{"name": "chat"}, {"name": "order"}, {"name": "payment"}]


def test_align_by_interface_field_reports_middle_missing():
    cases = [{"name": "a", "interface": "payment"}, {"name": "b", "interface": "chat"}]
    aligned, missing = align_cases(cases, IFACES)
    assert [n for _, n in aligned] == ["payment", "chat"]
    assert missing == ["order"]  # 漏中间的接口精确报告，不再误报尾部


def test_align_fallback_order_when_no_interface():
    cases = [{"name": "a"}, {"name": "b"}]
    aligned, missing = align_cases(cases, IFACES)
    assert [n for _, n in aligned] == ["chat", "order"]
    assert missing == ["payment"]


def test_align_unknown_interface_fallback():
    cases = [{"name": "a", "interface": "不存在的接口"}]
    aligned, missing = align_cases(cases, IFACES)
    assert [n for _, n in aligned] == ["chat"]
    assert missing == ["order", "payment"]


def test_align_dup_interface_second_falls_back():
    cases = [{"name": "a", "interface": "chat"}, {"name": "b", "interface": "chat"}]
    aligned, missing = align_cases(cases, IFACES)
    assert [n for _, n in aligned] == ["chat", "order"]  # 第二个回落空闲接口
    assert missing == ["payment"]


def test_align_wrong_order_resolved_by_name():
    cases = [{"name": "b", "interface": "payment"}, {"name": "a", "interface": "chat"},
             {"name": "c", "interface": "order"}]
    aligned, missing = align_cases(cases, IFACES)
    assert [n for _, n in aligned] == ["payment", "chat", "order"]  # 乱序仍按名对齐
    assert missing == []


def test_align_overflow_ignored():
    cases = [{"name": "a"}] * 5
    aligned, missing = align_cases(cases, IFACES)
    assert len(aligned) == 3 and missing == []
