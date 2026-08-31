"""case 加载统一入口（硬伤 #3 定向重跑）。

四处 case 加载（orchestrator 执行 / probe / _finish 对账 + scorer salvage）此前各自
复制同一过滤 SQL，过滤规则演进必然漏同步。本模块收敛为单一 _load_run_cases：
- 6.4b 留出集：held_out run 只跑 is_held_out 用例，manual run 排除留出集
- #3 定向重跑：run.case_ids 非空 → 只跑指定子集（叠加在留出集过滤之上）
- 只加载 status=active 用例（非 active 视为不参与本 run）

orchestrator 与 scorer 均可 import 本模块（scorer 不得反向 import orchestrator——
循环依赖；本模块只依赖 models，无环）。

TOCTOU：case 在 run 创建后被置非 active → 此处静默剔除，实际执行数 < 请求数
（case_ids 列保留原始请求，对账不再补它），为可接受降级。
"""
from sqlalchemy import select

from app.models import TestCase


async def _load_run_cases(db, run) -> list[TestCase]:
    """按 run 的 suite/trigger_type/case_ids 加载应执行的全部 active 用例。

    过滤规则单一来源：6.4b 留出集标志 + #3 子集（case_ids 非空 → id.in_）。
    无 ORDER BY（保持原四处查询的默认主键序，行为不变）。
    """
    stmt = select(TestCase).where(
        TestCase.suite_id == run.suite_id,
        TestCase.status == "active",
        TestCase.is_held_out == (run.trigger_type == "held_out"),
    )
    if run.case_ids:
        stmt = stmt.where(TestCase.id.in_(run.case_ids))
    return (await db.execute(stmt)).scalars().all()
