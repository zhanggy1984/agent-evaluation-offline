"""P2-E1 分页参数钳制单测（mock db，不进真库；function 级 loop）。

list_users 原仅钳 page_size，page_num 未钳——负值/0 产生负 offset，
MySQL `LIMIT -120, 20` 语法错误 500（见 users.py:42 P2-E1）。
钳制 page_num 下限 1：负值按第 1 页返回；超大 page_num 返回空页（无害）。
"""
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api import users as users_mod


def _mock_db(total, rows):
    db = AsyncMock()
    count_result = MagicMock()
    count_result.scalar_one.return_value = total
    rows_result = MagicMock(scalars=lambda: MagicMock(all=lambda: rows))
    db.execute.side_effect = [count_result, rows_result]
    return db


def _user(u_id, role="viewer"):
    return types.SimpleNamespace(id=u_id, username=f"u{u_id}", role=role, enabled=True)


@pytest.mark.asyncio(loop_scope="function")
async def test_negative_page_num_clamped_to_one():
    """page_num=-5 → 钳为 1，返回 data.page=1（不再生成负 offset）。"""
    db = _mock_db(3, [_user(3), _user(2), _user(1)])
    resp = await users_mod.list_users(page_num=-5, page_size=20, role=None, _=None, db=db)
    assert resp["data"]["page"] == 1, "负 page_num 应钳为第 1 页"
    assert resp["data"]["total"] == 3
    assert [u["id"] for u in resp["data"]["items"]] == [3, 2, 1]


@pytest.mark.asyncio(loop_scope="function")
async def test_zero_page_num_clamped_to_one():
    db = _mock_db(0, [])
    resp = await users_mod.list_users(page_num=0, page_size=20, role=None, _=None, db=db)
    assert resp["data"]["page"] == 1


@pytest.mark.asyncio(loop_scope="function")
async def test_normal_page_num_preserved():
    db = _mock_db(0, [])
    resp = await users_mod.list_users(page_num=2, page_size=20, role=None, _=None, db=db)
    assert resp["data"]["page"] == 2, "正常 page_num 不应被误钳"


@pytest.mark.asyncio(loop_scope="function")
async def test_page_size_clamped_upper():
    """page_size 超 100 → 钳为 100（原有逻辑回归护栏）。"""
    db = _mock_db(0, [])
    resp = await users_mod.list_users(page_num=1, page_size=9999, role=None, _=None, db=db)
    assert resp["data"]["page_size"] == 100
