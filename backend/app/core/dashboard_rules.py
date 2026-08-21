"""5.3 看板聚合纯逻辑（零 DB 依赖，宿主单测直接跑）。

与 test_cases 测 case_rules 同模式：只 import 本模块，不触 app.core.db
（宿主无 aiomysql/aiosqlite 时 create_async_engine 会加载驱动报错）。
"""
from statistics import mean, stdev

from app.core.constants import ACCURACY_DIMENSIONS

# run 终态：只有终态 run 才有评分/分数（cancel 不产生结果）
TERMINAL_STATUS = {"completed", "partial_failed", "scoring_failed", "timeout", "cancelled"}
# 6.3 基线对比只取「有评分」的终态（timeout/cancelled 无 score_per_dimension，scoring_failed 评分未完成）
BASELINE_RUN_STATUS = ("completed", "partial_failed")


def build_gate_cards(agents, runs, suites, case_cnt: dict) -> list[dict]:
    """L0 门禁墙：逐 agent 最新版本跨 suite 汇总 + 陈旧 suite 单独标注。

    - 当前版本 = 最近终态 run（按 started_at）反推
    - 该版本下所有终态 run 跨 suite 汇总 agent_score（各 run 简单均值）
    - 陈旧 suite = 该 agent 有 suite 但当前版本下无终态 run
    - 无终态 run 的 agent → version=None，stale 列出全部 suite
    """
    out = []
    for agent in agents:
        agent_runs = [r for r in runs
                      if r.agent_id == agent.id and r.status in TERMINAL_STATUS and r.started_at]
        agent_suites = [s for s in suites if s.agent_id == agent.id]
        if not agent_runs:
            card = _card(agent, None, None, [], agent_suites, case_cnt, 0, 0, 0)
            out.append(card)
            continue
        latest = max(agent_runs, key=lambda r: r.started_at)
        version = latest.version
        version_runs = [r for r in agent_runs if r.version == version]
        scored = [float(r.agent_score) for r in version_runs if r.agent_score is not None]
        total = sum(r.total_case or 0 for r in version_runs)
        card = _card(
            agent, version, round(sum(scored) / len(scored), 2) if scored else None,
            version_runs, agent_suites, case_cnt, total,
            sum(r.pass_case or 0 for r in version_runs),
            sum(r.fail_case or 0 for r in version_runs))
        out.append(card)
    return out


def _card(agent, version, agent_score, version_runs, agent_suites, case_cnt,
          total_case, pass_case, fail_case) -> dict:
    """组装单个 agent 的门禁墙卡片（含陈旧 suite 标注）。

    陈旧标注只针对「有用例内容」的 suite：空 suite（0 case）不可评测也无评测价值，
    标注只会造成噪音（阶段 4 走查 #6：走查-临时空 suite 被误标陈旧）。
    """
    stale = [{"id": s.id, "name": s.name, "case_count": case_cnt.get(s.id, 0)}
             for s in agent_suites
             if s.id not in {r.suite_id for r in version_runs}
             and case_cnt.get(s.id, 0) > 0]
    return {
        "agent_id": agent.id, "agent_name": agent.name,
        "version": version,
        "agent_score": agent_score,
        "pass_rate": round(pass_case / total_case, 4) if total_case else None,
        "total_case": total_case, "pass_case": pass_case, "fail_case": fail_case,
        "error_case": sum(r.error_case or 0 for r in version_runs),
        "latest_finished_at": max((r.finished_at for r in version_runs
                                   if r.finished_at), default=None),
        "stale_suites": stale,
    }


def significance(series: list[float], ma: float | None, mb: float | None):
    """L2 版本对比 2σ 显著性：样本标准差 σ，|Δ| > 2σ 标 up/down。

    数据不足（run 数 < 3 或任一方无分数）→ insufficient，不标注。
    """
    if ma is None or mb is None:
        return None, "insufficient"
    if len(series) < 3:
        return None, "insufficient"
    sd = stdev(series)
    if sd == 0:
        return 0.0, "flat"  # 历史无波动，视为无显著变化
    delta = mb - ma
    if abs(delta) > 2 * sd:
        return sd, ("up" if delta > 0 else "down")
    return sd, "flat"


