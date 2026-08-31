"""模块 C：P2-7 配置留痕——三个写接口审计 + config-history 变更历史查询。

mock DB 跑 agents.py 的 set_agent_weights/set_interface_weights/set_targets/agent_config_history。
覆盖：
- 审计：三接口各落一条 audit（detail 带 interface_id + dims{from,to}；targets 含审批流转）
- from 值在赋值前缓存（既有行 → 旧值；新建 → None）
- config-history：join User 取名 + 删除用户兜底「已删除用户」+ detail 透传
"""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.agents import (agent_config_history, set_agent_weights,
                            set_interface_weights, set_targets)
from app.models import AuditLog


def _req():
    return SimpleNamespace(headers={}, client=SimpleNamespace(host="10.0.0.1"))


def _user(role="admin"):
    return SimpleNamespace(id=7, role=role, username="u7")


def _db_with_row(row):
    """execute → scalar_one_or_none 返回既有行（或 None=新建）；db.get → 存在的 agent。"""
    db = AsyncMock()
    db.get.return_value = SimpleNamespace(id=1, enabled=True)

    class _Exec:
        def scalar_one_or_none(self):
            return row

    db.execute.return_value = _Exec()
    return db


def _audits(db):
    """db.add 调用中的 AuditLog 实例（首个 add 是业务 row，其后是审计）。"""
    return [c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], AuditLog)]


# ---------------- 三个写接口审计 ----------------
class TestWeightsAudit:
    @pytest.mark.asyncio
    async def test_agent_weights_audit_with_from(self):
        # 既有行：from 取旧值 0.4；detail 带 interface_id=0（agent 默认）
        row = SimpleNamespace(dimension_code="completeness", weight=0.4)
        db = _db_with_row(row)
        await set_agent_weights(1, SimpleNamespace(weights={"completeness": 0.5}),
                                _req(), _user(), db)
        a = _audits(db)[0]
        assert a.action == "agent.weights.set"
        assert a.target_type == "agent" and a.target_id == "1"
        assert a.user_id == 7
        assert a.detail == {"interface_id": 0,
                            "dims": {"completeness": {"from": 0.4, "to": 0.5}}}

    @pytest.mark.asyncio
    async def test_agent_weights_new_row_from_none(self):
        # 无既有行（新建）：from=None，不误报旧值
        db = _db_with_row(None)
        await set_agent_weights(1, SimpleNamespace(weights={"factuality": 0.3}),
                                _req(), _user(), db)
        a = _audits(db)[0]
        assert a.detail["dims"]["factuality"] == {"from": None, "to": 0.3}

    @pytest.mark.asyncio
    async def test_interface_weights_audit_interface_id(self):
        # 接口级覆盖：detail 带 iid，config-history 可区分默认/覆盖
        row = SimpleNamespace(dimension_code="factuality", weight=0.2)
        db = _db_with_row(row)
        await set_interface_weights(1, 3, SimpleNamespace(weights={"factuality": 0.4}),
                                    _req(), _user(), db)
        a = _audits(db)[0]
        assert a.action == "agent.interface.weights.set"
        assert a.detail == {"interface_id": 3,
                            "dims": {"factuality": {"from": 0.2, "to": 0.4}}}


class TestTargetsAudit:
    @pytest.mark.asyncio
    async def test_targets_audit_includes_approval_flow(self):
        # 双签流转一并留痕：auto → pending_approval（首次改）
        row = SimpleNamespace(dimension_code="completeness", target_score=70.0,
                              calibration_source="手动", approved_by_1=None,
                              approved_by_2=None, approval_status="auto")
        db = _db_with_row(row)
        await set_targets(1, 2, SimpleNamespace(target_scores={"completeness": 80.0}),
                          _req(), _user(), db)
        a = _audits(db)[0]
        assert a.action == "agent.targets.set"
        assert a.detail["interface_id"] == 2
        assert a.detail["dims"]["completeness"] == {
            "from": 70.0, "to": 80.0,
            "approval_status": {"from": "auto", "to": "pending_approval"}}

    @pytest.mark.asyncio
    async def test_targets_second_person_audit_approved(self):
        # 第二人改：审批流转 pending_approval → approved
        row = SimpleNamespace(dimension_code="completeness", target_score=70.0,
                              calibration_source="手动", approved_by_1=1,
                              approved_by_2=None, approval_status="pending_approval")
        db = _db_with_row(row)
        await set_targets(1, 2, SimpleNamespace(target_scores={"completeness": 75.0}),
                          _req(), _user(role="evaluator"), db)
        a = _audits(db)[0]
        assert a.detail["dims"]["completeness"]["approval_status"] == {
            "from": "pending_approval", "to": "approved"}


# ---------------- config-history 查询 ----------------
class TestConfigHistory:
    def _log(self, **kw):
        p = dict(id=1, action="agent.targets.set", target_type="agent", target_id="1",
                 detail={"interface_id": 2,
                         "dims": {"completeness": {"from": 70.0, "to": 80.0}}},
                 ip="1.2.3.4", created_at=datetime(2026, 8, 30, 10, 0, 0))
        p.update(kw)
        return SimpleNamespace(**p)

    @pytest.mark.asyncio
    async def test_history_username_and_fallback(self):
        db = AsyncMock()
        db.get.return_value = SimpleNamespace(id=1, enabled=True)
        rows = [(self._log(id=2), "zhang"), (self._log(id=1), None)]  # 第二行用户已删除

        class _All:
            def all(self):
                return rows

        db.execute.return_value = _All()
        out = (await agent_config_history(1, _user(), db))["data"]
        assert len(out) == 2
        assert out[0]["username"] == "zhang"
        assert out[0]["action"] == "agent.targets.set"
        assert out[0]["interface_id"] == 2
        assert out[0]["ip"] == "1.2.3.4"
        assert out[0]["created_at"] == "2026-08-30T10:00:00"
        assert out[0]["dims"]["completeness"]["from"] == 70.0
        assert out[1]["username"] == "已删除用户"  # C2 兜底

    @pytest.mark.asyncio
    async def test_history_other_agent_empty(self):
        # 按 target_id 过滤：mock 只返回该 agent 流水，查询结构上按 agent 隔离
        db = AsyncMock()
        db.get.return_value = SimpleNamespace(id=9, enabled=True)

        class _Empty:
            def all(self):
                return []

        db.execute.return_value = _Empty()
        out = (await agent_config_history(9, _user(), db))["data"]
        assert out == []
