"""Q9 完整对接工具：一键把 agent 按 manifest v2 标准对接进评测平台（完整链路）。

对比 verify_agent.py（只到 probe 冒烟），本工具补齐 discover / scenes / skeleton /
suite+case 落库，产出每步结果 JSON —— 对接交付物。**冒烟断言口径与 verify_agent
完全一致**（登录→get_or_create→adapter→sync llm→auth→probe），避免两套口径漂移。

用法：
  python scripts/onboard_agent.py \
      --agent-url http://host.docker.internal:8000 \
      --name customer-service \
      --platform-url http://localhost:8180/api \
      --admin-pwd '<口令>'（缺省读 ADMIN_PASSWORD env/../.env）\
      --secrets '{"username": "csadmin", "password": "xxx"}' \
      --input '{"content": "你好"}' \
      --out results/cs.json

步骤：discover → adapter 确认（权威生成落库）→ 接口 sync → 场景 sync → 凭证配置 →
probe 冒烟（断言 ok + 契约字段）→ skeleton 骨架（带 probe_input merge）→ suite+case
落库。adapter_drift 非空仅记录不阻断（DB 存量 v1 agent 无快照会报「平台无 manifest
快照」软提示，adapter 落库后自然消失）。
httpx 一律 trust_env=False（Windows 系统代理 memory）。
"""
import argparse
import json
import os
import sys
import time

import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
from app.core.contracts_v2 import parse_manifest_v2  # noqa: E402

_passed: list[str] = []
_failed: list[str] = []


def _check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        _passed.append(name)
        print(f"  ✓ {name}")
    else:
        _failed.append(name)
        print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))


def _fetch_manifest(agent_url: str) -> tuple[dict | None, str]:
    """拉取并基本校验 v2 manifest → (payload, 错误消息)。"""
    url = agent_url.rstrip("/") + "/api/contracts"
    try:
        r = httpx.get(url, timeout=30.0, trust_env=False)
    except Exception as exc:
        return None, f"拉取 manifest 失败: {type(exc).__name__}: {exc}"
    if r.status_code != 200:
        return None, f"标准端点返回 HTTP {r.status_code}"
    try:
        payload = r.json()
    except Exception:
        return None, "标准端点响应不是 JSON"
    if not isinstance(payload, dict) or not isinstance(payload.get("contract"), dict):
        return None, "非 v2 manifest（无 contract 段）"
    return payload, ""


def _read_secret(key: str, hint: str = "") -> str:
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


def _auth(c: httpx.Client, platform_url: str, admin_pwd: str) -> dict:
    r = c.post(f"{platform_url}/auth/login",
               json={"username": "admin", "password": admin_pwd})
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


def _get_or_create_agent(c, h, platform_url, name, base_url) -> int:
    r = c.get(f"{platform_url}/agents", headers=h)
    r.raise_for_status()
    for a in r.json()["data"]:
        if a["name"] == name:
            return a["id"]
    r = c.post(f"{platform_url}/agents", headers=h, json={
        "name": name, "base_url": base_url, "adapter_type": "config",
        "adapter_config": {}, "contract_version": "2.0"})
    r.raise_for_status()
    return r.json()["data"]["id"]


def _assert_sse(data: dict) -> None:
    """SSE §5.1 契约断言（口径与 verify_agent 一致）：usage/done 必选。"""
    _check("probe 整体达标", data.get("ok") is True, f"ok={data.get('ok')}")
    for itf in data["interfaces"]:
        f = itf["fields"]
        _check(f"HTTP 200（{itf['interface_id']}）", itf["http_status"] == 200,
               f"http={itf['http_status']}")
        _check("usage 事件到达（§5.1 必选）", f.get("usage") is True, f"fields={f}")
        _check("done 事件到达（§5.1 必选）", f.get("done") is True)
        _check("answer 事件到达", f.get("answer") is True)
        _check("errors 为空", not itf["errors"], f"errors={itf['errors']}")
        print(f"   events={f.get('events')} ttft_ms={f.get('ttft_ms')}")


