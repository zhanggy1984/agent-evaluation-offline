"""Q2 scaffold v2 端点集成测试（容器真库，直接调协程绕过 FastAPI Depends）。

覆盖 hook 指出的端点层缺口：
- POST /agents/{id}/adapter：落库（adapter_config + _manifest_v2 快照 + contract_version）、
  400 无 contract 段、400 硬错误拒绝
- POST /agents/{id}/discover v2 分支：mock HTTP 客户端返回 v2 payload → ok:true +
  manifest_version + adapter 块；adapter 硬错误软语义（ok:true + valid=false）；
  agent 端点返回非 dict JSON 不 500（走 v1 ok=false 诊断语义）

discover 的 HTTP 出站用 monkeypatch 替换 build_agent_client（不真连 agent）。
"""
import json

import pytest

pytest.importorskip("aiomysql")

from app.api import scaffold
from app.api.agents import ProbeBody, probe_agent
from app.api.scaffold import AdapterConfirmBody, SkeletonBody, case_skeleton, confirm_adapter, discover_agent
from app.core.errors import ApiError
from app.models import Agent
from helpers import make_agent, make_interface

VALID_V2 = {
    "agent": "demo", "contract_version": "2.0",
    "interfaces": [
        {"name": "chat", "path": "/chat", "contract_type": "sse", "llm": True},
    ],
    "scenes": [{"tag": "greeting", "description": "问候"}],
    "contract": {
        "type": "sse", "timeout": 120,
        "request": {"path": "/chat", "method": "POST",
                    "body": {"content": "{{input.content}}"}},
    },
}


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, timeout=30.0):
        return FakeResponse(self._payload)


def _patch_http(monkeypatch, payload):
    monkeypatch.setattr(scaffold, "build_agent_client",
                        lambda extra_cidrs=None: FakeClient(payload))


async def _seed_agent(env, db) -> Agent:
    agent = make_agent(name="it-q2-scaffold")
    db.add(agent)
    await db.flush()
    env.agents.append(agent)
    await db.commit()
    return agent


# ---------------- POST /agents/{id}/adapter ----------------

@pytest.mark.asyncio(loop_scope="session")
async def test_confirm_adapter_persists(db, env):
    agent = await _seed_agent(env, db)
    resp = await confirm_adapter(agent.id, AdapterConfirmBody(manifest=VALID_V2),
                                 _=object(), db=db)
    assert resp["data"]["agent_id"] == agent.id
    assert resp["data"]["requires_auth"] is False
    assert resp["data"]["input_fields"] == ["content"]
    await db.refresh(agent)
    assert agent.contract_version == "2.0"
    cfg = agent.adapter_config
    assert cfg["contract_type"] == "sse"
    assert cfg["request"]["body"] == {"content": "{case.input.content}"}
    assert cfg["_manifest_v2"] == VALID_V2  # 快照内嵌，Q3 单一真相源铺路


@pytest.mark.asyncio(loop_scope="session")
async def test_confirm_adapter_rejects_v1_no_contract(db, env):
    agent = await _seed_agent(env, db)
    v1 = {k: v for k, v in VALID_V2.items() if k != "contract"}
    with pytest.raises(ApiError) as exc:
        await confirm_adapter(agent.id, AdapterConfirmBody(manifest=v1), _=object(), db=db)
    assert "无 contract 段" in exc.value.message


@pytest.mark.asyncio(loop_scope="session")
async def test_confirm_adapter_rejects_hard_error(db, env):
    agent = await _seed_agent(env, db)
    bad = json.loads(json.dumps(VALID_V2))
    bad["contract"]["request"]["headers"] = {"Authorization": "Bearer {{prepare.nosuch.tok}}"}
    with pytest.raises(ApiError) as exc:
        await confirm_adapter(agent.id, AdapterConfirmBody(manifest=bad), _=object(), db=db)
    assert "引用不存在的 prepare 步骤" in exc.value.message
    await db.refresh(agent)
    assert agent.adapter_config == {}  # 拒绝后未落库（make_agent 默认空 dict）


