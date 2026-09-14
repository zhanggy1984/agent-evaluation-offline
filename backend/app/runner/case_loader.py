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

_ERROR_ASSERTION_OP = "keyword_not_contains"  # §8.1 error case 唯一允许的断言算子


def _is_error_case(case) -> bool:
    """§8.1 形态判定：断言恰为 keyword_not_contains 列表。

    SQL 侧只能判 `assertions IS NOT NULL`；算子形态须回 Python 判——JSON 列在 MySQL
    侧没有可移植的形态谓词。形态不符的 case 不加载（而非加载后判 fail）：它不是本 run
    要判的对象，混进来会被 online 读成一条真实 fail。
    """
    defs = case.assertions
    return bool(defs) and all(
        isinstance(d, dict) and d.get("op") == _ERROR_ASSERTION_OP for d in defs
    )


async def _load_run_cases(db, run) -> list[TestCase]:
    """按 run 的 suite/trigger_type/case_ids 加载应执行的全部 active 用例。

    过滤规则单一来源：6.4b 留出集标志 + #3 子集（case_ids 非空 → id.in_）
    + error_regression 专用分支（§8.1）。**两条分支以 case_type 互斥**：error 分支只取
    `case_type IS NOT NULL`、普通分支只取 `case_type IS NULL`，否则 suite 内两类 case 互窜。
    无 ORDER BY（保持原四处查询的默认主键序，行为不变）。
    """
    stmt = select(TestCase).where(
        TestCase.suite_id == run.suite_id,
        TestCase.status == "active",
    )
    if run.trigger_type == "error_regression":
        # §8.1：error case 恒非留出集；断言非空（形态回 Python 判，见 `_is_error_case`）。
        # 谓词是「case_type 非空」而非某个具体值——值域由 online case_type 白名单定。
        stmt = stmt.where(TestCase.case_type.is_not(None), TestCase.is_held_out.is_(False))
    else:
        stmt = stmt.where(
            TestCase.is_held_out == (run.trigger_type == "held_out"),
            TestCase.case_type.is_(None),
        )
    if run.case_ids:
        stmt = stmt.where(TestCase.id.in_(run.case_ids))
    cases = (await db.execute(stmt)).scalars().all()
    if run.trigger_type == "error_regression":
        cases = [c for c in cases if _is_error_case(c)]
    return cases
