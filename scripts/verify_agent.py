"""Q6 通用接入自测工具：任意 agent 的 v2 manifest + 标准契约达标校验。

平台定标准，agent 遵守适配——本工具是 agent 侧的自测入口，判定口径与平台
probe/build_adapter_config 同源（直接 import 平台代码），不依赖平台在线即可先跑静态校验。

用法：
  # 静态校验（零依赖，agent 侧开发期先跑这个；硬错误退出码非 0）
  python scripts/verify_agent.py --agent-url http://127.0.0.1:9000

  # 平台冒烟（契约真达标，需平台在线；复用平台 POST /agents/{id}/probe）
  python scripts/verify_agent.py --agent-url http://host.docker.internal:9000 \
      --platform-url http://localhost:8180/api --input '{"content": "你好"}' \
      --secrets '{"username": "svc", "password": "xxx"}'

静态校验：GET /api/contracts → parse_manifest_v2 + build_adapter_config（Q1 硬错误闸门）
→ 报告硬错误/软警告/input_fields/requires_auth。
平台冒烟：登录 → 注册 agent（adapter 由服务端权威生成）→ sync llm 接口 → 配置凭证 →
POST /probe → 按 contract.type 断言契约字段达标。
httpx 一律 trust_env=False（Windows 系统代理 memory）。
"""
import argparse
import json
import os
import sys
import time

import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 复用平台解析/派生代码（口径与平台完全一致）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
from app.core.contracts_v2 import build_adapter_config, parse_manifest_v2  # noqa: E402

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


# ---------------- 静态校验（零依赖） ----------------
def static_check(agent_url: str) -> int:
    print(f"── 静态校验: {agent_url}/api/contracts ──")
    payload, err = _fetch_manifest(agent_url)
    if payload is None:
        print(f"✗ {err}")
        return 1
    manifest, errs = parse_manifest_v2(payload)
    if manifest is None:
        print("✗ manifest 解析失败:")
        for e in errs:
            print(f"   - {e}")
        return 1
    draft, errs = build_adapter_config(payload)
    if draft is None:
        print("✗ adapter 派生失败（Q1 硬错误闸门）:")
        for e in errs:
            print(f"   - {e}")
        return 1
    print("✅ manifest 解析 + adapter 派生通过")
    print(f"   agent={manifest.agent} contract_version={manifest.contract_version}")
    print(f"   接口: {', '.join(i.name + '(' + (i.contract_type or 'aux') + ')' for i in manifest.interfaces)}")
    print(f"   llm 评测接口: {[i.name for i in manifest.interfaces if i.llm]}")
    print(f"   场景: {[s.tag for s in manifest.scenes]}")
    print(f"   input_fields: {draft.input_fields}")
    print(f"   requires_auth: {draft.requires_auth}")
    for w in draft.warnings:
        print(f"   软警告: {w}")
    return 0


# ---------------- 平台冒烟（复用平台 probe） ----------------
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


def platform_smoke(agent_url: str, platform_url: str, admin_pwd: str,
                   name: str, input_: dict | None, secrets: dict | None,
                   deploy_url: str | None) -> int:
    print(f"── 平台冒烟: {platform_url}（agent={name}）──")
    payload, err = _fetch_manifest(agent_url)
    if payload is None:
        print(f"✗ {err}")
        return 1
    base_url = deploy_url or agent_url

    with httpx.Client(timeout=httpx.Timeout(180.0, connect=10.0), trust_env=False) as c:
        h = _auth(c, platform_url, admin_pwd)
        print("✓ 平台登录成功")

        aid = _get_or_create_agent(c, h, platform_url, name, base_url)
        print(f"✓ agent id={aid}")

        # adapter 由服务端权威生成（Q2 确认端点，含 Q1 硬错误闸门兜底）
        r = c.post(f"{platform_url}/agents/{aid}/adapter", headers=h,
                   json={"manifest": payload})
        if r.status_code >= 400:
            print(f"✗ adapter 确认失败 HTTP {r.status_code}: {r.text[:300]}")
            return 1
        print("✓ adapter 落库（服务端权威生成）")

        # 只 sync llm 评测接口（辅助接口由 manifest prepare 驱动，非独立评测目标）
        ifaces = [{"name": i["name"], "path": i["path"], "method": i["method"],
                   "contract_type": i["contract_type"]}
                  for i in payload["interfaces"] if i.get("llm")]
        if not ifaces:
            print("✗ manifest 无 llm=true 评测接口")
            return 1
        r = c.post(f"{platform_url}/agents/{aid}/interfaces/sync", headers=h,
                   json={"interfaces": ifaces})
        if r.status_code >= 400:
            print(f"✗ 接口 sync 失败 HTTP {r.status_code}: {r.text[:300]}")
            return 1
        print(f"✓ 评测接口 sync: {[i['name'] for i in ifaces]}")

        if secrets:
            r = c.put(f"{platform_url}/agents/{aid}/auth", headers=h,
                      json={"secrets": secrets})
            if r.status_code >= 400:
                print(f"✗ 凭证配置失败 HTTP {r.status_code}: {r.text[:300]}")
                return 1
            print("✓ 凭证已配置（{{auth.*}} 域）")

        r = c.post(f"{platform_url}/agents/{aid}/probe", headers=h,
                   json={"input": input_} if input_ else {})
        if r.status_code >= 400:
            print(f"✗ 冒烟探测失败 HTTP {r.status_code}: {r.text[:300]}")
            return 1
        data = r.json()["data"]

    ctype = payload["contract"]["type"]
    if ctype == "sse":
        _assert_sse(data)
    else:
        _assert_sync(data)
    print(f"\n========== 契约探测: {len(_passed)} 通过 / {len(_failed)} 失败 ==========")
    if _failed:
        print(f"失败项: {_failed}")
        return 1
    return 0


def _assert_sse(data: dict) -> None:
    """SSE §5.1 契约断言（口径与 verify_probe_real 一致）：usage/done 必选。"""
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


def main() -> None:
    ap = argparse.ArgumentParser(description="任意 agent 接入自测（manifest v2 + 标准契约）")
    ap.add_argument("--agent-url", required=True, help="agent 地址（含 /api/contracts）")
    ap.add_argument("--platform-url", default="",
                    help="平台 API 根（如 http://localhost:8180/api），提供则做平台冒烟")
    ap.add_argument("--admin-pwd", default="Eval#Admin2026", help="平台 admin 密码")
    ap.add_argument("--name", default="", help="agent 注册名（默认自动生成防撞）")
    ap.add_argument("--input", default="", help="显式探测输入 JSON（如 '{\"content\":\"你好\"}'）")
    ap.add_argument("--secrets", default="", help="凭证 JSON（{{auth.*}} 域）")
    ap.add_argument("--deploy-url", default="",
                    help="平台侧访问 agent 的地址（容器场景传 host.docker.internal，默认同 --agent-url）")
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

    if not args.platform_url:
        code = static_check(args.agent_url)
    else:
        name = args.name or f"verify-{int(time.time())}"
        code = platform_smoke(args.agent_url, args.platform_url.rstrip("/"),
                              args.admin_pwd, name, input_, secrets,
                              args.deploy_url or None)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