# ---------------- POST /agents/{id}/discover v2 ----------------

@pytest.mark.asyncio(loop_scope="session")
async def test_discover_v2_returns_adapter_block(db, env, monkeypatch):
    agent = await _seed_agent(env, db)
    _patch_http(monkeypatch, VALID_V2)
    resp = await discover_agent(agent.id, _=object(), db=db)
    assert resp["data"]["ok"] is True
    assert resp["data"]["manifest_version"] == "2.0"
    assert resp["data"]["agent"] == "demo"
    assert resp["data"]["adapter"]["valid"] is True
    assert resp["data"]["adapter"]["draft"]["request"]["body"] == {"content": "{case.input.content}"}
    assert resp["data"]["interfaces"]  # diff 数据照常返回


@pytest.mark.asyncio(loop_scope="session")
async def test_discover_v2_adapter_hard_error_soft(db, env, monkeypatch):
    agent = await _seed_agent(env, db)
    bad = json.loads(json.dumps(VALID_V2))
    bad["contract"]["request"]["body"]["bad"] = "{{foo.bar}}"
    _patch_http(monkeypatch, bad)
    resp = await discover_agent(agent.id, _=object(), db=db)
    assert resp["data"]["ok"] is True  # 软语义：接口/场景照常发现
    assert resp["data"]["manifest_version"] == "2.0"
    assert resp["data"]["adapter"]["valid"] is False
    assert resp["data"]["adapter"]["draft"] is None
    assert any("未知占位符域: foo.bar" in e for e in resp["data"]["adapter"]["errors"])


@pytest.mark.asyncio(loop_scope="session")
async def test_discover_v2_non_dict_payload_no_500(db, env, monkeypatch):
    """agent 端点返回 JSON 非对象（list）→ 不得 500，走 v1 语义 ok=false（诊断不抛）。"""
    agent = await _seed_agent(env, db)
    _patch_http(monkeypatch, ["not", "a", "dict"])
    resp = await discover_agent(agent.id, _=object(), db=db)
    assert resp["data"]["ok"] is False
    assert resp["data"]["errors"]  # 非空可读错误


# ---------------- Q3：discover adapter_drift ----------------

async def _seed_agent_with_snapshot(env, db, payload: dict, name: str = "it-q3-drift") -> Agent:
    """seed 一个带 _manifest_v2 快照的 agent（模拟 Q3 落库形态）。"""
    agent = make_agent(name=name)
    agent.adapter_config = {"_manifest_v2": payload}
    db.add(agent)
    await db.flush()
    env.agents.append(agent)
    await db.commit()
    return agent


@pytest.mark.asyncio(loop_scope="session")
async def test_discover_v2_drift_empty_when_snapshot_matches(db, env, monkeypatch):
    """运行时 contract 与已落库快照一致 → adapter_drift=[]。"""
    agent = await _seed_agent_with_snapshot(env, db, VALID_V2)
    _patch_http(monkeypatch, VALID_V2)
    resp = await discover_agent(agent.id, _=object(), db=db)
    assert resp["data"]["ok"] is True
    assert resp["data"]["adapter_drift"] == []


@pytest.mark.asyncio(loop_scope="session")
async def test_discover_v2_drift_reports_agent_change(db, env, monkeypatch):
    """agent 运行时 contract 多出快照外段 → adapter_drift 报漂移（不阻断）。"""
    agent = await _seed_agent_with_snapshot(env, db, VALID_V2)
    drifted = json.loads(json.dumps(VALID_V2))
    drifted["contract"]["timeout"] = 999
    drifted["contract"]["extra_segment"] = 1
    _patch_http(monkeypatch, drifted)
    resp = await discover_agent(agent.id, _=object(), db=db)
    assert resp["data"]["ok"] is True  # 漂移提示不阻断发现
    assert any("多出平台快照外段" in e and "extra_segment" in e
               for e in resp["data"]["adapter_drift"])


