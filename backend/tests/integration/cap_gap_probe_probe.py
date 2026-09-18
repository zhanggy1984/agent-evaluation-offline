"""R-8 探测态真库验收：**只验「仍缺 → 静默」那支**（§5.6 谓词 / §5.8 节流）。

跑法（宿主直连共享库，env 从容器整体导出后覆写，见 memory `offline-real-db-probe-recipe`）：
    .venv\\Scripts\\python.exe tests\\integration\\cap_gap_probe_probe.py

**为什么只验一支（显式，勿读成「全支已验」）**：自愈那支（补齐 → 建 case + 单次 active ack）
要在真机走通，必须先**造一个能过 R-27 装载闸的 agent/interface**，而自愈会建出 active 的
error case ⇒ 该 agent **首次进入 `_error_agents` 扫描集** ⇒ `reconcile_loop` 随即可能连续补建
error run（其 base_url 不通则那批 run 全红，与真缺陷逐字同形）。这与「不往共享观测面注入假
故障」冲突，用户裁定：**自愈支不真机验**，留单测（`tests/test_cap_gap_probe.py`）覆盖。
⇒ 本文件**不写、不建、不改任何行**，零副作用。

**真的部分**：真库、真 `probe_once()` 全链（真谓词扫描 → 真 `_self_check`：登记解析 / 词表
净化 / R-27 装载闸）、真行内容、真静默分支、连跑两轮的真实残留。
**本探针证不了什么**（显式声明，勿外推）：
- **自愈支（`activated` 路径）**：本探针断言 `activated == 0`，恰恰**不含**建 case 与
  `_ack_active` 出站 —— 那两条路径无真机证据；
- **online 侧 R2 例外（`invalidated→active`）**：该变更只在自愈时发生，故本探针同样证不到，
  这条契约**至今未被真机走过**；
- **`_INTERVAL` 真的每小时**：不真等 3600s，只验单轮语义。
"""
import asyncio
import logging
import sys
from unittest import mock

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import Settings
from app.models.error_backflow_inbox import ErrorBackflowInbox
from app.runner import cap_gap_probe as cgp

PASS_COUNT = 0
FAIL_COUNT = 0


def _check(name: str, got, want) -> None:
    global PASS_COUNT, FAIL_COUNT
    if got == want:
        PASS_COUNT += 1
        print(f"  PASS  {name}")
    else:
        FAIL_COUNT += 1
        print(f"  FAIL  {name}: got={got!r} want={want!r}")


def _check_true(name: str, got) -> None:
    _check(name, bool(got), True)


async def _snapshot(engine, payload_ids: list[str]) -> dict:
    """取这些行的判别字段快照（静默 = 快照逐项不变）。"""
    if not payload_ids:
        return {}
    async with AsyncSession(engine) as s:
        rows = (await s.execute(select(
            ErrorBackflowInbox.payload_id, ErrorBackflowInbox.status,
            ErrorBackflowInbox.reject_code, ErrorBackflowInbox.ack_status,
            ErrorBackflowInbox.case_id,
        ).where(ErrorBackflowInbox.payload_id.in_(payload_ids)))).all()
    return {r[0]: (r[1], r[2], r[3], r[4]) for r in rows}


async def _expected_ids(engine, status: str, code: str, ack: str) -> set:
    """按谓词三要素直查库算期望命中集（与 probe_once 同一谓词，独立写法）。"""
    async with AsyncSession(engine) as s:
        rows = (await s.execute(select(ErrorBackflowInbox.payload_id).where(
            ErrorBackflowInbox.status == status,
            ErrorBackflowInbox.reject_code == code,
            ErrorBackflowInbox.ack_status == ack,
        ))).scalars().all()
    return set(rows)


async def _run_once_with_recorder() -> tuple[dict, set]:
    """跑一轮 `probe_once`，返回 (stat, 真正被交给 `_self_check` 的 payload_id 集合)。

    包一层 `_self_check` 而非替换它：转发**真实现**（`pl._self_check`），只顺手记录入参。
    """
    seen: list[str] = []
    real = cgp._self_check

    async def _rec(db, envelope):
        seen.append(str((envelope or {}).get("payload_id")))
        return await real(db, envelope)

    with mock.patch.object(cgp, "_self_check", _rec):
        stat = await cgp.probe_once()
    return stat, set(seen)


async def scenario_1(engine) -> None:
    """谓词命中真行 + 仍缺即静默 + 零状态变更。"""
    print("\n[场景 1] 谓词命中真行、逐行静默、行快照不变")
    status, code, ack = cgp.CAP_GAP_PROBE
    expected = await _expected_ids(engine, status, code, ack)
    print(f"  库内谓词命中 payload_id = {sorted(expected)}")

    before = await _snapshot(engine, sorted(expected))
    stat, seen = await _run_once_with_recorder()
    after = await _snapshot(engine, sorted(expected))

    # 判据「等于谁」：空集也能让下面三条恒绿，故先钉死命中集非空且有判别力
    _check_true("1 · 谓词在真库命中 ≥1 行（否则本场景无判别力）", len(expected) >= 1)
    _check("1 · probe_once 交给自检的正是谓词命中集", seen, expected)
    _check("1 · 扫描行数", stat["scanned"], len(expected))
    _check("1 · 仍缺行数", stat["still_missing"], len(expected))
    _check("1 · 自愈行数（本探针只许 0：自愈支未真机验）", stat["activated"], 0)
    _check("1 · 行快照逐项不变（status/reject_code/ack_status/case_id）", after, before)


async def scenario_2(engine) -> None:
    """连跑两轮：第二轮不得因上一轮残留改变结论（真库探针可重复性铁律）。"""
    print("\n[场景 2] 连跑两轮结论一致、残留为零")
    status, code, ack = cgp.CAP_GAP_PROBE
    expected = await _expected_ids(engine, status, code, ack)
    before = await _snapshot(engine, sorted(expected))

    stat1, seen1 = await _run_once_with_recorder()
    stat2, seen2 = await _run_once_with_recorder()
    after = await _snapshot(engine, sorted(expected))

    _check("2 · 两轮扫描集一致", seen2, seen1)
    _check("2 · 两轮 stat 一致", stat2, stat1)
    _check("2 · 两轮后行快照仍不变", after, before)


async def scenario_3(engine) -> None:
    """谓词不外溢：`online_content_gap` 的已 acked 行归 R-28 那条路，本模块不得抢。"""
    print("\n[场景 3] 不抢 online_content_gap 的行")
    stray = await _expected_ids(engine, "rejected", "online_content_gap", "acked")
    _, seen = await _run_once_with_recorder()
    if not stray:
        print("  SKIP  库内无 (rejected ∧ online_content_gap ∧ acked) 对照行 ⇒ 本条本轮无判别力")
        return
    _check("3 · 与 R-28 那条路的行集交集为空", seen & stray, set())


async def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    engine = create_async_engine(Settings().sqlalchemy_url)   # 不打印（含口令）
    try:
        await scenario_1(engine)
        await scenario_2(engine)
        await scenario_3(engine)
    finally:
        await engine.dispose()
    print(f"\n===== {PASS_COUNT} passed / {FAIL_COUNT} failed =====")
    print("注：本探针只覆盖「仍缺 → 静默」一支；自愈支（建 case + ack active + online R2 例外）"
          "无真机证据，只由单测覆盖。")
    sys.exit(1 if FAIL_COUNT else 0)


if __name__ == "__main__":
    asyncio.run(main())
