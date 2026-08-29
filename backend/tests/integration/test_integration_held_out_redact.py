"""P2-D8 held_out run 聚合结果对 owner 裁剪（runs.py list/get + dashboard 排除）。

漏洞：list_runs/get_run 未对 owner 裁剪 held_out run 的聚合结果；dashboard gate/trend/
compare/perf/cost/baseline 混入 held_out run——owner 经看板同样可读留出集分数，且污染
「最新版本门禁分」等常规评测指标。

修复后：
- owner 调 list_runs/get_run：held_out run 结果型字段（agent_score/pass_case/ttft/e2e 等）
  全 None，run 记录保留（占用 agent 执行槽位，owner 需知情）；manual run 不受影响
- admin（非该 agent owner）：held_out run 聚合结果正常返回
- dashboard 各端点：查询层排除 held_out run（看板为常规评测视图，留出集结果走 /runs 详情）
"""
import uuid
from datetime import datetime, timedelta

import pytest

pytest.importorskip("aiomysql")

from pytest_asyncio import fixture as async_fixture

from sqlalchemy import delete

from app.api import dashboard as dash_mod
from app.api import runs as runs_mod
from app.core.db import SessionLocal
from app.core.errors import ApiError
from app.models.user import User
from helpers import create_chain, make_run

# 结果型字段：owner 对 held_out run 全部裁剪；状态型元数据（id/version/status/时间/total_case）保留
_RESULT_FIELDS = ("agent_score", "pass_case", "fail_case", "error_case",
                  "na_case", "judge_incomplete", "ttft_p50", "e2e_p50")


