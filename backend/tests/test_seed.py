"""阶段 7 空库重建 seed 数据不变量（宿主直接跑——seed_data 是纯模块，不触 app.core.config）。

覆盖：SEED_AGENTS 四家结构完整性、gold 用例唯一且带金标准分、SEED_MODEL_PRICES
版本化价格、SEED_USERS 角色合法性。DEFAULT_SYSTEM_CONFIG 项数与库内行数断言
由容器 verify_fresh_start.py 覆盖（seed.py 顶层 import security → 宿主无法 import）。

Q3：另覆盖 manifest v2 单一真相源不变量——SEED_AGENTS 与 MANIFEST_SNAPSHOTS 一一对应、
contract_version 统一 2.0、adapter_config 由快照派生、seed.py 写库注入 _manifest_v2 契约。
"""
import pytest

from app import seed_data
from app.models.user import ROLE


def _all_cases():
    for spec in seed_data.SEED_AGENTS:
        for c in spec["sample_suite"]["cases"]:
            yield spec["name"], c


def test_seed_agents_four_and_unique():
    names = [spec["name"] for spec in seed_data.SEED_AGENTS]
    assert len(names) == 4
    assert len(set(names)) == 4
    assert {"customer-service", "contract-check", "smart-procurement", "good-question"} <= set(names)


def test_seed_agents_no_plaintext_credentials():
    # P2-D17：被评 agent 凭证不得出现在 seed_data 源码（明文 → env 注入）
    for spec in seed_data.SEED_AGENTS:
        assert spec.get("auth_secrets") is None, f"{spec['name']} 含明文凭证"


def test_load_agent_secrets(monkeypatch):
    # P2-D17：凭证解析——缺 env / 坏 JSON → 匿名（空 dict）；合法 JSON → 按 agent name 提取
    from app.seed import _load_agent_secrets  # conftest 兜底注入 security env，宿主可 import

    monkeypatch.delenv("AGENT_AUTH_SECRETS", raising=False)
    assert _load_agent_secrets() == {}

    monkeypatch.setenv("AGENT_AUTH_SECRETS",
                       '{"customer-service": {"username": "admin", "password": "x"}}')
    got = _load_agent_secrets()
    assert got["customer-service"]["password"] == "x"

    monkeypatch.setenv("AGENT_AUTH_SECRETS", "{bad json")
    assert _load_agent_secrets() == {}


def test_each_agent_complete():
    for spec in seed_data.SEED_AGENTS:
        assert spec["base_url"] and spec["adapter_type"] == "config"
        assert spec["interfaces"], f"{spec['name']} 缺接口"
        assert spec["scenes"], f"{spec['name']} 缺场景"
        suite = spec["sample_suite"]
        assert suite["name"] and suite["cases"], f"{spec['name']} 缺示例用例"


def test_cases_shape():
    for agent, c in _all_cases():
        assert c["name"] and c["input_type"] in ("text", "file", "conversation"), f"{agent}/{c['name']}"
        assert c["input"] and c["assertions"] and c["metrics"], f"{agent}/{c['name']} 断言/metrics 缺失"
        assert c.get("interface"), f"{agent}/{c['name']} 缺 interface"


def test_gold_case_unique_with_scores():
    golds = [c for _, c in _all_cases() if c.get("is_gold")]
    assert len(golds) == 1
    scores = golds[0]["expected"].get("judge_gold_scores")
    # 打招呼为纯问候无推理内容：judge 实判 reasoning_quality=0，该维度不参与
    # 漂移检测（阶段 4 #8：误标 80 致空库首测即告警，已从 seed 移除，仅剩 factuality）
    assert scores and len(scores) >= 1
    assert "factuality" in scores
    assert all(isinstance(s["score"], (int, float)) for s in scores.values())


def test_model_prices_versioned():
    prices = seed_data.SEED_MODEL_PRICES
    assert len(prices) >= 2
    keys = {(p["model"], p["effective_from"]) for p in prices}
    assert len(keys) == len(prices)  # 主键 model+effective_from 唯一
    for p in prices:
        assert p["input_price"] > 0 and p["output_price"] > 0


