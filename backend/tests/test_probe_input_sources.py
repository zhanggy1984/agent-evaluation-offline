"""契约探测输入三级来源：resolve_probe_input（URL 即接入，冒烟无需用例）。

核心逻辑：显式 input → adapter_config.probe.input → suite 用例 input。
空 dict / 非 dict 视为无效输入，不参与选择（避免空 input 渲染出空请求）。
"""
from app.api.agents import resolve_probe_input


def test_explicit_wins():
    inp, src = resolve_probe_input(
        {"content": "显式输入"}, {"probe": {"input": {"content": "声明式"}}}, {"content": "用例输入"})
    assert (inp, src) == ({"content": "显式输入"}, "explicit")


def test_adapter_probe_second():
    inp, src = resolve_probe_input(
        None, {"probe": {"input": {"content": "声明式"}}}, {"content": "用例输入"})
    assert (inp, src) == ({"content": "声明式"}, "adapter_probe")


def test_suite_case_fallback():
    inp, src = resolve_probe_input(None, {}, {"content": "用例输入"})
    assert (inp, src) == ({"content": "用例输入"}, "suite_case")


def test_no_source():
    inp, src = resolve_probe_input(None, {}, None)
    assert (inp, src) == (None, "")


def test_empty_explicit_ignored_falls_to_adapter():
    """显式传空 dict 视为无效，落到第二级。"""
    inp, src = resolve_probe_input({}, {"probe": {"input": {"content": "声明式"}}}, None)
    assert (inp, src) == ({"content": "声明式"}, "adapter_probe")


def test_non_dict_explicit_ignored_falls_to_suite():
    """显式传非 dict（如字符串）视为无效，落到第三级。"""
    inp, src = resolve_probe_input("bad", {}, {"content": "用例输入"})
    assert (inp, src) == ({"content": "用例输入"}, "suite_case")


def test_probe_cfg_without_input_ignored():
    """probe 段存在但无 input / input 非 dict → 跳过，落 suite。"""
    inp, src = resolve_probe_input(None, {"probe": {"timeout": 30}}, {"a": 1})
    assert (inp, src) == ({"a": 1}, "suite_case")
    inp, src = resolve_probe_input(None, {"probe": {"input": []}}, {"a": 1})
    assert (inp, src) == ({"a": 1}, "suite_case")


def test_adapter_config_none_safe():
    inp, src = resolve_probe_input(None, None, {"a": 1})
    assert (inp, src) == ({"a": 1}, "suite_case")