async def _mk_user(db, role: str) -> User:
    u = User(username=f"it-d8-{role}-{uuid.uuid4().hex[:8]}",
             password_hash="x", role=role, enabled=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@async_fixture(loop_scope="session")
async def d8_users(db):
    """owner（该 agent 负责人）+ admin；teardown 独立 session 删 user（agent.owner_id 无 FK）。"""
    owner = await _mk_user(db, "evaluator")
    admin = await _mk_user(db, "admin")
    yield owner, admin
    async with SessionLocal() as s:
        await s.execute(delete(User).where(User.id.in_([owner.id, admin.id])))
        await s.commit()


async def _seed(db, env, owner):
    """agent(owner) + 两个 completed run：manual 先插（id 小）、held_out 后插（id 大=最新）。

    baseline 取最新一条：无过滤会选中 held_out（污染），过滤后回落 manual——有区分度。
    """
    ch = await create_chain(db)
    ch["agent"].owner_id = owner.id
    now = datetime.utcnow()
    manual = make_run(ch["agent"].id, ch["suite"].id)
    manual.status = "completed"
    manual.agent_score = 90.0
    manual.pass_case = 9; manual.fail_case = 1; manual.total_case = 10
    manual.ttft_p50 = 0.3; manual.e2e_p50 = 1.5
    manual.started_at = now - timedelta(minutes=10); manual.finished_at = now - timedelta(minutes=5)
    db.add(manual)
    await db.flush()
    held = make_run(ch["agent"].id, ch["suite"].id)
    held.trigger_type = "held_out"
    held.status = "completed"
    held.agent_score = 80.0
    held.pass_case = 8; held.fail_case = 2; held.total_case = 10
    held.ttft_p50 = 0.5; held.e2e_p50 = 2.0
    held.started_at = now - timedelta(minutes=3); held.finished_at = now - timedelta(minutes=1)
    db.add(held)
    await db.flush()
    env.agents.append(ch["agent"]); env.interfaces.append(ch["iface"])
    env.suites.append(ch["suite"]); env.cases.extend(ch["cases"])
    env.runs.extend([manual, held])
    await db.commit()
    return ch, manual, held


@pytest.mark.asyncio(loop_scope="session")
async def test_list_runs_owner_redacts_held_out(db, env, d8_users):
    """owner 列表：held_out run 结果型字段全 None，记录保留；manual run 不受影响。"""
    owner, _ = d8_users
    _, manual, held = await _seed(db, env, owner)
    resp = await runs_mod.list_runs(user=owner, db=db)
    by_id = {r["id"]: r for r in resp["data"]}
    h = by_id[held.id]
    assert h["trigger_type"] == "held_out"
    for f in _RESULT_FIELDS:
        assert h[f] is None, f"{f} 应被裁剪"
    assert h["status"] == "completed" and h["total_case"] == 10  # 状态型元数据保留
    assert by_id[manual.id]["agent_score"] == 90.0  # 普通 run 不受影响


@pytest.mark.asyncio(loop_scope="session")
async def test_list_runs_admin_sees_held_out(db, env, d8_users):
    """admin（非该 agent owner）：held_out run 聚合结果正常返回。"""
    owner, admin = d8_users
    _, manual, held = await _seed(db, env, owner)
    resp = await runs_mod.list_runs(user=admin, db=db)
    by_id = {r["id"]: r for r in resp["data"]}
    assert by_id[held.id]["agent_score"] == 80.0
    assert by_id[held.id]["pass_case"] == 8
    assert by_id[manual.id]["agent_score"] == 90.0


@pytest.mark.asyncio(loop_scope="session")
async def test_get_run_owner_redacts_held_out(db, env, d8_users):
    """owner 详情：held_out run 结果型字段全 None；manual run 详情不受影响。"""
    owner, _ = d8_users
    _, manual, held = await _seed(db, env, owner)
    h = (await runs_mod.get_run(held.id, user=owner, db=db))["data"]
    for f in _RESULT_FIELDS:
        assert h[f] is None, f"{f} 应被裁剪"
    assert h["status"] == "completed"
    assert (await runs_mod.get_run(manual.id, user=owner, db=db))["data"]["agent_score"] == 90.0


@pytest.mark.asyncio(loop_scope="session")
async def test_get_run_admin_sees_held_out(db, env, d8_users):
    """admin（非该 agent owner）详情：held_out run 聚合结果正常。"""
    owner, admin = d8_users
    _, _, held = await _seed(db, env, owner)
    h = (await runs_mod.get_run(held.id, user=admin, db=db))["data"]
    assert h["agent_score"] == 80.0 and h["pass_case"] == 8


@pytest.mark.asyncio(loop_scope="session")
async def test_dashboard_gate_trend_perf_exclude_held_out(db, env, d8_users):
    """看板 gate/trend/perf 排除 held_out run——owner 与 admin 一致（看板为常规评测视图）。"""
    owner, admin = d8_users
    ch, manual, held = await _seed(db, env, owner)
    for user in (owner, admin):
        t = (await dash_mod.trend(ch["agent"].id, user=user, db=db))["data"]
        run_ids = {r["run_id"] for r in t}
        assert held.id not in run_ids and manual.id in run_ids
        p = (await dash_mod.perf(ch["agent"].id, user=user, db=db))["data"]
        assert held.id not in {r["run_id"] for r in p}
        c = (await dash_mod.cost(ch["agent"].id, user=user, db=db))["data"]
        assert held.id not in {r["run_id"] for r in c}  # 成本面板同样排除留出集
        g = (await dash_mod.gate(user=user, db=db))["data"]
        card = next(c for c in g if c["agent_id"] == ch["agent"].id)
        assert card["agent_score"] == 90.0  # 只来自 manual，held_out 未混入


@pytest.mark.asyncio(loop_scope="session")
async def test_dashboard_baseline_and_compare_exclude_held_out(db, env, d8_users):
    """baseline 最新是 held_out 时回落 manual；compare 对 held_out run 404。"""
    owner, admin = d8_users
    ch, manual, held = await _seed(db, env, owner)
    b = (await dash_mod.baseline(ch["agent"].id, user=admin, db=db))["data"]
    assert b["run"]["run_id"] == manual.id  # held_out 不被当基线
    with pytest.raises(ApiError) as ei:
        await dash_mod.compare(held.id, manual.id, user=admin, db=db)
    assert ei.value.status_code == 404  # 看板不泄露留出集存在性
