"""P2-D9 CaseUpdate 补 interface_id/scenes 字段（cases.py 编辑用例静默丢弃修复）。

前端编辑用例始终提交 interface_id + scenes（Cases.vue saveCase payload），但原
CaseUpdate 缺这两字段 → Pydantic 忽略额外字段静默丢弃：用户改接口/场景保存不生效。

修复后：
- interface_id：校验新接口属于该 agent（对齐 create_case）后更新 case.interface_id；
  外部接口 → 400 不改动；显式 null（异常态）→ 保留原值
- scenes：校验标签在该 agent scene_catalog 后删旧插新（case_scene 关联表）；
  scenes=null（前端清空）→ 删旧不插新；字段缺失（exclude_unset）→ 完全不动
"""
import pytest

pytest.importorskip("aiomysql")

from sqlalchemy import select

from app.api import cases as cases_mod
from app.core.db import SessionLocal
from app.core.errors import ApiError, E_VALIDATION
from app.models import CaseScene, SceneCatalog
from app.models.user import User
from helpers import create_chain, make_interface


def _admin() -> User:
    """admin：豁免 golden 字段级权限；非 owner（agent.owner_id 默认 None）。"""
    return User(username="it-d9-admin", password_hash="x", role="admin", enabled=True)


async def _seed(db, env):
    """agent + 2 接口 + suite + case + catalog 场景标签（it-d9-scene）。"""
    ch = await create_chain(db)
    iface2 = make_interface(ch["agent"].id, "it-d9-iface2")
    db.add(iface2)
    await db.flush()
    db.add(SceneCatalog(agent_id=ch["agent"].id, scene_tag="it-d9-scene", description="D9 测试场景"))
    await db.flush()
    env.agents.append(ch["agent"]); env.interfaces.extend([ch["iface"], iface2])
    env.suites.append(ch["suite"]); env.cases.extend(ch["cases"])
    await db.commit()
    return ch, iface2


async def _load_scene_tags(case_id):
    async with SessionLocal() as s:
        rows = (await s.execute(select(CaseScene).where(CaseScene.case_id == case_id))).scalars().all()
    return [r.scene_tag for r in rows]


@pytest.mark.asyncio(loop_scope="session")
async def test_update_case_interface_id(db, env):
    """换接口生效：interface_id 校验归属后更新 case.interface_id。"""
    ch, iface2 = await _seed(db, env)
    case = ch["cases"][0]
    assert case.interface_id == ch["iface"].id
    await cases_mod.update_case(case.id, cases_mod.CaseUpdate(**{"interface_id": iface2.id}),
                                user=_admin(), db=db)
    await db.refresh(case)
    assert case.interface_id == iface2.id


@pytest.mark.asyncio(loop_scope="session")
async def test_update_case_rejects_foreign_interface(db, env):
    """接口不属于该 agent → 400 且不改动（对齐 create_case 校验）。"""
    ch, _ = await _seed(db, env)
    other = await create_chain(db)  # 另一 agent 的接口
    db.add(other["agent"]); await db.flush()
    env.agents.append(other["agent"]); env.interfaces.append(other["iface"])
    env.suites.append(other["suite"]); env.cases.extend(other["cases"])
    await db.commit()
    case = ch["cases"][0]
    with pytest.raises(ApiError) as ei:
        await cases_mod.update_case(case.id, cases_mod.CaseUpdate(**{"interface_id": other["iface"].id}),
                                    user=_admin(), db=db)
    assert ei.value.status_code == 400 and ei.value.code == E_VALIDATION
    await db.refresh(case)
    assert case.interface_id == ch["iface"].id


@pytest.mark.asyncio(loop_scope="session")
async def test_update_case_add_scenes(db, env):
    """加场景生效：scenes 写入 case_scene 关联表（校验 tag 在 scene_catalog）。"""
    ch, _ = await _seed(db, env)
    case = ch["cases"][0]
    await cases_mod.update_case(case.id, cases_mod.CaseUpdate(**{"scenes": ["it-d9-scene"]}),
                                user=_admin(), db=db)
    assert await _load_scene_tags(case.id) == ["it-d9-scene"]


@pytest.mark.asyncio(loop_scope="session")
async def test_update_case_clear_scenes(db, env):
    """清空场景（scenes=null）→ 删旧不插新，与「字段缺失不动」区分。"""
    ch, _ = await _seed(db, env)
    case = ch["cases"][0]
    await cases_mod.update_case(case.id, cases_mod.CaseUpdate(**{"scenes": ["it-d9-scene"]}),
                                user=_admin(), db=db)
    assert await _load_scene_tags(case.id) == ["it-d9-scene"]
    await cases_mod.update_case(case.id, cases_mod.CaseUpdate(**{"scenes": None}),
                                user=_admin(), db=db)
    assert await _load_scene_tags(case.id) == []
