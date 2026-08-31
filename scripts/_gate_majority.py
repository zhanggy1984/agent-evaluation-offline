# -*- coding: utf-8 -*-
"""门禁多数决评测 V2（发布前规程，见 上线待办.md「门禁判定规程」）。

背景：judge 维度（factuality/reasoning）temperature=0 下仍有 LLM 服务端采样噪声，
且分数仅 6 档步进 20，门禁 85 等价于必须满分档 → 单次 run 的 80 漂移会误伤 fail
（3182 铁证：同一 232 字回答 3 次 judge = fail/pass/pass）。

V2 三层分级判定（2026-08-31 用户确认）：
  1. 规则维度（completeness/tool_usage）：断言确定性高，3 次全过才过（fail 即真实问题）
  2. judge 维度（factuality/reasoning）：≥2/3 多数决；3 次全 fail → 强 fail 不可翻案
     （全 fail 概率 0.008，是真实退化信号不是噪声）
  3. run 级：agent_score 3 次均值 ≥ --gate-line（默认 85）

用法：
  python scripts/_gate_majority.py <agent_id> <suite_id> [--runs 3] [--version 0.2.0] [--gate-line 85]

平台限制：同一 agent 同时只能 1 个进行中 run（409）→ 必须触发-等待交织串行。
"""
from __future__ import annotations

import argparse
import os
import time

import httpx

PLATFORM = "http://localhost:8180/api"


