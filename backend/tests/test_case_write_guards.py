"""§6.5：error case / error suite 人工写守卫（api 层策略单测）。

**这份测试能证明什么、不能证明什么**（先说清楚，免得被 N/N 全绿骗到）：

- **能证明**：六个写端点在 `case_type IS NOT NULL` / `is_error_suite=True` 时**抛 403 +
  E_NO_PERMISSION**，且**同一个端点在普通 case / 普通 suite 上仍正常返回**（反向对照）。
- **不能证明**：前端收到 403 后的行为；也不能证明「没有内部代码走这些写路径」——那只是静态
  grep 的结论。

**每条判据都配一条反向对照**：只断言「error case 抛 403」是不够的——把端点整体打死（比如无条件抛）
也能让那一条绿。同文件里的 `*_normal_*` 用例专门堵这个洞。
"""
from types import SimpleNamespace

import pytest

from app.api.annotations import AnnotationCreate, add_annotation
from app.api.cases import (
    CaseCreate,
    CaseUpdate,
    create_case,
    delete_suite,
    invalidate_case,
    update_case,
)
from app.api.scaffold import CaseSceneBody, add_case_scenes
from app.core.errors import E_NO_PERMISSION, ApiError

pytestmark = pytest.mark.asyncio  # 本模块全为协程用例，逐条标注不如模块级一次

USER = SimpleNamespace(id=7, username="staff", role="evaluator", password_changed_at=None)
AGENT = SimpleNamespace(id=1, owner_id=None, enabled=True)


def _case(case_type=None, is_held_out=False, suite_id=10):
    return SimpleNamespace(id=100, case_id=None, case_type=case_type, is_held_out=is_held_out,
                           suite_id=suite_id, status="active", annotation_status="draft")


def _suite(is_error_suite=False, agent_id=1):
    return SimpleNamespace(id=10, agent_id=agent_id, is_error_suite=is_error_suite, name="s")


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        # 聚合查询（count）返回单列表行，SQLAlchemy 的 scalar_one 会解包——替身必须同形，
        # 否则 `(0,)` 为真 ⇒ delete_suite 误判「suite 下仍有用例」而 409。
        return self._rows[0][0] if self._rows else None

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _DB:
    """按模型名分派的最小替身。`case` / `suite` 就是被测守卫要读的那两个对象。"""

    def __init__(self, case=None, suite=None, count=0):
        self._case = case
        self._suite = suite
        self._count = count
        self.committed = False
        self.added = []

    async def get(self, model, pk, with_for_update=False):
        return {"TestCase": self._case, "TestSuite": self._suite,
                "Agent": AGENT}.get(model.__name__)

    async def execute(self, stmt):
        if "count" in str(stmt).lower():
            return _Result([(self._count,)])
        return _Result([])

    def add(self, obj):
        self.added.append(obj)

    def add_all(self, objs):
        self.added.extend(objs)

    async def delete(self, obj):
        return None

    async def commit(self):
        self.committed = True


async def _expect_403(coro):
    with pytest.raises(ApiError) as ei:
        await coro
    assert ei.value.status_code == 403
    assert ei.value.code == E_NO_PERMISSION


# ---------------- 1-2. update_case / invalidate_case ----------------

async def test_update_case_on_error_case_403():
    db = _DB(case=_case(case_type="regression_error"), suite=_suite())
    await _expect_403(update_case(100, CaseUpdate(**{"name": "改"}), USER, db))


async def test_update_case_on_normal_case_ok():
    """反向对照：普通 case 不受影响（否则「403 通过」可能只是端点整体坏掉）。"""
    db = _DB(case=_case(), suite=_suite())
    assert (await update_case(100, CaseUpdate(**{"name": "改"}), USER, db))["code"] == 0


async def test_invalidate_case_on_error_case_403():
    db = _DB(case=_case(case_type="regression_error"), suite=_suite())
    await _expect_403(invalidate_case(100, USER, db))
    assert db._case.status == "active"  # 未落库


async def test_invalidate_case_on_normal_case_ok():
    db = _DB(case=_case(), suite=_suite())
    assert (await invalidate_case(100, USER, db))["code"] == 0
    assert db._case.status == "invalidated"


# ---------------- 3-4. add_annotation / add_case_scenes ----------------

async def test_add_annotation_on_error_case_403():
    db = _DB(case=_case(case_type="regression_error"), suite=_suite())
    body = AnnotationCreate(dimension_code="accuracy", level=3, note=None)
    await _expect_403(add_annotation(100, body, USER, db))


async def test_add_annotation_on_normal_case_ok():
    """普通 case 走到维度校验才停（400），证明**守卫是按 case_type 分流的、不是无条件抛 403**。"""
    db = _DB(case=_case(), suite=_suite())
    body = AnnotationCreate(dimension_code="accuracy", level=3, note=None)
    with pytest.raises(ApiError) as ei:
        await add_annotation(100, body, USER, db)
    assert ei.value.status_code == 400  # 维度未注册——不是 403


async def test_add_case_scenes_on_error_case_403():
    db = _DB(case=_case(case_type="regression_error"), suite=_suite())
    await _expect_403(add_case_scenes(100, CaseSceneBody(scene_tags=["x"]), USER, db))


async def test_add_case_scenes_on_normal_case_ok():
    db = _DB(case=_case(), suite=_suite())
    body = CaseSceneBody(scene_tags=[])
    assert (await add_case_scenes(100, body, USER, db))["code"] == 0


# ---------------- 5-6. create_case / delete_suite ----------------

async def test_create_case_on_error_suite_403():
    db = _DB(suite=_suite(is_error_suite=True))
    # input_type 无默认值（必填），与 is_gold/is_held_out 一起给全 —— B7 正是要证明
    # 这三个入参在 error suite 上被守卫整体吞掉，而不是靠逐字段判
    body = CaseCreate(name="新用例", interface_id=1, input_type="text",
                      is_gold=True, is_held_out=True)
    await _expect_403(create_case(10, body, USER, db))
    assert db.added == []  # 未建对象


async def test_create_case_on_normal_suite_not_403():
    """反向对照：普通 suite 上 403 不再出现（后续因接口不存在而 400，证明是分流而非打死）。"""
    db = _DB(suite=_suite())
    body = CaseCreate(name="新用例", interface_id=1, input_type="text")
    with pytest.raises(ApiError) as ei:
        await create_case(10, body, USER, db)
    assert ei.value.status_code == 400


async def test_delete_suite_on_error_suite_403():
    """即使子用例数为 0（本该删得掉），error suite 也必须先被 403 挡住。"""
    db = _DB(suite=_suite(is_error_suite=True), count=0)
    await _expect_403(delete_suite(10, USER, db))


async def test_delete_suite_on_normal_suite_ok():
    db = _DB(suite=_suite(), count=0)
    assert (await delete_suite(10, USER, db))["code"] == 0
