# -*- coding: utf-8 -*-
"""一次性（T10）：把 seed_real_cases.py 更新后的 reference_docs 同步到线上 cs/sp 新 suite 用例。

背景：cs 3133/3135/3137（factuality 低分）与 sp 3154 的 reference_docs 缺失真实明细
（金额/数量/大促条款/CMMI3/质保24月），judge 误判真实数据为编造。seed 常量已补全，
本脚本按 case name 匹配线上 case，PUT 覆盖 expected（整体替换，保留 golden_answer）。

注意：PUT /cases/{id} 的 expected 是整体覆盖（setattr），必须传完整 expected。
"""
from __future__ import annotations

import sys
import os
import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seed_real_cases as S  # noqa: E402

PLATFORM = S.PLATFORM
AGENTS = {"cs": (2298, S.cs_real_cases), "sp": (2297, S.sp_real_cases)}


def main() -> None:
    with httpx.Client(timeout=30, trust_env=False) as c:
        h = S._auth(c)

        for agent, (aid, builder) in AGENTS.items():
            # 1) 定位该 agent 的「真实业务评测」suite
            r = c.get(f"{PLATFORM}/suites", headers=h, params={"agent_id": aid})
            if r.status_code >= 400:
                print(f"✗ {agent} suites 查询失败 HTTP {r.status_code}: {r.text[:300]}")
                continue
            suites = [s for s in r.json()["data"] if s["name"] == f"{agent} 真实业务评测"]
            if not suites:
                print(f"  - {agent} 无新 suite，跳过")
                continue
            suite_id = suites[0]["id"]

            # 2) 拉线上 case 列表
            r = c.get(f"{PLATFORM}/suites/{suite_id}/cases", headers=h)
            online = {x["name"]: x["id"] for x in r.json()["data"]}

            # 3) 按 name 匹配 seed 最新定义，覆盖 expected
            ok_n = 0
            for body in builder():
                name = body["name"]
                cid = online.get(name)
                if cid is None:
                    print(f"  - 未匹配线上 case: {name}")
                    continue
                exp = body.get("expected") or {}
                if not exp.get("reference_docs"):
                    continue
                r = c.put(f"{PLATFORM}/cases/{cid}", headers=h,
                          json={"expected": exp})
                if r.status_code >= 400:
                    print(f"  ✗ PUT {name}({cid}) HTTP {r.status_code}: {r.text[:300]}")
                else:
                    ok_n += 1
            print(f"  ✓ {agent} 同步 reference_docs {ok_n} 条（suite={suite_id}）")


if __name__ == "__main__":
    main()
