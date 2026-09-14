"""回流信封纯函数单测（SD §5.3/§5.4 校验矩阵逐条落地）。

覆盖 `validate_envelope` / `sanitize_words`（无 IO 纯函数）+ `resolve_agent` /
`resolve_interface`（**用替身 session 只测匹配逻辑，不连库**；SQL 的 WHERE 过滤本身
（agent_id / method）不被本文件覆盖——替身忽略 WHERE，这是本文件的判别力边界）。

判据取向：**每条规则都要有「正例 + 反例」两向**。只验「该驳回的驳回了」会漏掉
「把合法载荷也一并驳回」这一类错法（fail-closed 的方向性错误），故每个必填/可空项
都成对验证。
"""
from types import SimpleNamespace

import pytest

from app.core.error_payload import (
    MAX_WORD_LEN,
    resolve_agent,
    resolve_interface,
    sanitize_words,
    validate_envelope,
)


def _env(**over) -> dict:
    """一份合法信封（batch 1 §2.1 必填全给、可空项一律留空）。"""
    e = {
        "schema_version": "1.0",
        "case_type": "regression_error",
        "payload_id": "p-0001",
        "source": {"agent": "good-question", "interface": "POST /chat", "trace_id": "t-1"},
        "versions": {"trigger_version": "2026.08.31-r47"},
        "evidence": {"input": {"content": "帮我查订单"}},
        "assert": {"no_fallback": {"config_ref": {"wordlist_version": 7}}},
        "no_fallback_config": {"words": ["抱歉，我无法回答"], "wordlist_version": 7},
    }
    for dotted, val in over.items():
        if val is _DELETE:
            _del(e, dotted)
            continue
        _set(e, dotted, val)
    return e


class _Delete:
    pass


_DELETE = _Delete()


def _set(e: dict, dotted: str, val) -> None:
    parts = dotted.split(".")
    cur = e
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = val


def _del(e: dict, dotted: str) -> None:
    parts = dotted.split(".")
    cur = e
    for p in parts[:-1]:
        cur = cur.get(p, {})
    cur.pop(parts[-1], None)


# ---------- 正向：合法载荷必须过 ----------


def test_valid_envelope_passes():
    ok, errors, verdict = validate_envelope(_env())
    assert ok is True, errors
    assert errors == []
    assert verdict == "ok"


def test_optional_fields_missing_do_not_reject():
    """可空六项**全缺**仍须通过——这是 fail-closed 最容易做反的地方。"""
    e = _env()
    for p in ("versions.fix_version", "evidence.output", "evidence.session_snapshot",
              "evidence.retrieve_hit", "source.cluster_id", "source.generation"):
        assert _get(e, p) is None
    ok, errors, verdict = validate_envelope(e)
    assert ok is True, errors
    assert verdict == "ok"


def test_optional_fields_blank_string_do_not_reject():
    """可空项给了**空串**也不算缺（文档：缺省/空不得驳回）。"""
    ok, errors, _ = validate_envelope(
        _env(**{"versions.fix_version": "", "evidence.output": "", "source.cluster_id": ""})
    )
    assert ok is True, errors


def _get(e: dict, dotted: str):
    cur = e
    for p in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


# ---------- 必填：逐个缺失都须驳回，且判 content_gap ----------

REQUIRED = (
    "schema_version", "case_type", "payload_id", "source.agent", "source.interface",
    "source.trace_id", "versions.trigger_version", "evidence.input",
    "assert.no_fallback.config_ref.wordlist_version", "no_fallback_config",
)


def test_each_required_missing_rejects():
    for p in REQUIRED:
        ok, errors, verdict = validate_envelope(_env(**{p: _DELETE}))
        assert ok is False, f"{p} 缺失竟通过"
        assert verdict == "content_gap", f"{p} 缺失 verdict 应为 content_gap，实为 {verdict}"
        assert any(p in msg for msg in errors), f"{p} 的报错未指明缺项：{errors}"


