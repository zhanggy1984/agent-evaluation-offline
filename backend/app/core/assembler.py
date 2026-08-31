"""ResultAssembler：把 SSE 事件流拼装为统一结果对象（§5.3）。

- answer/reasoning：多 delta 拼接（list+join）
- 按事件 id 去重（set 存已见 id；id 缺失时用 data 内 ts 兜底）
- TTFT/端到端用评测端 perf_counter（不依赖 agent 时钟）
"""
import time

from app.core.sse_parser import SSEParseError

# C7：answer/reasoning 累积上限（对齐 DB MEDIUMTEXT 16MB）。agent 异常流把 answer 撑到无界
# 不再现实（列本身 16MB），提前拦截防内存峰值与后续入库撑爆；超限报契约错误。
MAX_ANSWER_CHARS = 16 * 1024 * 1024


class ResultAssembler:
    def __init__(self) -> None:
        self._answer: list[str] = []
        self._reasoning: list[str] = []
        self._answer_len = 0  # C7：累计字符数（避免每次 join 求 len 的 O(n²)）
        self._reasoning_len = 0
        self._tool_calls: list[dict] = []
        self._usage: dict | None = None
        self._meta: dict | None = None
        self._seen_ids: set[str] = set()
        self._seen_ts: dict[str, set[tuple]] = {}  # 按事件类型分桶：(ts, delta) 组合，reasoning 与 answer 不互相误伤
        self._first_token_at: float | None = None  # 相对本用例起始的耗时（s）
        self._done_at: float | None = None
        self._start = time.perf_counter()

    def on_event(self, ev, elapsed: float) -> None:
        """ev: SSEEvent；elapsed: 本事件相对请求发出的秒数（评测端时钟）。"""
        t = ev.type
        # 增量事件（answer/reasoning）需去重：优先 id，无 id 用 data.ts 兜底。
        # 单次事件（usage/meta/done/tool_call）不去重——真实 agent 可能同毫秒多事件
        # （如 usage 与 done 同 ts），按 ts 去重会误丢终结事件。
        if t in ("answer", "reasoning"):
            if ev.id is not None:
                if ev.id in self._seen_ids:
                    return
                self._seen_ids.add(ev.id)
            else:
                # 无 id 兜底去重：纯 ts 去重会误杀同毫秒多个真实 delta（gq token 无 id、ts 毫秒级，
                # LLM 流式同毫秒多 chunk 即被丢帧致 answer 残缺）。改 (ts, delta) 组合——
                # 同 ts 同 delta 才视为重复（真重发）；同 ts 不同 delta 为正常流式帧，全保留。
                ts = ev.data.get("ts")
                if ts is not None:
                    delta = ev.data.get("delta") or ev.data.get("content")
                    bucket = self._seen_ts.setdefault(t, set())
                    key = (ts, delta)
                    if key in bucket:
                        return
                    bucket.add(key)

        if t == "meta":
            self._meta = ev.data
        elif t == "answer":
            if self._first_token_at is None:
                self._first_token_at = elapsed
            # 兼容读 delta 或 content（路径 A：good-question 双字段都发；只发 content 的 agent 也能采）
            delta = ev.data.get("delta") or ev.data.get("content", "")
            if delta:
                # C7：16MB 保护，超限报契约错误（异常流提前拦截）
                if self._answer_len + len(delta) > MAX_ANSWER_CHARS:
                    raise SSEParseError("answer 累积超 16MB 上限，疑似异常流")
                self._answer.append(delta)
                self._answer_len += len(delta)
        elif t == "reasoning":
            delta = ev.data.get("delta") or ev.data.get("content", "")
            if delta:
                if self._reasoning_len + len(delta) > MAX_ANSWER_CHARS:
                    raise SSEParseError("reasoning 累积超 16MB 上限，疑似异常流")
                self._reasoning.append(delta)
                self._reasoning_len += len(delta)
        elif t == "tool_call":
            self._tool_calls.append(ev.data)
        elif t == "usage":
            self._usage = ev.data  # 单条，覆盖（取最后一次）
        elif t == "done":
            self._done_at = elapsed

    def ingest_sync(self, unified: dict, elapsed: float) -> None:
        """同步变体（§5.2）：一次性灌入统一结果。TTFT 不适用（接口级标 N/A）。"""
        if unified.get("meta"):
            self._meta = unified["meta"]
        ans = unified.get("answer")
        if ans:
            # C7：同步灌入路径同样受 16MB 上限保护
            if self._answer_len + len(ans) > MAX_ANSWER_CHARS:
                raise SSEParseError("answer 累积超 16MB 上限，疑似异常流")
            self._answer.append(ans)
            self._answer_len += len(ans)
        rs = unified.get("reasoning")
        if rs:
            if self._reasoning_len + len(rs) > MAX_ANSWER_CHARS:
                raise SSEParseError("reasoning 累积超 16MB 上限，疑似异常流")
            self._reasoning.append(rs)
            self._reasoning_len += len(rs)
        for tc in unified.get("tool_calls") or []:
            self._tool_calls.append(tc)
        if unified.get("usage"):
            self._usage = unified["usage"]
        self._done_at = elapsed

    # ---- 只读结果 ----
    @property
    def answer(self) -> str:
        return "".join(self._answer)

    @property
    def reasoning(self) -> str:
        return "".join(self._reasoning)

    @property
    def tool_calls(self) -> list[dict]:
        return list(self._tool_calls)

    @property
    def usage(self) -> dict | None:
        return self._usage

    @property
    def meta(self) -> dict | None:
        return self._meta

    @property
    def first_token_elapsed(self) -> float | None:
        """TTFT（评测端时钟）。无 answer 增量时为 None。"""
        return self._first_token_at

    @property
    def done_elapsed(self) -> float | None:
        return self._done_at

    def timing_entry(self) -> dict:
        end = self._done_at if self._done_at is not None else time.perf_counter() - self._start
        return {
            "start_ts": self._start,
            "first_token_ts": self._first_token_at,
            "end_ts": end,
        }

    def to_unified(self) -> dict:
        """统一结果对象 {answer, reasoning, tool_calls, usage, meta}（评分层只认它）。"""
        return {
            "answer": self.answer,
            "reasoning": self.reasoning,
            "tool_calls": self.tool_calls,
            "usage": self.usage,
            "meta": self._meta,
        }