def resolve_targets(targets: dict, interface_id: int) -> dict:
    """baseline_target 解析：interface 级优先，缺省回退 agent 默认（interface_id=0 哨兵）。"""
    resolved = {dc: t for (iid, dc), t in targets.items() if iid == interface_id}
    for (iid, dc), t in targets.items():
        if iid == 0:
            resolved.setdefault(dc, t)
    return resolved


def build_coverage(interfaces, used_interface_ids: set, scenes, tagged_scene_tags: set) -> dict:
    """覆盖率聚合（5.4 覆盖率页）：接口覆盖 + 场景覆盖，盲区单独列出。

    - 接口：enabled 接口全量 vs 有用例标注（used_interface_ids）→ 已覆盖 / 盲区
    - 场景：SceneCatalog 全量 vs 用例打标（tagged_scene_tags）→ 已覆盖 / 盲区
    - rate = covered / total（total 为 0 → None，前端显 N/A）
    """
    covered_if = [i for i in interfaces if i.id in used_interface_ids]
    blank_if = [i for i in interfaces if i.id not in used_interface_ids]
    covered_sc = [s for s in scenes if s.scene_tag in tagged_scene_tags]
    blank_sc = [s for s in scenes if s.scene_tag not in tagged_scene_tags]
    return {
        "interface_total": len(interfaces), "interface_covered": len(covered_if),
        "interface_rate": round(len(covered_if) / len(interfaces), 4) if interfaces else None,
        "interface_blank": [{"id": i.id, "name": i.name, "path": i.path, "method": i.method}
                            for i in blank_if],
        "scene_total": len(scenes), "scene_covered": len(covered_sc),
        "scene_rate": round(len(covered_sc) / len(scenes), 4) if scenes else None,
        "scene_blank": [{"tag": s.scene_tag, "description": s.description} for s in blank_sc],
    }


# L3 用例明细排序：error/fail 靠前，其余按分数降序
PF_ORDER = {"error": 0, "fail": 1, "na": 2, "pass": 3}


def build_baseline(results, case_interface: dict, targets: dict, interfaces) -> list[dict]:
    """6.3 基线对比：run 内结果按接口分组，逐 accuracy 维度均值 vs 达标分。

    - case_interface: {case_id: interface_id}（test_case.interface_id）
    - targets: {(interface_id, dimension_code): target}，接口级优先 + agent 默认（0）回退
      —— 与 scorer._resolve_targets 同口径，防漂移
    - 每接口返回 dims：score（有效值均值）/ target / gap=score-target / met（达标判定）
    - 无该维度有效值或未配置 target → score/target/gap=None，met=None（前端显「—/未配置」）
    - 接口按 interfaces 顺序返回，仅含该 run 有用例的接口
    """
    groups: dict[int, list] = {}
    for r in results:
        iid = case_interface.get(r.case_id)
        if iid is None:
            continue  # case 无 interface 关联（异常数据）→ 不计入任何接口
        groups.setdefault(iid, []).append(r)
    out = []
    for iface in interfaces:
        rows = groups.get(iface.id, [])
        if not rows:
            continue
        dims = []
        for dim in ACCURACY_DIMENSIONS:
            vals = []
            for r in rows:
                entry = next((e for e in (r.score_per_dimension or [])
                              if e.get("code") == dim), None)
                if entry is None or entry.get("na") or entry.get("value") is None:
                    continue
                vals.append(float(entry["value"]))
            score = round(mean(vals), 2) if vals else None
            target = targets.get((iface.id, dim))
            if target is None:
                target = targets.get((0, dim))
            if score is None or target is None:
                gap, met = None, None
            else:
                gap, met = round(score - target, 2), score >= target
            dims.append({"code": dim, "score": score, "target": target,
                         "gap": gap, "met": met})
        out.append({"interface_id": iface.id, "name": iface.name,
                    "case_count": len(rows), "dims": dims})
    return out