def test_blank_required_rejects():
    """必填项给空串/纯空白 = 未提供（不是「给了个空值」）。"""
    for p, blank in (("source.agent", "   "), ("evidence.input", {}), ("payload_id", "")):
        ok, _, verdict = validate_envelope(_env(**{p: blank}))
        assert ok is False, f"{p}={blank!r} 竟通过"
        assert verdict == "content_gap"


# ---------- 版本域：走 version_drift，不是 content_gap ----------


def test_schema_version_drift():
    ok, errors, verdict = validate_envelope(_env(schema_version="2.0"))
    assert ok is False
    assert verdict == "version_drift", "schema 不支持须判 version_drift（requeue 不愈）"
    assert "2.0" in errors[0]


def test_case_type_not_in_whitelist_is_version_drift():
    ok, _, verdict = validate_envelope(_env(case_type="regression_perf"))
    assert ok is False
    assert verdict == "version_drift"


# ---------- 词表：fail-closed 三态 + 同源同值 ----------


def test_empty_wordlist_rejected_fail_closed():
    ok, errors, verdict = validate_envelope(_env(**{"no_fallback_config.words": []}))
    assert ok is False, "空词表竟通过 —— 违反 fail-closed，会导致兜底判定全进候选"
    assert verdict == "content_gap"
    assert any("words" in m for m in errors)


def test_wordlist_version_mismatch_rejected():
    """assert 区固化版本与 no_fallback_config 不同源 ⇒ 两份词表可能不同代，必须驳回。"""
    ok, errors, verdict = validate_envelope(
        _env(**{"assert.no_fallback.config_ref.wordlist_version": 6})
    )
    assert ok is False, "词表版本不一致竟通过"
    assert verdict == "content_gap"
    assert any("不同源" in m for m in errors)


def test_bad_wordlist_version_type_rejected():
    for bad in ("7", -1, True, None):
        ok, _, verdict = validate_envelope(
            _env(**{"no_fallback_config.wordlist_version": bad})
        )
        assert ok is False, f"wordlist_version={bad!r} 竟通过"
        assert verdict == "content_gap"


# ---------- 净化 ----------


def test_sanitize_strips_and_drops_blanks():
    assert sanitize_words(["  抱歉  ", "", "   ", "好的"]) == ["抱歉", "好的"]


def test_sanitize_drops_overlong():
    long_w = "x" * (MAX_WORD_LEN + 1)
    assert sanitize_words([long_w, "ok"]) == ["ok"]
    assert sanitize_words(["y" * MAX_WORD_LEN]) == ["y" * MAX_WORD_LEN]  # 边界含等号


def test_sanitize_dedupes_keeping_order():
    assert sanitize_words(["b", "a", "b", " a "]) == ["b", "a"]


def test_sanitize_ignores_non_str_and_non_list():
    assert sanitize_words(["a", 1, None, {"x": 1}, "b"]) == ["a", "b"]
    assert sanitize_words(None) == []
    assert sanitize_words("不是数组") == []


def test_sanitize_does_not_change_case():
    """**故意不归一大小写**（见 error_payload 文件头）：归一会让大写兜底话术匹配不上，
    方向正是 R-12 要治的漏判。此用例把该决定钉住，防止后人「顺手补上」。

    ⚠️ 该决定**已被翻案**（任务 #235：归一 + 算子同步改大小写不敏感）。本用例在 #235
    落地时**必须一并改掉**——它现在的绿是「偏离仍在」的绿，不是「行为正确」的绿。
    """
    assert sanitize_words(["Fallback"]) == ["Fallback"]


# ---------- no_fallback_config 形态：fail-open 回归护栏 ----------