@pytest.mark.asyncio(loop_scope="session")
async def test_discover_v1_runtime_with_snapshot_drift_hint(db, env, monkeypatch):
    """agent 回退 v1（无 contract 段）+ 平台有快照 → drift 提示「无 contract 段」。"""
    agent = await _seed_agent_with_snapshot(env, db, VALID_V2)
    v1 = {k: v for k, v in VALID_V2.items() if k != "contract"}
    v1["contract_version"] = "1.0"
    _patch_http(monkeypatch, v1)
    resp = await discover_agent(agent.id, _=object(), db=db)
    assert resp["data"]["ok"] is True
    assert any("无 contract 段" in e for e in resp["data"]["adapter_drift"])


# ---------------- Q4：probe 文件型输入前置校验（短路，不触发 HTTP） ----------------

@pytest.mark.asyncio(loop_scope="session")
async def test_probe_input_error_short_circuits(db, env, monkeypatch, tmp_path):
    """文件型探测输入缺样例文件 → input_error=true 短路，不真发请求（省无效探测调用）。"""
    from app.adapters import base as base_mod

    monkeypatch.setattr(base_mod, "_UPLOADS_DIR", str(tmp_path))
    agent = make_agent(name="it-q4-probe")
    agent.adapter_config = {
        "contract_type": "sync",
        "request": {"path": "/v1/chat", "method": "POST",
                    "body": {"content": "{case.input.content}"}},
    }
    db.add(agent)
    await db.flush()
    env.agents.append(agent)
    iface = make_interface(agent.id, "it-q4-iface")
    iface.contract_type = "sync"
    db.add(iface)
    await db.flush()
    env.interfaces.append(iface)
    await db.commit()

    resp = await probe_agent(agent.id, body=ProbeBody(
        input={"file_path": str(tmp_path / "__q4_missing.pdf")}), _=object(), db=db)
    data = resp["data"]
    assert data["ok"] is False
    assert data["input_error"] is True
    for itf in data["interfaces"]:
        assert itf["input_error"] is True
        assert any("样例文件不存在" in e for e in itf["errors"])


# ---------------- Q5：POST /agents/{id}/skeleton（用例骨架，不落库） ----------------

@pytest.mark.asyncio(loop_scope="session")
async def test_skeleton_returns_suite_cases(db, env):
    """v2 快照 → 骨架：每 scene×llm 接口一个 case，probe_input merge，expected 留空。"""
    agent = await _seed_agent_with_snapshot(env, db, VALID_V2, name="it-q5-skeleton")
    resp = await case_skeleton(agent.id, SkeletonBody(probe_input={"content": "你好"}),
                               _=object(), db=db)
    data = resp["data"]
    assert data["agent_id"] == agent.id
    assert data["agent_name"] == "it-q5-skeleton"
    assert data["suite"]["name"] == "it-q5-skeleton 接入示例"
    assert len(data["cases"]) == 1  # VALID_V2: 1 llm 接口 + 1 scene
    c = data["cases"][0]
    assert c["name"] == "greeting-chat"
    assert c["input_type"] == "text"
    assert c["input"]["content"] == "你好"  # probe_input merge
    assert c["expected"] == {}  # 业务知识留空
    assert c["assertions"]  # 可跑最小集


@pytest.mark.asyncio(loop_scope="session")
async def test_skeleton_no_snapshot_400(db, env):
    """无 v2 快照（手工配置 adapter）→ 400 提示，不生成。"""
    agent = make_agent(name="it-q5-nosnap")
    db.add(agent)
    await db.flush()
    env.agents.append(agent)
    await db.commit()
    with pytest.raises(ApiError) as exc:
        await case_skeleton(agent.id, SkeletonBody(), _=object(), db=db)
    assert "无 v2 manifest 快照" in exc.value.message
