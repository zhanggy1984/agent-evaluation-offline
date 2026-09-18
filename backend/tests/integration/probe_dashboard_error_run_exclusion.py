"""P2-1 真库探针：`/gate` 与 `/trend` 排除 `error_regression` run（**只读**）。

**为什么是探针而不是单测**：本批改的是 **SQL 谓词**（`api/dashboard.py` 的 where 子句），
`build_gate_cards` 纯函数层**未动** ⇒ 宿主单测覆盖不到被改的那一层
（`dashboard_rules.py` 的 docstring 明写「零 DB 依赖，宿主单测直接跑」，
单测直接传 runs 列表、绕过 SQL）。

**判据写法**：断言卡片 `total_case` **等于**「同 version 下排除 error 的 sum」——
**不是**「变小了」。并**内置判别力对照**：同一 version 含 error 的 sum **必须与之不同**，
否则本探针对该 agent 无判别力（`agent_score` 恒 NULL 不构成对照，分母才是被改的量）。

**重复性**：只读、无写入、无副作用 ⇒ 连跑两遍结果必须一致（幂等）。

用法（容器内，`backend` 已 bind mount 到 `/app`）：
    docker exec ai-eval-backend python tests/integration/probe_dashboard_error_run_exclusion.py
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import func, select

from app.api import dashboard as dash
from app.core.db import SessionLocal


class _User:
    """仅用于 `logger.debug(..., user.username)`；不参与任何判据。"""

    username = "probe"


TERMINAL = tuple(dash.TERMINAL_STATUS)


def _unwrap(resp):
    """解包 `ok(...)` 的返回，拿到 list。"""
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        for key in ("data", "items", "result", "rows"):
            if key in resp:
                return _unwrap(resp[key])
        raise SystemExit(f"无法解包：dict keys={list(resp)}")
    raise SystemExit(f"无法解包：type={type(resp)}")


async def _sum_case(db, *, agent_id: int, version: str, exclude_error: bool) -> int:
    """按 `/gate` 的 SQL 口径复算该 (agent, version) 的 total_case 之和。

    `exclude_error=True` = **改后**语义；`False` = 改前语义（对照）。
    """
    conds = [
        dash.EvalRun.agent_id == agent_id,
        dash.EvalRun.version == version,
        dash.EvalRun.trigger_type != "held_out",
        dash.EvalRun.status.in_(TERMINAL),
    ]
    if exclude_error:
        conds.append(dash.EvalRun.trigger_type != "error_regression")
    return int((await db.execute(
        select(func.coalesce(func.sum(dash.EvalRun.total_case), 0)).where(*conds)
    )).scalar_one())


async def main() -> int:
    failures: list[str] = []
    exercised = 0          # gate：判别力对照真正生效的 agent 数（改前 ≠ 改后）
    trend_exercised = 0    # trend：同上
    checked = 0            # 有 version 的卡片数

    async with SessionLocal() as db:
        cards = _unwrap(await dash.gate(_User(), db))
        print(f"[gate] 卡片 {len(cards)} 张")

        for card in cards:
            version = card.get("version")
            if not version:
                continue
            checked += 1
            agent_id = card["agent_id"]
            want = await _sum_case(db, agent_id=agent_id, version=version, exclude_error=True)
            old = await _sum_case(db, agent_id=agent_id, version=version, exclude_error=False)
            got = card["total_case"]

            if old != want:
                exercised += 1
            # 判据一：写成「等于谁」
            if got != want:
                failures.append(
                    f"gate agent={agent_id} version={version!r}：total_case 实测 {got} "
                    f"≠ 期望（排除 error 后）{want}；含 error 的旧口径为 {old}"
                )
            # 判据二：分母同步（pass_rate 由二者推出）
            if old != want and got == old:
                failures.append(
                    f"gate agent={agent_id} version={version!r}：total_case 仍是旧口径值 {old}"
                    f" ⇒ 谓词未生效"
                )

        # ---- trend：判据 = error run 的 run_id 不得出现在返回集里 ----
        err_ids = set((await db.execute(
            select(dash.EvalRun.id).where(
                dash.EvalRun.trigger_type == "error_regression",
                dash.EvalRun.status.in_(TERMINAL),
            )
        )).scalars().all())

        agents_with_err = sorted({r for r in (await db.execute(
            select(dash.EvalRun.agent_id).where(
                dash.EvalRun.trigger_type == "error_regression",
                dash.EvalRun.status.in_(TERMINAL),
            ).distinct()
        )).scalars().all()})
        print(f"[trend] 待查 agent {len(agents_with_err)} 个；error run {len(err_ids)} 条")

        for agent_id in agents_with_err:
            rows = _unwrap(await dash.trend(agent_id, _User(), db))
            got_ids = {r["run_id"] for r in rows}
            # 改前口径（仅排 held_out）的集合 —— 期望值写成「它减去 error 集」
            old_ids = set((await db.execute(
                select(dash.EvalRun.id).where(
                    dash.EvalRun.agent_id == agent_id,
                    dash.EvalRun.trigger_type != "held_out",
                    dash.EvalRun.status.in_(TERMINAL),
                )
            )).scalars().all())
            want_ids = old_ids - err_ids
            if old_ids != want_ids:
                trend_exercised += 1
            if got_ids != want_ids:
                failures.append(
                    f"trend agent={agent_id}：返回集与期望不符——多出 "
                    f"{sorted(got_ids - want_ids)[:5]}，少了 {sorted(want_ids - got_ids)[:5]}"
                    f"（改前口径 {len(old_ids)} 条 / error {len(old_ids & err_ids)} 条）"
                )

    print("\n=== 汇总 ===")
    print(f"gate 有 version 的卡片：{checked}；其中判别力对照生效（改前≠改后）：{exercised}")
    print(f"trend 待查 agent：{len(agents_with_err)}；其中判别力对照生效：{trend_exercised}")
    if not exercised and not trend_exercised:
        print("⚠️ 判别力对照未生效：本探针在本次数据上**无法区分**改前/改后")
    if failures:
        print(f"\n❌ FAIL {len(failures)} 条：")
        for f in failures:
            print("  -", f)
        return 1
    print("\n✅ PASS：gate 分母 = 排除 error 的口径；trend 不含 error run")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
