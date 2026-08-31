"""SSE 解析器（自写，处理半帧/多行 data/id/心跳）。

帧格式：`event: <type>\ndata: <json>\nid: <seq>\n\n`；心跳为单独的 `:` 行。
data 可多行（每行 `data: ` 前缀），按 \n 拼接。
增量 feed：`feed(chunk)` 返回本次累积出的完整事件；半帧自动跨 chunk 保留。
"""
import json
from typing import Any

# C7：单事件无换行数据累积上限。SSE 事件行不该大到这个量级——超过即非 SSE 流/流协议崩坏，
# 防恶意或异常流把 _buf 撑到无界（内存耗尽）。超限抛 SSEParseError（executor 归 sse_parse_error，可重试）。
MAX_BUF_BYTES = 64 * 1024


class SSEParseError(Exception):
    """SSE 解析失败（error_type=sse_parse_error）。"""


class SSEEvent:
    __slots__ = ("type", "data", "id")

    def __init__(self, type: str, data: dict, id: str | None = None):
        self.type = type
        self.data = data
        self.id = id

    def __repr__(self) -> str:
        return f"SSEEvent({self.type}, id={self.id})"


class SSEParser:
    def __init__(self) -> None:
        self._buf = bytearray()
        self._event_type: str | None = None
        self._event_id: str | None = None
        self._data_lines: list[str] = []
        self.last_id: str | None = None  # 7.5c 最近一次事件 id（断流续推依据）

    def feed(self, chunk: bytes) -> list[SSEEvent]:
        """喂入字节，返回新解析出的完整事件。"""
        self._buf.extend(chunk)
        # C7：累积超上限且无换行（不是大事件的跨 chunk 半帧）→ 契约错误，防 _buf 无界增长
        if len(self._buf) > MAX_BUF_BYTES and self._buf.find(b"\n") < 0:
            raise SSEParseError(f"SSE 无换行数据累积超 {MAX_BUF_BYTES} 字节上限，疑似非 SSE 流")
        events: list[SSEEvent] = []
        while True:
            nl = self._buf.find(b"\n")
            if nl < 0:
                break
            line = bytes(self._buf[:nl]).decode("utf-8", errors="replace")
            del self._buf[: nl + 1]
            ev = self._handle_line(line)
            if ev is not None:
                events.append(ev)
        return events

    def _handle_line(self, line: str) -> SSEEvent | None:
        # 心跳/注释：冒号开头（": " 或 ":"）——无内容，忽略
        if line.startswith(":"):
            return None
        if line == "":
            # 空行 = 事件结束
            if self._event_type is None and not self._data_lines:
                return None
            return self._finish_event()
        if ":" in line:
            field, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
        else:
            field, value = line, ""
        if field == "event":
            self._event_type = value or None
        elif field == "data":
            self._data_lines.append(value)
        elif field == "id":
            self._event_id = value or None
        # 其他字段（retry 等）忽略
        return None

    def _finish_event(self) -> SSEEvent:
        type_ = self._event_type or ""
        raw = "\n".join(self._data_lines)
        try:
            data = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as exc:
            raise SSEParseError(f"data JSON 解析失败: {exc}") from exc
        ev = SSEEvent(type=type_, data=data if isinstance(data, dict) else {"value": data},
                      id=self._event_id)
        if ev.id:
            self.last_id = ev.id  # 7.5c reset 前截获，供续推头透出
        self._event_type = None
        self._event_id = None
        self._data_lines = []
        return ev
