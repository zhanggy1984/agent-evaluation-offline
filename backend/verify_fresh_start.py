"""阶段 7 空库重建冒烟矩阵（容器内运行）。

用法：
    docker compose exec -T backend python verify_fresh_start.py

前置：空库已 `alembic upgrade head` + `python -m app.seed`。本脚本含首登强制改密闭环（P1-a）：
登录 → change-password → 重登，跑完库内 admin/evaluator/viewer 密码变为 SMOKE_PASSWORD
（冒烟测试专用，非幂等——重建空库后才能重跑）。

验证矩阵：
1. seed 完整性：15 张表行数精确断言（dimension/assertion_op_def/metric_def/judge_rubric/system_config/
   agent/agent_interface/agent_dimension_weight/scene_catalog/test_suite/test_case/case_scene/
   baseline_target/user/model_price）；system_config/user 期望值动态计算（见 TABLE_ROWS 注释）；
   seed 幂等复跑由宿主 tests/test_seed.py + 容器手工验证
2. 登录鉴权：admin 与 demo 账号统一「首登强制改密闭环」——登录断言 must_change_password=true →
   change-password（old=seed 初始密码, new=SMOKE_PASSWORD）→ 新密码重登断言 false；demo 按
   DEMO_EVALUATOR_PASSWORD / DEMO_VIEWER_PASSWORD env 是否存在决定验证（P0 安全收敛：未设不建，跳过）
3. 基础配置域：GET /config/model-prices（deepseek-chat 2 档版本化）/assertion-ops/rubrics 非空；
   dimension/metric_def 无独立读端点 → DB 行数断言（见 1）
4. agent 域：GET /agents=4；每家 interface=1 且 enabled、weights 4 维齐全、详情含 adapter_config；
   scenes 无独立读端点 → DB 断言 scene_catalog=17（见 1）
5. 用例域：GET /suites=4（四家各 1）；各 agent case_count 精确；gold 用例（cs「打招呼」）is_gold=true 且
   expected.judge_gold_scores 存在；cc file 型 case 的 file_ref 指向容器内 uploads 文件
6. 数据清理：POST /meta/cleanup 空库幂等无害（purged=0；无 preview 端点，仅触发端点）

注：alarm/overfit 配置口径验证已随「轻量化改造 v2.0」删除（app.core.alarm、app.runner.overfit
随治理功能移除，verify_alarm.py 等一批历史验证脚本同批废弃）。
"""
import asyncio
import os
import re
from pathlib import Path

import httpx
from sqlalchemy import text

from app.core.db import SessionLocal, engine
from app.judge.rubric import BUILTIN_RUBRICS

BASE = "http://127.0.0.1:8000"

# 空库 seed 后的凭据：admin 密码 = ADMIN_PASSWORD env（seed 缺则 fail-fast，未设无法建 admin）；
# demo 账号由 DEMO_EVALUATOR_PASSWORD / DEMO_VIEWER_PASSWORD env 决定存在与否（P0：未设不建）。
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
DEMO_CREDS = {
    "evaluator": os.environ.get("DEMO_EVALUATOR_PASSWORD"),
    "viewer": os.environ.get("DEMO_VIEWER_PASSWORD"),
}
# 冒烟改密测试密码（首登强制改密闭环用；跑完库内账号密码变为该值，docstring 已注明非幂等）
# P2-D18：compose 用 `:-` 注入空串（未设时），空串也算未设——`or` 兜底默认，不能用 get 的 default 参数
SMOKE_PASSWORD = os.environ.get("SMOKE_PASSWORD") or "Verify#Reset2026"


def _seed_config_count() -> int:
    """从 seed.py 源文件提取 DEFAULT_SYSTEM_CONFIG 顶层 key 数（期望值动态化，加配置项不再手动同步）。

    不 import seed（顶层 import security，宿主无 env 跑不了）；同 tests/test_frontend_meta_sync 正则手法。
    """
    text_ = (Path(__file__).parent / "app" / "seed.py").read_text(encoding="utf-8")
    block = re.search(r"DEFAULT_SYSTEM_CONFIG = \{(.*?)\n\}", text_, re.S)
    assert block, "seed.py 找不到 DEFAULT_SYSTEM_CONFIG 块"
    return len(re.findall(r'^ {4}"([\w.]+)":', block.group(1), re.M))


def _user_count() -> int:
    """admin 恒建 1 + env 注入存在的 demo 账号数（缺 env 不建，见 seed._seed_users）。"""
    return 1 + sum(1 for pw in DEMO_CREDS.values() if pw)


