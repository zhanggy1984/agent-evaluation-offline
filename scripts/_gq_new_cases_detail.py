# -*- coding: utf-8 -*-
"""一次性：查 gq suite 2161 新增 6 case（3612-3617）某 run 的判定明细。

用法：python scripts/_gq_new_cases_detail.py <run_id>
输出：逐 case pass/score/断言结果/answer 摘要，用于验证新 case 断言命中。
"""
import json
import os
import sys

import pymysql


def _env_secret(key: str, default: str = "") -> str:
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
    return default


def _conn() -> pymysql.Connection:
    # 本机跑连的是宿主端口映射（docker 内部 host 是 mysql:3306 连不上），
    # DB_HOST/DB_PORT 只认 env 显式覆盖，默认 127.0.0.1:33061；其余凭据走 ../.env。
    return pymysql.connect(
        host=os.environ.get("DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("DB_PORT", "33061")),
        charset="utf8mb4",
        user=os.environ.get("DB_USER") or _env_secret("DB_USER"),
        password=os.environ.get("DB_PASSWORD") or _env_secret("DB_PASSWORD"),
        database=os.environ.get("DB_NAME") or _env_secret("DB_NAME"),
    )


NEW_IDS = (3612, 3613, 3614, 3615, 3616, 3617)


def main() -> None:
    run_id = int(sys.argv[1])
    conn = _conn()
    with conn.cursor() as cur:
        cur.execute("SELECT id, name FROM test_case WHERE id IN %s", (NEW_IDS,))
        names = dict(cur.fetchall())
        cur.execute("""SELECT case_id, pass_fail, score_total, assertion_results, answer
                       FROM eval_result WHERE run_id=%s AND case_id IN %s""",
                    (run_id, NEW_IDS))
        rows = cur.fetchall()
    conn.close()

    got = {r[0]: r for r in rows}
    for cid in NEW_IDS:
        nm = names.get(cid, "?")
        row = got.get(cid)
        if not row:
            print(f"{cid} {nm}: 无评测结果")
            continue
        _, pf, st, ar, ans = row
        print(f"===== {cid} {nm} [{pf}] score={st}")
        if ar:
            for a in json.loads(ar):
                ok = "PASS" if a.get("pass") else "FAIL"
                print(f"  {ok} {a.get('op')} {json.dumps(a.get('args', {}), ensure_ascii=False)}")
        print("  answer:", (ans or "")[:260])


if __name__ == "__main__":
    main()
