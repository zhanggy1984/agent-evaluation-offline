"""6.4 数据清理：按 (agent_id, suite_id) 保留 N 次，分批删最老 run。

- 排除：pinned（关键版本，不删）
- 级联：eval_result / judge_task / export_token 均 ON DELETE CASCADE，删 run 自动连带
- 分批：每批 20 条，避免长事务锁表（run + 级联结果）
- 幂等：仅候选已存在才删，重复调用安全；无候选 → purged=0
- agent 侧清库：平台侧只删自己的 run 数据，agent 自身测试数据留钩子（决策：平台侧 + 留钩子）

纯函数（candidates_to_purge）宿主单测直接跑；run_cleanup 做 DB 编排。
审计由调用方写（API 用 write_audit 带 request/user；scanner 用 user_id=0 系统记录）。
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# 仅终态 run 可清理（pending/running/scoring 在执行/待评分，绝不删）
_TERMINAL = ("completed", "partial_failed", "scoring_failed", "timeout", "cancelled")
DEFAULT_RETAIN = 50    # 与 seed system_config retain_runs 缺省值一致
DEFAULT_BATCH = 20     # 分批删除大小（run + 级联结果，控制单事务量）


def candidates_to_purge(runs, retain: int = DEFAULT_RETAIN) -> list[int]:
    """可清理 run id：按 (agent_id, suite_id) 分组，每组保留最近 retain 个，其余候选。

    保留语义叠加版本维度：每个版本最近 1 条（版本代表）优先保护，剩余按时间填充 retain，
    防止高频迭代把早期但重要的版本代表全部顶掉（版本数 ≤ retain 时每版本至少留 1 条）。
    排除：pinned（关键版本不删）。
    返回按 id 升序（旧先删，分批时优先清最老数据）。
    """
    groups: dict[tuple[int, int], list] = {}
    for r in runs:
        if r.pinned:
            continue
        groups.setdefault((r.agent_id, r.suite_id), []).append(r)
    out = []
    for grp in groups.values():
        by_version: dict[str, list] = {}
        for r in grp:
            by_version.setdefault(r.version, []).append(r)
        protected, rest = [], []
        for vs in by_version.values():
            vs.sort(key=lambda r: (r.started_at or datetime.min, r.id), reverse=True)
            protected.append(vs[0])     # 每版本最近 1 条 = 版本代表
            rest.extend(vs[1:])
        protected.sort(key=lambda r: (r.started_at or datetime.min, r.id), reverse=True)
        rest.sort(key=lambda r: (r.started_at or datetime.min, r.id), reverse=True)
        keep = protected[:retain]
        if len(protected) < retain:
            keep += rest[:retain - len(protected)]
        keep_ids = {r.id for r in keep}
        out.extend(r.id for r in grp if r.id not in keep_ids)
    return sorted(out)


async def _retain_runs(db: AsyncSession) -> int:
    """保留次数：system_config retain_runs（global），缺省 50。"""
    from app.models import SystemConfig
    row = await db.get(SystemConfig, "retain_runs")
    return int((row.value if row else None) or DEFAULT_RETAIN)


async def _agent_side_cleanup_hook(db: AsyncSession, run_ids: list[int]) -> None:
    """agent 侧数据清库钩子（预留）。

    平台侧清库只删自己的 run/result/token（DB CASCADE）；agent 自身测试数据
    （如 sp 评审会话堆积）由 agent 侧清理任务负责。此处留调用点，agent 适配后实现；
    当前为空实现（决策：平台侧清理 + 留钩子）。
    """
    if run_ids:
        logger.debug("agent 侧清库钩子：候选 %d 个 run（平台已清，agent 侧待接入）", len(run_ids))


async def run_cleanup(db: AsyncSession, *, retain: int | None = None,
                      batch: int = DEFAULT_BATCH) -> dict:
    """执行一轮清理：分批删候选 run（CASCADE 连带 result/judge_task/export_token）。

    返回 {purged, skipped_pinned, retain}；无候选 → purged=0（幂等）。
    """
    from app.models import EvalRun

    if retain is None:
        retain = await _retain_runs(db)
    rows = (await db.execute(select(EvalRun).where(
        EvalRun.status.in_(_TERMINAL)))).scalars().all()
    skipped_pinned = sum(1 for r in rows if r.pinned)
    candidates = candidates_to_purge(rows, retain)
    purged = 0
    for i in range(0, len(candidates), batch):
        ids = candidates[i:i + batch]
        await db.execute(delete(EvalRun).where(EvalRun.id.in_(ids)))
        await db.commit()
        purged += len(ids)
        logger.info("数据清理批次 %d-%d，累计 %d", i, i + len(ids), purged)
    await _agent_side_cleanup_hook(db, candidates)
    if purged:
        logger.info("数据清理完成：purged=%d skipped_pinned=%d retain=%d",
                    purged, skipped_pinned, retain)
    return {"purged": purged, "skipped_pinned": skipped_pinned, "retain": retain}