# 各表 seed 期望行数（数据唯一来源：app/seed.py + app/seed_data.py）
TABLE_ROWS = {
    "dimension": 7,
    "assertion_op_def": 10,
    "metric_def": 7,
    "system_config": _seed_config_count(),
    "agent": 4,
    "agent_interface": 4,
    "agent_dimension_weight": 16,
    "scene_catalog": 17,
    "test_suite": 4,
    "test_case": 7,
    "case_scene": 7,
    "baseline_target": 16,
    "user": _user_count(),
    "model_price": 2,
}


def _p(msg: str) -> None:
    print(msg, flush=True)


async def _api(client: httpx.AsyncClient, method: str, path: str, token: str | None = None,
               body=None, expect: int = 200) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = await client.request(method, BASE + path, json=body, headers=headers)
    if r.status_code != expect:
        raise RuntimeError(f"{method} {path} → {r.status_code}（期望 {expect}）: {r.text[:300]}")
    return r.json()


async def _login(client: httpx.AsyncClient, username: str, password: str) -> dict:
    auth = await _api(client, "POST", "/api/auth/login",
                      body={"username": username, "password": password})
    return auth["data"]


async def _login_and_force_change(client: httpx.AsyncClient, username: str, initial_pwd: str) -> dict:
    """首登强制改密闭环：登录断言 must_change_password=true → change-password → 新密码重登断言 false。

    返回改密后的 token 集（后续业务断言统一用它；改密后 refresh 族已撤销，必须重登拿新 token）。
    """
    tok = await _login(client, username, initial_pwd)
    assert tok["must_change_password"] is True, f"{username} 首登 must_change_password 应为 true"
    await _api(client, "POST", "/api/auth/change-password", tok["access_token"],
               {"old_password": initial_pwd, "new_password": SMOKE_PASSWORD})
    tok2 = await _login(client, username, SMOKE_PASSWORD)
    assert tok2["must_change_password"] is False, f"{username} 改密后 must_change_password 应为 false"
    return tok2