def _assert_sync(data: dict) -> None:
    """同步 §5.2 契约断言：answer/usage/timing 必选。"""
    _check("probe 整体达标", data.get("ok") is True, f"ok={data.get('ok')}")
    for itf in data["interfaces"]:
        f = itf["fields"]
        _check(f"HTTP 200（{itf['interface_id']}）", itf["http_status"] == 200,
               f"http={itf['http_status']}")
        _check("answer 非空（§5.2 必选）", f.get("answer") is True, f"fields={f}")
        _check("usage 合法（§5.2 必选）", f.get("usage") is True)
        _check("timing start/end 存在（§5.2 必选）",
               f.get("timing_start") is True and f.get("timing_end") is True)
        _check("errors 为空", not itf["errors"], f"errors={itf['errors']}")
        print(f"   elapsed_ms={itf.get('elapsed_ms')}")


def onboard(agent_url: str, platform_url: str, admin_pwd: str,
            name: str, base_url: str, input_: dict | None, secrets: dict | None,
            out_path: str | None) -> int:
    """完整对接流程，返回退出码。每一步都记录进 results。"""
    print(f"── 完整对接: agent={name} base_url={base_url} ──")
    results: dict = {"agent": name, "base_url": base_url,
                     "started_at": time.strftime("%Y-%m-%d %H:%M:%S")}

    payload, err = _fetch_manifest(agent_url)
    if payload is None:
        print(f"✗ {err}")
        results["error"] = err
        _dump(out_path, results)
        return 1

    manifest, errs = parse_manifest_v2(payload)
    if manifest is None:
        results["error"] = "; ".join(errs)
        print(f"✗ manifest 解析失败: {results['error']}")
        _dump(out_path, results)
        return 1

    with httpx.Client(timeout=httpx.Timeout(180.0, connect=10.0), trust_env=False) as c:
        h = _auth(c, platform_url, admin_pwd)
        print("✓ 平台登录成功")

        aid = _get_or_create_agent(c, h, platform_url, name, base_url)
        print(f"✓ agent id={aid}")

        # 1. discover：确认 v2 + adapter 草案 + drift（非空仅记录不阻断）
        r = c.post(f"{platform_url}/agents/{aid}/discover", headers=h)
        r.raise_for_status()
        disc = r.json()["data"]
        drift = disc.get("adapter_drift") or []
        results["discover"] = {
            "ok": disc.get("ok"), "contract_version": disc.get("contract_version"),
            "adapter_drift": drift,
        }
        if disc.get("ok") is not True:
            print(f"✗ discover 失败: {disc.get('errors')}")
            results["error"] = disc.get("errors")
            _dump(out_path, results)
            return 1
        print(f"✓ discover: version={disc.get('contract_version')} "
              f"drift={'（非空，仅记录）' if drift else '（空）'}")
        for d in drift:
            print(f"   drift: {d}")

        # 2. adapter 确认（服务端权威生成，Q1 硬错误闸门兜底）
        r = c.post(f"{platform_url}/agents/{aid}/adapter", headers=h,
                   json={"manifest": payload})
        if r.status_code >= 400:
            results["error"] = f"adapter 确认失败 HTTP {r.status_code}: {r.text[:300]}"
            print(f"✗ {results['error']}")
            _dump(out_path, results)
            return 1
        ad = r.json()["data"]
        results["adapter"] = {"contract_version": ad.get("contract_version"),
                              "requires_auth": ad.get("requires_auth"),
                              "input_fields": ad.get("input_fields"),
                              "warnings": ad.get("warnings")}
        print(f"✓ adapter 落库: requires_auth={ad.get('requires_auth')} "
              f"input_fields={ad.get('input_fields')}")

        # 3. 接口 sync（只 sync llm 评测接口，辅助接口由 manifest prepare 驱动）
        ifaces = [{"name": i["name"], "path": i["path"], "method": i["method"],
                   "contract_type": i["contract_type"]}
                  for i in payload["interfaces"] if i.get("llm")]
        if not ifaces:
            results["error"] = "manifest 无 llm=true 评测接口"
            print(f"✗ {results['error']}")
            _dump(out_path, results)
            return 1
        r = c.post(f"{platform_url}/agents/{aid}/interfaces/sync", headers=h,
                   json={"interfaces": ifaces})
        if r.status_code >= 400:
            results["error"] = f"接口 sync 失败 HTTP {r.status_code}: {r.text[:300]}"
            print(f"✗ {results['error']}")
            _dump(out_path, results)
            return 1
        sync = r.json()["data"]
        results["interfaces"] = sync
        print(f"✓ 接口 sync: created={sync['created']} skipped={sync['skipped']}")

        # 4. 场景 sync（幂等）
        scenes = [{"tag": s["tag"], "description": s.get("description", "")}
                  for s in payload.get("scenes", [])]
        if scenes:
            r = c.post(f"{platform_url}/agents/{aid}/scenes", headers=h,
                       json={"scenes": scenes})
            if r.status_code >= 400:
                results["error"] = f"场景 sync 失败 HTTP {r.status_code}: {r.text[:300]}"
                print(f"✗ {results['error']}")
                _dump(out_path, results)
                return 1
            sc = r.json()["data"]
            results["scenes"] = sc
            print(f"✓ 场景 sync: created={len(sc['created'])} skipped={len(sc['skipped'])}")
        else:
            results["scenes"] = {"created": [], "skipped": []}
            print("✓ 场景 sync: manifest 无场景")

        # 5. 凭证配置
        if secrets:
            r = c.put(f"{platform_url}/agents/{aid}/auth", headers=h,
                      json={"secrets": secrets})
            if r.status_code >= 400:
                results["error"] = f"凭证配置失败 HTTP {r.status_code}: {r.text[:300]}"
                print(f"✗ {results['error']}")
                _dump(out_path, results)
                return 1
            results["auth"] = r.json()["data"]
            print("✓ 凭证已配置（{{auth.*}} 域）")

        # 6. probe 冒烟（断言契约字段）
        r = c.post(f"{platform_url}/agents/{aid}/probe", headers=h,
                   json={"input": input_} if input_ else {})
        if r.status_code >= 400:
            results["error"] = f"冒烟探测失败 HTTP {r.status_code}: {r.text[:300]}"
            print(f"✗ {results['error']}")
            _dump(out_path, results)
            return 1
        probe = r.json()["data"]
        results["probe"] = {"ok": probe.get("ok"),
                            "interfaces": probe.get("interfaces")}
        ctype = payload["contract"]["type"]
        if ctype == "sse":
            _assert_sse(probe)
        else:
            _assert_sync(probe)

        # 7. skeleton 骨架（带 probe_input merge）+ 落库 suite/case
        r = c.post(f"{platform_url}/agents/{aid}/skeleton", headers=h,
                   json={"probe_input": input_} if input_ else {})
        if r.status_code >= 400:
            results["error"] = f"骨架生成失败 HTTP {r.status_code}: {r.text[:300]}"
            print(f"✗ {results['error']}")
            _dump(out_path, results)
            return 1
        sk = r.json()["data"]
        results["skeleton"] = sk
        print(f"✓ 骨架生成: {len(sk['cases'])} cases (suite={sk['suite']['name']})")

        # 落库：suite + 按 interface_name 匹配 id 的 cases
        lib = _suites_and_cases(c, h, platform_url, aid, sk)
        results["persist"] = lib
        print(f"✓ 落库: suite_id={lib['suite_id']} cases={lib['case_ids']}")

    print(f"\n========== 完整对接: {len(_passed)} 通过 / {len(_failed)} 失败 ==========")
    results["passed"] = len(_passed)
    results["failed"] = len(_failed)
    results["completed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _dump(out_path, results)
    if _failed:
        print(f"失败项: {_failed}")
        return 1
    return 0


def _suites_and_cases(c, h, platform_url, aid, sk: dict) -> dict:
    """把骨架落库：create suite，case 按 interface_name 匹配 AgentInterface.id。"""
    out = {"suite_id": None, "case_ids": []}

    # 接口 name→id 映射（GET /agents/{id}/interfaces 返回含 id）
    id_map = {}
    r = c.get(f"{platform_url}/agents/{aid}/interfaces", headers=h)
    if r.status_code < 400:
        for itf in r.json()["data"]:
            id_map[itf["name"]] = itf["id"]
    if not id_map:
        out["error"] = "无法获取 agent 接口列表（name→id 映射为空）"
        print(f"✗ {out['error']}")
        return out

    suite = sk.get("suite") or {}
    r = c.post(f"{platform_url}/suites", headers=h,
               json={"agent_id": aid, "name": suite.get("name", "接入示例"),
                     "description": suite.get("description") or ""})
    if r.status_code >= 400:
        out["error"] = f"suite 落库失败 HTTP {r.status_code}: {r.text[:300]}"
        print(f"✗ {out['error']}")
        return out
    suite_id = r.json()["data"]["id"]
    out["suite_id"] = suite_id

    for case in sk.get("cases", []):
        iname = case.get("interface_name")
        iface_id = id_map.get(iname) if iname else None
        if iface_id is None:
            out["error"] = (out.get("error") or "") + \
                f"; case '{case.get('name')}' 无 interface_id（interface_name={iname}）"
            print(f"✗ case 无 interface_id: {case.get('name')}")
            continue
        r = c.post(f"{platform_url}/suites/{suite_id}/cases", headers=h, json={
            "name": case.get("name", "示例"), "interface_id": iface_id,
            "input_type": case.get("input_type", "text"), "input": case.get("input") or {},
            "expected": case.get("expected") or {}, "assertions": case.get("assertions") or [],
            "metrics": case.get("metrics") or {}, "scenes": [case["scene_tag"]]
            if case.get("scene_tag") else None,
        })
        if r.status_code >= 400:
            out["error"] = (out.get("error") or "") + \
                f"; case '{case.get('name')}' 落库失败 HTTP {r.status_code}: {r.text[:200]}"
            print(f"✗ case 落库失败: {case.get('name')}")
            continue
        out["case_ids"].append(r.json()["data"]["id"])
    return out


def _dump(out_path: str | None, results: dict) -> None:
    if not out_path:
        return
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"结果已写入: {out_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="按 manifest v2 完整对接 agent 进评测平台")
    ap.add_argument("--agent-url", required=True, help="agent 地址（含 /api/contracts）")
    ap.add_argument("--name", required=True, help="agent 注册名（平台一致）")
    ap.add_argument("--platform-url", required=True, help="平台 API 根（如 http://localhost:8180/api）")
    ap.add_argument("--admin-pwd", default="", help="平台 admin 密码（缺省读 ADMIN_PASSWORD env/../.env）")
    ap.add_argument("--base-url", default="",
                    help="平台侧访问 agent 的地址（容器场景传 host.docker.internal，默认同 --agent-url）")
    ap.add_argument("--input", default="", help="探测输入 JSON（如 '{\"content\":\"你好\"}'）")
    ap.add_argument("--secrets", default="", help="凭证 JSON（{{auth.*}} 域）")
    ap.add_argument("--out", default="", help="结果 JSON 输出路径")
    args = ap.parse_args()

    try:
        input_ = json.loads(args.input) if args.input else None
    except json.JSONDecodeError:
        print(f"✗ --input 不是合法 JSON: {args.input}")
        return
    try:
        secrets = json.loads(args.secrets) if args.secrets else None
    except json.JSONDecodeError:
        print(f"✗ --secrets 不是合法 JSON: {args.secrets}")
        return

    base_url = args.base_url or args.agent_url
    admin_pwd = args.admin_pwd or _read_secret("ADMIN_PASSWORD", "（平台 admin 登录口令）")
    code = onboard(args.agent_url, args.platform_url.rstrip("/"), admin_pwd,
                   args.name, base_url, input_, secrets, args.out or None)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
