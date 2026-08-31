"""7.1 ResultAssembler 单测（app/core/assembler.py）。

SSE 事件流 → 统一结果对象：多 delta 拼接、去重（id/ts 兜底）、TTFT/耗时、
ingest_sync 同步灌入。宿主直接跑（纯逻辑，不触 app.core.config）。
用 SimpleNamespace 模拟 SSEEvent（on_event 只访问 type/id/data 三字段）。
"""
from types import SimpleNamespace

from app.core.assembler import ResultAssembler


def _ev(t, data, eid=None):
    return SimpleNamespace(type=t, id=eid, data=data)


def test_answer_and_reasoning_concatenated():
    a = ResultAssembler()
    a.on_event(_ev("answer", {"delta": "你好"}, eid="a1"), 0.1)
    a.on_event(_ev("reasoning", {"delta": "用户问好"}, eid="r1"), 0.2)
    a.on_event(_ev("answer", {"delta": "，请问需要什么帮助"}, eid="a2"), 0.3)
    a.on_event(_ev("done", {}), 0.5)
    assert a.answer == "你好，请问需要什么帮助"
    assert a.reasoning == "用户问好"
    assert a.first_token_elapsed == 0.1  # 首个 answer 增量事件耗时
    assert a.done_elapsed == 0.5


def test_content_field_compat():
    # 只发 content 的 agent（§5.2 路径 A 兼容：delta 或 content 均可采）
    a = ResultAssembler()
    a.on_event(_ev("answer", {"content": "仅 content"}, eid="a1"), 0.2)
    assert a.answer == "仅 content"


def test_dedup_by_id():
    a = ResultAssembler()
    a.on_event(_ev("answer", {"delta": "A"}, eid="x1"), 0.1)
    a.on_event(_ev("answer", {"delta": "A 重复"}, eid="x1"), 0.2)  # 同 id 丢弃
    a.on_event(_ev("answer", {"delta": "B"}, eid="x2"), 0.3)
    assert a.answer == "AB"


def test_dedup_by_ts_no_id_bucketed():
    a = ResultAssembler()
    # 无 id 用 data.ts 兜底：(ts, delta) 组合去重，且按事件类型分桶：answer 与 reasoning 同 ts 不误伤
    a.on_event(_ev("answer", {"delta": "A", "ts": 1.0}, eid=None), 0.1)
    a.on_event(_ev("answer", {"delta": "A", "ts": 1.0}, eid=None), 0.2)  # 同 ts 同 delta：真重发，丢弃
    a.on_event(_ev("answer", {"delta": "B", "ts": 1.0}, eid=None), 0.3)  # 同 ts 不同 delta：正常流式帧，保留
    a.on_event(_ev("reasoning", {"delta": "R", "ts": 1.0}, eid=None), 0.4)
    assert a.answer == "AB"
    assert a.reasoning == "R"


def test_same_ts_multi_delta_no_frame_loss():
    # 回归（run 158 answer 残缺根因）：gq token 事件无 id、ts 毫秒级，LLM 流式同毫秒多 chunk
    # （不同 delta）被旧纯 ts 去重误杀 → 改为 (ts, delta) 组合后全部保留，answer 不丢字
    a = ResultAssembler()
    for d in ["你", "好", "，", "我", "是", "文", "档", "助", "手"]:
        a.on_event(_ev("answer", {"content": d, "delta": d, "ts": 1700000000000}, eid=None), 0.1)
    assert a.answer == "你好，我是文档助手"


def test_single_events_not_deduped_by_ts():
    # usage/meta/done 不去重：真实 agent 可能同毫秒多事件，按 ts 去重会误丢终结事件
    a = ResultAssembler()
    a.on_event(_ev("usage", {"prompt_tokens": 10, "ts": 1.0}, eid=None), 0.1)
    a.on_event(_ev("usage", {"prompt_tokens": 20, "ts": 1.0}, eid=None), 0.2)
    a.on_event(_ev("done", {"ts": 1.0}, eid=None), 0.3)
    assert a.usage == {"prompt_tokens": 20, "ts": 1.0}  # 覆盖取最后一次（整条 data）


def test_meta_usage_tool_calls():
    a = ResultAssembler()
    a.on_event(_ev("meta", {"trace_id": "t1"}, eid="m1"), 0.1)
    a.on_event(_ev("tool_call", {"name": "lookup", "args": "{}"}, eid="t1"), 0.2)
    a.on_event(_ev("tool_call", {"name": "calc", "args": "{}"}, eid="t2"), 0.3)
    assert a.meta == {"trace_id": "t1"}
    assert len(a.tool_calls) == 2
    assert a.tool_calls[0]["name"] == "lookup"
    u = a.to_unified()
    assert u["answer"] == "" and u["usage"] is None  # 未发的字段为空/None


def test_first_token_elapsed_none_without_answer():
    a = ResultAssembler()
    a.on_event(_ev("done", {}), 0.5)
    assert a.first_token_elapsed is None  # 无 answer 增量 → 无 TTFT
    assert a.done_elapsed == 0.5


def test_ingest_sync():
    a = ResultAssembler()
    unified = {
        "answer": "同步答案", "reasoning": "同步推理",
        "tool_calls": [{"name": "x"}], "usage": {"prompt_tokens": 1},
        "meta": {"m": 1},
    }
    a.ingest_sync(unified, elapsed=0.4)
    assert a.answer == "同步答案"
    assert a.reasoning == "同步推理"
    assert a.tool_calls == [{"name": "x"}]
    assert a.usage == {"prompt_tokens": 1}
    assert a.meta == {"m": 1}
    assert a.done_elapsed == 0.4
    assert a.first_token_elapsed is None  # 同步变体接口级标 N/A


def test_timing_entry_uses_done_when_present():
    a = ResultAssembler()
    a.on_event(_ev("done", {}), 0.8)
    te = a.timing_entry()
    assert te["end_ts"] == 0.8
    assert te["start_ts"] is not None
    assert te["first_token_ts"] is None


def test_answer_overflow_raises_contract():
    """C7：answer 累积超 16MB 上限 → 契约错误（agent 异常流提前拦截）。"""
    import pytest
    from app.core.assembler import MAX_ANSWER_CHARS
    from app.core.sse_parser import SSEParseError

    a = ResultAssembler()
    with pytest.raises(SSEParseError):
        a.on_event(_ev("answer", {"delta": "x" * (MAX_ANSWER_CHARS + 1)}, eid="a1"), 0.1)