async def main() -> None:
    try:
        # ---------- 1. seed 完整性（DB 层） ----------
        async with SessionLocal() as db:
            for table, expect in TABLE_ROWS.items():
                cnt = (await db.execute(text(f"select count(*) from {table}"))).scalar()
                assert cnt == expect, f"{table}: 期望 {expect} 实际 {cnt}"
            jr = (await db.execute(text("select count(*) from judge_rubric"))).scalar()
            assert jr == len(BUILTIN_RUBRICS), f"judge_rubric: 期望 {len(BUILTIN_RUBRICS)} 实际 {jr}"
            _p(f"① seed 完整性：{len(TABLE_ROWS)} 张表行数全精确匹配 + judge_rubric={jr} 内置 rubric")

        assert ADMIN_PASSWORD, "verify 需 ADMIN_PASSWORD env（与 seed 同口径；未设时 seed 也不会建 admin）"

        async with httpx.AsyncClient(timeout=60, trust_env=False) as c:
            # ---------- 2. 登录鉴权（首登强制改密闭环） ----------
            adm = await _login_and_force_change(c, "admin", ADMIN_PASSWORD)
            assert adm["role"] == "admin"
            me = await _api(c, "GET", "/api/auth/me", adm["access_token"])
            assert me["data"]["must_change_password"] is False
            _p("② admin 登录→改密→重登闭环 OK：must_change_password true→false；me 同步 false")

            # demo 账号：env 注入的才存在（P0：未设不建），对存在的账号走同一改密闭环与权限矩阵
            tokens: dict[str, str] = {}
            for name, pw in DEMO_CREDS.items():
                if not pw:
                    _p(f"② {name} 未设 DEMO_{name.upper()}_PASSWORD，跳过（生产默认不建 demo 账号）")
                    continue
                tok = await _login_and_force_change(c, name, pw)
                assert tok["role"] == name
                tokens[name] = tok["access_token"]
                _p(f"② {name} 登录→改密→重登闭环 OK")

            # 权限矩阵：viewer 只读；evaluator 可建 suite 不可建 agent（仅对存在的 demo 账号验证）
            if "viewer" in tokens:
                await _api(c, "POST", "/api/agents", tokens["viewer"],
                           {"name": "x", "base_url": "http://127.0.0.1:9"}, expect=403)
                await _api(c, "POST", "/api/suites", tokens["viewer"],
                           {"agent_id": 1, "name": "x"}, expect=403)
            if "evaluator" in tokens:
                await _api(c, "POST", "/api/agents", tokens["evaluator"],
                           {"name": "x", "base_url": "http://127.0.0.1:9"}, expect=403)
                suite = await _api(c, "POST", "/api/suites", tokens["evaluator"],
                                   {"agent_id": 1, "name": "冒烟临时suite"})
                _p(f"② 权限矩阵：viewer 建 agent/suite 均 403；evaluator 建 agent 403、建 suite 200（id={suite['data']['id']}）")
                # 注：DELETE /suites 仅 admin（cases.py 设计）；临时 suite 用 admin token 清理
                await _api(c, "DELETE", f"/api/suites/{suite['data']['id']}", adm["access_token"])

            # 后续只读配置/agent/用例域：优先 evaluator token，缺则 admin 兜底（读权限等价）
            worker = tokens.get("evaluator") or adm["access_token"]

            # ---------- 3. 基础配置域 ----------
            prices = (await _api(c, "GET", "/api/config/model-prices", worker))["data"]
            assert len(prices) == 2, f"model-prices 应 2 档，实际 {len(prices)}"
            eff = {(p["model"], p["effective_from"][:10]) for p in prices}
            assert {"deepseek-chat", "deepseek-chat"} == {m for m, _ in eff}
            assert ("deepseek-chat", "2026-01-01") in eff and ("deepseek-chat", "2026-07-01") in eff, eff
            ops = (await _api(c, "GET", "/api/config/assertion-ops", worker))["data"]
            assert len(ops) == 10
            rubrics = (await _api(c, "GET", "/api/config/rubrics", worker))["data"]
            assert len(rubrics) == len(BUILTIN_RUBRICS)
            _p("③ 配置域：model-prices 2 档版本化 / assertion-ops 10 / rubrics 内置齐")

            # ---------- 4. agent 域 ----------
            agents = (await _api(c, "GET", "/api/agents", worker))["data"]
            assert len(agents) == 4
            for a in agents:
                assert a["adapter_config"], f"agent {a['name']} adapter_config 为空"
                ifaces = (await _api(c, "GET", f"/api/agents/{a['id']}/interfaces",
                                     worker))["data"]
                assert len(ifaces) == 1 and ifaces[0]["enabled"] is True, f"{a['name']} 接口缺失/停用"
                weights = (await _api(c, "GET", f"/api/agents/{a['id']}/weights",
                                      worker))["data"]
                assert set(weights) >= {"completeness", "factuality", "reasoning_quality", "tool_usage"}, \
                    f"{a['name']} 权重维度不全"
            _p("④ agent 域：4 家 adapter_config / interface / 权重 4 维齐全")

            # ---------- 5. 用例域 ----------
            suites = (await _api(c, "GET", "/api/suites", worker))["data"]
            assert len(suites) == 4, f"suites 应 4，实际 {len(suites)}"
            name2suite = {s["agent_name"]: s for s in suites}
            expected = {"customer-service": 2, "contract-check": 1,
                        "smart-procurement": 2, "good-question": 2}
            for agent_name, n in expected.items():
                assert name2suite[agent_name]["case_count"] == n, \
                    f"{agent_name}: case_count 期望 {n} 实际 {name2suite[agent_name]['case_count']}"
            # gold 用例：cs「打招呼」is_gold + judge_gold_scores
            cs_cases = (await _api(c, "GET", f"/api/suites/{name2suite['customer-service']['id']}/cases",
                                   worker))["data"]
            gold = next(cc for cc in cs_cases if cc["is_gold"])
            gold_detail = (await _api(c, "GET", f"/api/cases/{gold['id']}", worker))["data"]
            assert gold_detail["name"] == "打招呼"
            assert "judge_gold_scores" in gold_detail["expected"], "gold 用例缺 judge_gold_scores"
            # cc file 型 case：列表视图不含 file_ref，走详情接口；file_ref 指向容器内 uploads 文件
            cc_cases = (await _api(c, "GET", f"/api/suites/{name2suite['contract-check']['id']}/cases",
                                   worker))["data"]
            assert len(cc_cases) == 1 and cc_cases[0]["input_type"] == "file"
            cc_detail = (await _api(c, "GET", f"/api/cases/{cc_cases[0]['id']}",
                                    worker))["data"]
            fr = cc_detail["file_ref"]
            assert fr and os.path.exists(fr), f"cc file 型 case file_ref 不存在: {fr}"
            _p("⑤ 用例域：4 suite / case 数精确 / gold 判分依据 / cc file 型用例文件就位")

            # ---------- 6. 数据清理 ----------
            res = (await _api(c, "POST", "/api/meta/cleanup", worker))["data"]
            assert res["purged"] == 0, f"空库清理应 purged=0，实际 {res['purged']}"
            _p("⑥ 数据清理：空库幂等无害（purged=0；无 preview 端点，仅触发端点）")

            _p("verify_fresh_start 全部通过 ✓")
    finally:
        await engine.dispose()


asyncio.run(main())
