"""P2-E5 登录限速器内存清理单测：_hits 超 max_keys 后裁剪陈旧 key。

原实现 _hits 只增不删——攻击者换不同 IP/用户名可无界占内存。现 allow() 顺带 _prune：
超过 max_keys 后删除整个窗口内无活动的陈旧 key（key 的 list 全为窗口外时间戳）。
"""
import time

from app.api.auth import _LoginLimiter


def _mk(max_keys=2):
    return _LoginLimiter(window_s=60, max_hits=10, max_keys=max_keys)


def test_prune_removes_stale_keys_when_over_threshold():
    lim = _mk(max_keys=2)
    now = time.monotonic()
    lim._hits = {
        "stale1": [now - 1000],  # 窗口外 → 应被清理
        "stale2": [now - 1000],
        "active": [now - 1],     # 活跃 → 保留
    }
    lim.allow("active")
    assert "stale1" not in lim._hits
    assert "stale2" not in lim._hits
    assert "active" in lim._hits


def test_no_prune_under_threshold():
    lim = _mk(max_keys=100)
    now = time.monotonic()
    lim._hits = {"a": [now - 1000], "b": [now - 1000]}
    lim.allow("a")
    assert set(lim._hits) == {"a", "b"}, "未超上限不应清理（避免误杀窗口内刚访问的 key）"


def test_prune_keeps_all_recent_keys_even_over_threshold():
    lim = _mk(max_keys=1)
    now = time.monotonic()
    lim._hits = {"recent1": [now - 1], "recent2": [now - 2], "old": [now - 1000]}
    lim.allow("recent1")
    assert "old" not in lim._hits
    assert "recent1" in lim._hits
    assert "recent2" in lim._hits, "活跃 key 即使超上限也应保留（只删窗口外）"
