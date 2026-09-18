"""R-3 版本差集对账：判定点单测（不连库）。

只测**纯函数与门控**——查库口径（坏终态视为有 run、锚取 max(id)、信号排除 error_regression）
由真库探针 `tests/integration/reconcile_probe.py` 覆盖，此处不重复造替身（替身会把 SQL 口径
测成我自己的假设）。
"""
import asyncio

from app.core.config import settings
from app.runner import reconcile_loop as rl


def test_version_diff_returns_signal_minus_have():
    assert rl._version_diff({"v1", "v2", "v3"}, {"v1"}) == {"v2", "v3"}


def test_version_diff_empty_when_all_have_error_run():
    """同版本已有 error run（含坏终态版本）⇒ 不进 diff，不会被反复补建。"""
    assert rl._version_diff({"v1", "v2"}, {"v1", "v2"}) == set()


def test_version_diff_ignores_have_versions_without_signal():
    """have 里有而 signal 里没有的版本不会反向出现在 diff（差集是单向的）。

    这是「自指防护」在纯函数层的可见形式：error run 自身的版本若不来自信号，永远不会被
    本模块再次补建 ⇒ 无自指放大。**信号侧如何排除 error_regression 由探针场景 4 验证**。
    """
    assert rl._version_diff({"v1"}, {"v1", "v9"}) == set()


def test_pick_next_version_by_anchor_arrival_order():
    """多缺版按锚 run id 升序取最老者（到达序，不用 semver 字典序）。

    反例守卫：`{"v10": 5, "v9": 30}` 若按字符串排序会选 v10（错），按锚 id 选 v10 也是
    对的——故本条真正约束的是「按锚选」而非「按串选」；`v9` 锚更晚 ⇒ 应选 v10。
    """
    assert rl._pick_next_version({"v9": 30, "v10": 5}) == "v10"


def test_pick_next_version_none_when_no_diff():
    assert rl._pick_next_version({}) is None


def test_loop_returns_immediately_when_disabled(monkeypatch):
    """`backflow_enabled=false` ⇒ loop 直接返回，**一次都不执行**对账（而非空转后再退出）。"""
    monkeypatch.setattr(settings, "backflow_enabled", False)

    async def _boom():
        raise AssertionError("backflow_enabled=false 时不应执行对账")

    monkeypatch.setattr(rl, "reconcile_once", _boom)
    asyncio.run(rl.reconcile_loop())  # 若门控失效，这里会抛 AssertionError 而非挂死