def test_no_fallback_config_non_dict_shapes_reject():
    """**回归护栏**：非空且非对象的 no_fallback_config 曾被静默放行。

    成因 = `isinstance(nfc, dict)` 为假时整块跳过，而必填校验又因「非空」放行
    ⇒ 返回 ok=True。这与 §5.3 fail-closed 相反（畸形载荷被当合法收下）。
    """
    for bad in ("x", ["a"], 7, 3.14, True):
        ok, errors, verdict = validate_envelope(_env(**{"no_fallback_config": bad}))
        assert ok is False, f"no_fallback_config={bad!r} 竟通过（fail-open 复发）"
        assert verdict == "content_gap", f"{bad!r} 应判 content_gap，实为 {verdict}"
        assert any("形态非法" in m for m in errors), f"{bad!r} 报错未指明形态：{errors}"


def test_no_fallback_config_blank_shapes_still_reject():
    """修 fail-open 不得把「缺失/空」一并放过：空串/空容器/None 仍须驳回。"""
    for blank in ("", "   ", {}, [], None):
        ok, _, verdict = validate_envelope(_env(**{"no_fallback_config": blank}))
        assert ok is False, f"no_fallback_config={blank!r} 竟通过"
        assert verdict == "content_gap"


# ---------- 映射（替身 session：只验匹配逻辑，不连库） ----------


class _FakeResult:
    def __init__(self, rows) -> None:
        self._rows = rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    """只实现 resolve_* 用到的读面；**忽略 WHERE**（故 method/agent_id 的 SQL 过滤不被覆盖）。"""

    def __init__(self, *groups) -> None:
        self._groups = list(groups)
        self.calls: list = []

    async def execute(self, stmt):
        self.calls.append(stmt)
        return _FakeResult(self._groups.pop(0) if self._groups else [])


@pytest.mark.asyncio
async def test_resolve_agent_hit_miss_and_blank_short_circuit():
    agent = SimpleNamespace(id=1, name="good-question")
    session = _FakeSession([agent])
    assert await resolve_agent(session, "good-question") is agent

    assert await resolve_agent(_FakeSession(), "nope") is None

    # 空/None 直接短路：**不得查库**（省一次往返，也避免把空串当成合法 agent 名去匹配）
    for blank in ("", None):
        s = _FakeSession()
        assert await resolve_agent(s, blank) is None
        assert s.calls == [], f"{blank!r} 竟查了库"


@pytest.mark.asyncio
async def test_resolve_interface_strips_method_prefix():
    """入参 path 形如 "GET /chat"（online 侧 interface 串形态）须剥掉前缀再匹配。"""
    row = SimpleNamespace(method="GET", path="/chat")
    agent = SimpleNamespace(id=1)
    # method 缺省时用前缀里的动词
    assert await resolve_interface(_FakeSession([row]), agent, "", "GET /chat") is row
    # 前缀动词与方法参数同时给：以参数为准，path 仍须剥净（否则段数对不上）
    assert await resolve_interface(_FakeSession([row]), agent, "GET", "GET /chat") is row


@pytest.mark.asyncio
async def test_resolve_interface_wildcard_and_segment_count():
    agent = SimpleNamespace(id=1)
    row = SimpleNamespace(method="POST", path="/order/{id}/detail")
    assert (
        await resolve_interface(_FakeSession([row]), agent, "POST", "/order/123/detail")
    ) is row
    # 段数不等 → 不命中（防 "/a/{b}/c" 被 "/a/{b}/c/d" 撞上）
    assert (
        await resolve_interface(_FakeSession([row]), agent, "POST", "/order/123/detail/x")
    ) is None
    assert await resolve_interface(_FakeSession([row]), agent, "POST", "/order/123") is None
    # 非占位段必须逐字相等
    assert await resolve_interface(_FakeSession([row]), agent, "POST", "/x/{y}/detail") is None


@pytest.mark.asyncio
async def test_resolve_interface_blank_inputs_return_none_without_query():
    s = _FakeSession()
    assert await resolve_interface(s, SimpleNamespace(id=1), "", "") is None
    assert await resolve_interface(s, SimpleNamespace(id=1), None, None) is None
    assert s.calls == [], "空输入竟查了库"
