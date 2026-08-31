"""C7：sse_parser 无换行数据累积上限（防 _buf 无界增长）。

- 超长无换行块 → SSEParseError（契约错误；executor 归 sse_parse_error 可重试）
- 正常分帧（跨 chunk 半帧 + 多行 data）回归：C7 不改正常路径
"""
import pytest

from app.core.sse_parser import MAX_BUF_BYTES, SSEParser, SSEParseError


def test_oversized_no_newline_raises_contract():
    """C7：无换行累积超上限 → 契约错误（原实现 _buf 无界增长）。"""
    p = SSEParser()
    with pytest.raises(SSEParseError):
        p.feed(b"x" * (MAX_BUF_BYTES + 1))


def test_chunked_event_still_parses():
    """跨 chunk 半帧 + 多行 data 回归：正常事件切分不受上限误伤。"""
    p = SSEParser()
    evs = p.feed(b'event: answer\ndata: {"delta": "a')
    assert evs == []
    evs = p.feed(b'"}\n\n')
    assert len(evs) == 1
    assert evs[0].type == "answer"
    assert evs[0].data == {"delta": "a"}


def test_buf_above_limit_with_newline_ok():
    """累积超上限但有换行（数据行本身就是长行）不误判为协议崩坏。"""
    p = SSEParser()
    payload = '{"delta": "' + "z" * (MAX_BUF_BYTES + 100) + '"}\n\n'
    evs = p.feed(b"data: " + payload.encode())
    assert len(evs) == 1
    assert evs[0].type == ""
    assert "delta" in evs[0].data
