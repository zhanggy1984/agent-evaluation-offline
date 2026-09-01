"""P2-C2 system_config 热改校验单测（app/core/sysconfig_schema.py + seed meta 契约）。

核心守卫 test_all_defaults_valid：遍历 DEFAULT_SYSTEM_CONFIG 断言每个默认值通过自身
meta 契约——防「新增/修改配置项时 meta 与默认值漂移」（单一真相源自洽性）。
"""
from app.core.sysconfig_schema import validate_sysconfig_value
from app.seed import DEFAULT_SYSTEM_CONFIG


def _meta(key: str) -> dict:
    return DEFAULT_SYSTEM_CONFIG[key]["meta"]


# ---------------- int ----------------
def test_int_valid():
    assert validate_sysconfig_value("case_timeout", 120, _meta("case_timeout")) is None


def test_int_out_of_range():
    assert validate_sysconfig_value("case_timeout", 0, _meta("case_timeout"))
    assert validate_sysconfig_value("case_timeout", 99999999, _meta("case_timeout"))


def test_int_rejects_bool_float_str():
    # bool 是 int 子类（True==1）必须单独拒绝；float/str 类型不符
    assert validate_sysconfig_value("case_timeout", True, _meta("case_timeout"))
    assert validate_sysconfig_value("case_timeout", 1.5, _meta("case_timeout"))
    assert validate_sysconfig_value("case_timeout", "120", _meta("case_timeout"))


# ---------------- number ----------------
def test_number_valid_accepts_int():
    m = _meta("judge_na_threshold")
    assert validate_sysconfig_value("judge_na_threshold", 0.3, m) is None
    assert validate_sysconfig_value("judge_na_threshold", 1, m) is None  # int 当 number 放行
    assert validate_sysconfig_value("judge_na_threshold", 0, m) is None


def test_number_out_of_range():
    m = _meta("judge_na_threshold")
    assert validate_sysconfig_value("judge_na_threshold", 1.5, m)
    assert validate_sysconfig_value("judge_na_threshold", -0.1, m)


def test_number_rejects_bool():
    assert validate_sysconfig_value("judge_na_threshold", True, _meta("judge_na_threshold"))


# ---------------- nullable ----------------
def test_nullable_allows_none():
    m = _meta("run_timeout")
    assert validate_sysconfig_value("run_timeout", None, m) is None
    assert validate_sysconfig_value("run_timeout", 0, m)  # None 允许，0 仍按 min=1 拒绝


# ---------------- str ----------------
def test_str_max_len():
    m = _meta("judge_llm.base_url")
    assert validate_sysconfig_value("judge_llm.base_url", "", m) is None  # 空=未配置
    assert validate_sysconfig_value("judge_llm.base_url", "x" * 256, m) is None
    assert validate_sysconfig_value("judge_llm.base_url", "x" * 257, m)
    assert validate_sysconfig_value("judge_llm.base_url", 123, m)  # 非 str


# ---------------- bool ----------------
def test_bool_valid():
    m = _meta("judge_cache_enabled")
    assert validate_sysconfig_value("judge_cache_enabled", True, m) is None
    assert validate_sysconfig_value("judge_cache_enabled", False, m) is None


def test_bool_rejects_non_bool():
    # bool 分支：1/0/字符串都不是布尔值（int 分支已单独拒 bool，这里反之）
    m = _meta("judge_cache_enabled")
    assert validate_sysconfig_value("judge_cache_enabled", 1, m)
    assert validate_sysconfig_value("judge_cache_enabled", 0, m)
    assert validate_sysconfig_value("judge_cache_enabled", "true", m)


# ---------------- list ----------------
def test_list_of_str():
    m = _meta("llm_allowlist")
    assert validate_sysconfig_value("llm_allowlist", ["a.com", "b.com"], m) is None
    assert validate_sysconfig_value("llm_allowlist", [], m) is None  # 空数组允许
    assert validate_sysconfig_value("llm_allowlist", ["a.com", 1], m)      # 含非 str 项
    assert validate_sysconfig_value("llm_allowlist", ["a.com"] * 51, m)    # 超 max_items=50
    assert validate_sysconfig_value("llm_allowlist", "not-a-list", m)      # 非 list


# ---------------- 契约完整性 ----------------
def test_missing_meta_rejected():
    assert validate_sysconfig_value("some_key", 1, None)
    assert validate_sysconfig_value("some_key", 1, {})  # 空 meta 等同缺失


def test_all_defaults_valid():
    # 每个默认值必须通过自身 meta 契约（防 meta 与默认值漂移；漏 meta 的 key 在此暴露）
    for key, cfg in DEFAULT_SYSTEM_CONFIG.items():
        assert validate_sysconfig_value(key, cfg["value"], cfg.get("meta")) is None, key
