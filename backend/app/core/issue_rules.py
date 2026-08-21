"""问题状态机 + 复现验证判定纯逻辑（零 DB 依赖，宿主单测直接跑）。

与 dashboard_rules 同模式：test_issues 只 import 本模块，不触 app.core.db
（宿主无 aiomysql 时 create_async_engine 会加载驱动报错）。
"""
ISSUE_SEVERITY = ("low", "medium", "high", "critical")
ISSUE_STATUS = ("open", "fixing", "fixed", "verified", "closed")
ISSUE_VERIFY_RESULT = ("reproduced", "fixed", "verified")
# 参与复现验证的未关闭状态（closed 终态不验）
ISSUE_ACTIVE_STATUS = ("open", "fixing", "fixed", "verified")

# 合法转移表：线性 open→fixing→fixed→verified→closed；另 open→closed（登记作废）
ALLOWED_TRANSITIONS = {
    "open": {"fixing", "closed"},
    "fixing": {"fixed"},
    "fixed": {"verified"},
    "verified": {"closed"},
    "closed": set(),
}


def can_transition(from_status: str, to_status: str) -> bool:
    """状态机合法性：to_status 是否可从 from_status 一步流转。"""
    return to_status in ALLOWED_TRANSITIONS.get(from_status, set())


def judge_issue(*, result: dict | None, related_dimension: str | None,
                dim_target: float | None, issue_status: str,
                ) -> tuple[str | None, str | None]:
    """复现验证判定（6.2 核心口径，用户拍板：维度优先 + 整体回退）。

    输入 result = {"pass_fail": ..., "score_per_dimension": [{code,value,na,na_reason}]}；
    返回 (verify_result, skip_reason)：
    - error/na 用例 → 跳过（验收要求）；无 result（case 不在 run）→ 跳过
    - 关联维度：该维 na → 跳过；该维有值且有 dim_target → 维度判定；
      该维有值但无 dim_target / 该维未启用 → 回退整体（挑战点 2）
    - 未关联维度 → 整体 pass_fail
    - pass → fixed/verified（按 issue_status 区分：verified 态确认闭环，其余标 fixed）；
      fail → reproduced
    """
    if result is None:
        return None, "case 不在本 run"
    pf = result.get("pass_fail")
    if pf == "error":
        return None, "执行错误，跳过"
    if pf == "na":
        return None, "结果 N/A，跳过"
    fixed = pf == "pass"
    if related_dimension:
        entry = next((d for d in (result.get("score_per_dimension") or [])
                      if d.get("code") == related_dimension), None)
        if entry is not None:
            if entry.get("na"):
                return None, f"维度 {related_dimension} N/A，跳过"
            if dim_target is not None:
                # 维度优先：达标分 = baseline_target（interface 回退 agent 默认，同 scorer 门禁口径）
                fixed = (entry.get("value") or 0) >= dim_target
            # entry 有值但无达标分 → 维度无判据，回退整体
    # entry is None（该 run 未启用此维度）→ 回退整体
    if fixed:
        return ("verified" if issue_status == "verified" else "fixed"), None
    return "reproduced", None


def should_reopen(status: str, verify_result: str | None) -> bool:
    """回归自动重开：曾修好（fixed/verified）的 issue 被复现验证判为 reproduced。

    自动打回 open 由调用方执行（含 audit_log issue.reopen 追溯）。
    """
    return verify_result == "reproduced" and status in ("fixed", "verified")


def _resolve_targets(targets: dict, interface_id: int) -> dict:
    """interface 级达标分优先，缺省回退 agent 默认（interface_id=0 哨兵）。

    输入 targets = {(interface_id, dimension_code): target}（baseline_target 表形态）；
    judge_issue 的 dim_target 由此解析，与 scorer 门禁同口径（纯逻辑，宿主单测直接跑）。
    """
    resolved = {dcode: t for (iid, dcode), t in targets.items() if iid == interface_id}
    for (iid, dcode), t in targets.items():
        if iid == 0:
            resolved.setdefault(dcode, t)
    return resolved