def _secret(key: str, hint: str = "") -> str:
    """敏感配置：优先环境变量，兜底读项目根 ../.env（不硬编码凭据）。"""
    val = os.environ.get(key)
    if val:
        return val
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
    try:
        with open(env_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    raise SystemExit(f"缺少 {key}（环境变量或 ../.env 中配置）{hint}")


RULE_DIMS = ["completeness", "tool_usage"]
JUDGE_DIMS = ["factuality", "reasoning_quality"]


def _judge_met(value: float, target: float) -> bool:
    """#2 档位化：judge 维度达标判定（与 backend runner/scorer._gate_met 同规则）。

    judge 分恒为 6 档步进 20（0/20/.../100），target 是连续双签值。直接连续比较会让
    85 target 只有 100 档能过（80<85 悬崖）。target 归 floor 档、value 达 floor 档即过
    （85/92 → 4 档，80 可过）。负数无档位语义，回退连续分比较。
    脚本独立运行不 import 后端包，判据复制防漂移。
    """
    if target >= 0 and value >= 0:
        return int(value // 20) >= int(target // 20)
    return value >= target


def decide_case(dims: dict[str, list[float | None]],
                tgts: dict[str, float]) -> tuple[bool, bool, list[str], list[str]]:
    """单个 case 三层分级判定（V2 门禁核心逻辑，纯函数可单测）。

    规则维度（completeness/tool_usage）：3 次全过才过，1 次 fail 即真实问题；
    judge 维度（factuality/reasoning_quality）：≥2/3 多数决，0/3 全 fail 强 fail。
    全 N/A 的维度不判。

    返回 (rule_ok, judge_ok, rule_reason, judge_reason)。
    """
    rule_reason, judge_reason = [], []
    rule_ok = judge_ok = True
    for dim, t in tgts.items():
        vals = [v for v in dims.get(dim, []) if v is not None]
        if not vals:
            continue  # 全 N/A 不判
        if dim in RULE_DIMS:
            fails = sum(1 for v in vals if v < t)
            if fails:
                rule_ok = False
                rule_reason.append(f"{dim}({fails}次<{t})")
        elif dim in JUDGE_DIMS:
            # #2 档位化：judge 80 对 target 85 不再 fail（同档 4 档）；全 fail 强 fail 语义保留
            pc = sum(1 for v in vals if _judge_met(v, t))
            if pc == 0:
                judge_ok = False
                judge_reason.append(f"{dim}全fail<{t}")
            elif pc < 2:
                judge_ok = False
                judge_reason.append(f"{dim}({pc}/3<{t})")
    return rule_ok, judge_ok, rule_reason, judge_reason


def _auth(c: httpx.Client) -> dict:
    r = c.post(f"{PLATFORM}/auth/login",
               json={"username": "admin", "password": _secret("ADMIN_PASSWORD", "（平台 admin 登录口令）")})
    if r.status_code >= 400:
        raise SystemExit(f"登录失败 HTTP {r.status_code}: {r.text[:300]}")
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


def _trigger(c: httpx.Client, h: dict, agent_id: int, suite_id: int, version: str) -> int:
    for _ in range(10):
        r = c.post(f"{PLATFORM}/runs", headers=h,
                   json={"agent_id": agent_id, "suite_id": suite_id, "version": version})
        if r.status_code == 200 and r.json().get("data"):
            return r.json()["data"]["id"]
        print(f"  触发失败 HTTP {r.status_code} {r.text[:120]} 重试", flush=True)
        time.sleep(3)
    raise SystemExit(f"触发 run 失败（agent={agent_id} suite={suite_id}）")


def _wait(c: httpx.Client, h: dict, rid: int) -> str:
    for _ in range(400):
        r = c.get(f"{PLATFORM}/runs/{rid}", headers=h)
        st = r.json()["data"]["status"]
        if st in ("completed", "failed", "cancelled", "partial_failed"):
            return st
        time.sleep(15)
    return "timeout"


def main() -> None:
    ap = argparse.ArgumentParser(description="门禁多数决评测 V2")
    ap.add_argument("agent_id", type=int)
    ap.add_argument("suite_id", type=int)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--version", default="0.2.0")
    ap.add_argument("--gate-line", type=float, default=85.0, help="run 级 agent_score 均值门禁线")
    args = ap.parse_args()

    import json

    import pymysql
    _db = dict(host="127.0.0.1", port=33061, charset="utf8mb4",
               user="evaluation", database="ai_evaluation")
    _db["password"] = _secret("DB_PASSWORD")
    conn = pymysql.connect(**_db)

    with conn.cursor() as cur:
        # 加载该 agent 的 target（interface 级 + agent 默认哨兵 0）
        cur.execute("""SELECT interface_id, dimension_code, target_score
                       FROM baseline_target WHERE agent_id=%s""", (args.agent_id,))
        trows = cur.fetchall()
        if not trows:
            raise SystemExit(f"agent={args.agent_id} 无 baseline_target，无法门禁判定")
        t_by_iface: dict[int, dict[str, float]] = {}
        for iid, dim, t in trows:
            t_by_iface.setdefault(iid, {})[dim] = float(t)
        def _targets(iid: int) -> dict[str, float]:
            out = {}
            for d, t in t_by_iface.get(iid, {}).items():
                out[d] = t
            for d, t in t_by_iface.get(0, {}).items():
                out.setdefault(d, t)
            return out

    with httpx.Client(timeout=60, trust_env=False) as c:
        h = _auth(c)
        rids = []
        print(f"串行触发 {args.runs} 次 run（agent={args.agent_id} suite={args.suite_id}）", flush=True)
        for i in range(args.runs):
            rid = _trigger(c, h, args.agent_id, args.suite_id, args.version)
            rids.append(rid)
            print(f"  [{i+1}/{args.runs}] run={rid} 等待完成…", flush=True)
            st = _wait(c, h, rid)
            print(f"  run={rid} status={st}", flush=True)
            time.sleep(2)

    # 触发 run 耗时较长，旧 conn 可能已失效（MySQL gone away）→ 查询前重新连接
    _db = dict(host="127.0.0.1", port=33061, charset="utf8mb4",
               user="evaluation", database="ai_evaluation")
    _db["password"] = _secret("DB_PASSWORD")
    conn = pymysql.connect(**_db)
    placeholders = ",".join(["%s"] * len(rids))
    with conn.cursor() as cur:
        cur.execute(f"""SELECT er.case_id, tc.name, tc.interface_id, er.run_id,
                               er.score_total, er.score_per_dimension
                        FROM eval_result er JOIN test_case tc ON er.case_id=tc.id
                        WHERE er.run_id IN ({placeholders}) ORDER BY er.case_id, er.run_id""", tuple(rids))
        rows = cur.fetchall()
    conn.close()

    # 重组：case → {dim: [3 次 value]} + run 均分
    dim_vals: dict[int, dict[str, list[float | None]]] = {}
    case_meta: dict[int, tuple[str, int]] = {}
    run_scores: list[float] = []
    for case_id, name, iid, run_id, st_, spd in rows:
        case_meta.setdefault(case_id, (name, iid))
        dims = dim_vals.setdefault(case_id, {})
        if isinstance(spd, str):
            try:
                spd = json.loads(spd)
            except json.JSONDecodeError:
                spd = []
        for d in (spd or []):
            code = d.get("code")
            if code in (RULE_DIMS + JUDGE_DIMS):
                dims.setdefault(code, []).append(d.get("value") if not d.get("na") else None)
        if st_ is not None:
            run_scores.append(float(st_))
    run_avg = round(sum(run_scores) / len(run_scores), 2) if run_scores else None

    print(f"\n{'case_id':<8}{'用例名':<30}{'规则':<10}{'judge':<22}{'判定':<6}")
    all_pass = True
    for case_id in sorted(dim_vals):
        name, iid = case_meta[case_id]
        tgts = _targets(iid)
        dims = dim_vals[case_id]
        rule_ok, judge_ok, rule_reason, judge_reason = decide_case(dims, tgts)
        verdict = "PASS" if (rule_ok and judge_ok) else "FAIL"
        if verdict != "PASS":
            all_pass = False
        print(f"{case_id:<8}{name:<30}{'OK' if rule_ok else '!':<10}"
              f"{(','.join(judge_reason) if judge_reason else 'OK'):<22}{verdict:<6}")
    print(f"\nrun agent_score 均值: {run_avg}  (门禁线 {args.gate_line})")
    mean_ok = run_avg is not None and run_avg >= args.gate_line
    ok = all_pass and mean_ok
    print(f"== 门禁结论: {'通过' if ok else 'FAIL '} ==")
    if not all_pass:
        print("  存在 case 未过（规则全过/judge 多数决/全fail强fail 任一层未过）")
    if not mean_ok:
        print(f"  run 均分 {run_avg} < 门禁线 {args.gate_line}")


if __name__ == "__main__":
    main()
