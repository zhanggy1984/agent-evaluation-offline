"""阶段 7 空库重建 seed 数据不变量（宿主直接跑——seed_data 是纯模块，不触 app.core.config）。

覆盖：SEED_AGENTS 四家结构完整性、gold 用例唯一且带金标准分、SEED_MODEL_PRICES
版本化价格、SEED_USERS 角色合法性。DEFAULT_SYSTEM_CONFIG 项数与库内行数断言
由容器 verify_fresh_start.py 覆盖（seed.py 顶层 import security → 宿主无法 import）。
"""
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
