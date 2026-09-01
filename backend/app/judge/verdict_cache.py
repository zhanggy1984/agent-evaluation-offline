"""judge 判分结果缓存（进程内 LRU + TTL，纯优化）。

背景：judge 是确定性意图调用（temperature=0，同输入期望同输出），平台核心用法
「同一测试集对同一 agent 反复跑」会让跨 run 的同一 (case, answer, 维度) 重复调
judge LLM。本缓存复用历史多数决判分，省去重复调用。

关键设计（方案评审确认）：
- 读写分离：worker._process_one 循环内只读「历史写入」的缓存，循环结束后才写本
  task 的多数决值 → 单 task 内 repeat 保持独立采样，只有跨 task/run 命中。
- key 绑定「实际发给 judge 的输入」（含 judge 模型/endpoint/截断后 reasoning 等），
  任一输入变 key 变，天然失效，无需显式失效；TTL 兜底模型行为漂移。
- enabled 默认 False：生产由 _drain_once 每轮 configure 显式开启；测试默认关，
  保证现有 _process_one 测试基线零回归（Mock client.model 不可 JSON 序列化）。
- 纯优化静默降级：调用方对 key 构造/get/set 各自 try/except，本模块异常绝不
  影响 judge 主链路。
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict

_DEFAULT_MAX_ENTRIES = 10000
_DEFAULT_TTL = 86400.0  # 24h：模型行为漂移兜底（key 已绑定版本，正常命中不依赖 TTL）


class VerdictCache:
    """进程内 LRU + TTL 判分缓存。同步方法、内部无 await（单进程单线程原子，无需锁）。

    仅缓存成功判分的多数决值 {level, score, reason}；answer 等大字段只进 key 不进 value。
    """

    def __init__(self, max_entries: int = _DEFAULT_MAX_ENTRIES,
                 default_ttl: float = _DEFAULT_TTL, enabled: bool = False) -> None:
        # enabled 默认 False：生产由 _drain_once configure 显式开启；测试默认关保证
        # 现有 _process_one 测试零回归（Mock client 的 model/base_url 不可 JSON 序列化）。
        self.max_entries = max_entries
        self.default_ttl = default_ttl
        self.enabled = enabled
        self._d: OrderedDict[str, tuple[float, dict]] = OrderedDict()
        self._hits = 0
        self._misses = 0

    def configure(self, *, enabled: bool, ttl: float, max_entries: int) -> None:
        """按轮次配置刷新（is_hot 热生效入口）。改 max_entries 不缩容，仅影响后续写入。

        热关（True→False）视为「模型可能漂移，旧判分不可复用」→ 顺带 clear 清空脏数据
        （否则重新开启时会命中漂移前的旧判分）。下降沿才清，幂等：关着重复 configure 不重复清。
        """
        if self.enabled and not enabled:
            self.clear()
        self.enabled = enabled
        self.default_ttl = ttl
        self.max_entries = max_entries

    def get(self, key: str) -> dict | None:
        """查缓存。命中返回多数决值 dict；未命中/过期/disabled 返回 None（disabled 不计数）。"""
        if not self.enabled:
            return None
        entry = self._d.get(key)
        if entry is None:
            self._misses += 1
            return None
        expiry, value = entry
        if expiry < time.monotonic():
            del self._d[key]  # 惰性过期
            self._misses += 1
            return None
        self._d.move_to_end(key)  # LRU 刷新：热 key 保留
        self._hits += 1
        return value

    def set(self, key: str, value: dict, ttl: float | None = None) -> None:
        """写缓存（TTL 缺省用当前 default_ttl）。超 max_entries 淘汰最旧（FIFO 淘汰点）。"""
        if not self.enabled:
            return
        self._d[key] = (time.monotonic() + (ttl or self.default_ttl), value)
        self._d.move_to_end(key)
        while len(self._d) > self.max_entries:
            self._d.popitem(last=False)

    def clear(self) -> None:
        """清空全部条目（测试用；进程重启自然清空）。"""
        self._d.clear()

    def stats(self) -> tuple[int, int]:
        """返回 (hits, misses)。misses 不含 disabled 期的调用（enabled 才计数）。"""
        return self._hits, self._misses

    def reset_stats(self) -> None:
        self._hits = 0
        self._misses = 0


def judge_cache_key(*, dimension, template, case_input, golden_answer, reference_docs,
                    agent_output, agent_reasoning, agent_tool_calls,
                    rubric_version, model, base_url) -> str:
    """判分缓存 key：绑定全部「实际发给 judge 的输入」，任一维度变 key 变。

    必须与 worker._process_one 传给 client.judge 的参数 1:1（含截断后 reasoning/
    tool_calls），否则表达式漂移会变成静默永久 miss（正确但无用）。base_url 必须含
    （judge 供应商热改换 endpoint 时旧判定不可复用）。明文前缀 'judge:v1:' 便于排查，
    结构变更时升版本号让旧缓存自然失效。
    """
    payload = {
        "dimension": dimension,
        "template": template,
        "case_input": case_input,
        "golden_answer": golden_answer,
        "reference_docs": reference_docs,
        "agent_output": agent_output,
        "agent_reasoning": agent_reasoning,
        "agent_tool_calls": agent_tool_calls,
        "rubric_version": rubric_version,
        "model": model,
        "base_url": base_url,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()[:32]
    return f"judge:v1:{digest}"


verdict_cache = VerdictCache()