def test_seed_users_roles():
    """demo 用户只声明用户名/角色；密码一律走 env 注入（P0 安全收敛，禁止明文落源码）。"""
    users = {u["username"]: u for u in seed_data.SEED_USERS}
    assert {"evaluator", "viewer"} <= set(users)
    for u in users.values():
        assert u["role"] in ROLE
        assert "password" not in u, "demo 账号密码禁止硬编码在 seed_data（须 env 注入）"


# ---------------- Q3：manifest v2 单一真相源不变量 ----------------

def test_seed_agents_contract_version_20():
    # 4 家迁移 manifest v2：agent 级 + 接口级 contract_version 统一 2.0
    for spec in seed_data.SEED_AGENTS:
        assert spec["contract_version"] == "2.0", f"{spec['name']} agent 级 version 非 2.0"
        for iface in spec["interfaces"]:
            assert iface["contract_version"] == "2.0", f"{spec['name']}/{iface['name']} 接口 version 非 2.0"


def test_manifest_snapshots_match_seed_agents():
    # 快照与 SEED_AGENTS 一一对应（名称全集相等）
    snap_names = set(seed_data.MANIFEST_SNAPSHOTS)
    agent_names = {s["name"] for s in seed_data.SEED_AGENTS}
    assert snap_names == agent_names == {"customer-service", "contract-check",
                                         "smart-procurement", "good-question"}


def test_adapter_config_derived_from_snapshot():
    # SEED_AGENTS.adapter_config 必须等于快照 build 派生值（无 _manifest_v2 内嵌）——
    # 手写 adapter_config 与快照双维护一旦出现即红
    from app.core.contracts_v2 import build_adapter_config

    for spec in seed_data.SEED_AGENTS:
        name = spec["name"]
        draft, errs = build_adapter_config(seed_data.MANIFEST_SNAPSHOTS[name])
        assert errs == [] and draft is not None, f"{name} 快照 build 失败: {errs}"
        assert spec["adapter_config"] == draft.adapter_config, f"{name} 派生值不一致"
        assert "_manifest_v2" not in spec["adapter_config"], f"{name} 原始 adapter_config 不应含快照"


def test_seed_injects_manifest_v2_contract():
    # seed.py::_seed_agents 写库表达式契约（Q3）：adapter_config 内嵌快照且其余键与派生值一致
    for spec in seed_data.SEED_AGENTS:
        name = spec["name"]
        written = {**spec["adapter_config"], "_manifest_v2": seed_data.MANIFEST_SNAPSHOTS[name]}
        assert written["_manifest_v2"] == seed_data.MANIFEST_SNAPSHOTS[name]
        assert {k: v for k, v in written.items() if k != "_manifest_v2"} == spec["adapter_config"]


# ---------------- Q4：seed 校验文件型 agent 样例文件 ----------------

def test_verify_sample_files(monkeypatch, tmp_path):
    """文件存在即通过、缺失即报错（把「人工预置」变「显式契约」）。

    校验对象：adapter_config.probe.input.file_path（通用声明）+ sample_suite 用例
    input.file_path（seed 示例用例）。
    """
    from app import seed_data
    from app.adapters import base as base_mod
    from app.seed import _verify_sample_files

    monkeypatch.setattr(base_mod, "_UPLOADS_DIR", str(tmp_path))
    (tmp_path / "real.pdf").write_bytes(b"pdf")
    fake_agents = [
        {"name": "file-agent",
         "adapter_config": {"probe": {"input": {"file_path": str(tmp_path / "real.pdf")}}},
         "sample_suite": {"cases": []}},
        {"name": "text-agent",
         "adapter_config": {"probe": {"input": {"content": "hi"}}},
         "sample_suite": {"cases": []}},
    ]
    monkeypatch.setattr(seed_data, "SEED_AGENTS", fake_agents)
    _verify_sample_files()  # 全部文件存在，不抛

    # case input 引用缺失文件 → 报错（seed 示例用例路径也是校验对象）
    fake_agents[0]["sample_suite"]["cases"] = [
        {"name": "c1", "input": {"file_path": str(tmp_path / "missing.pdf")}}]
    with pytest.raises(RuntimeError, match="样例文件缺失"):
        _verify_sample_files()
