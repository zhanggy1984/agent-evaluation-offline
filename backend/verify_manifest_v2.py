"""manifest v2 最小验证：评估「URL 即接入」阶段 3 的可行性（2026-08-30 方案验证）。

验证三个问题：
1. 充分性：v2 contract 段能否表达 seed_data.py 中 4 家现有 adapter_config
   （prepare 前置链路 / poll 轮询 / files 上传 / sse field_map / timeout / 模板引用）。
2. 等价性：build_adapter_config(v2) 生成的 dict 是否 == seed_data 对应 ADAPTER_CFG。
3. 向后兼容：现有 v1 parse_manifest 对 v1 与 v2 payload 均不回归
   （pydantic 默认 extra="ignore"，v2 的 contract 段对 v1 解析透明）。

1-3 全过 → 阶段 3「discover 拉 v2 manifest → 自动生成 adapter_config 草案」
可行性成立，才值得在 app/core 落地正式解析器。

v1 manifest 内联自 4 家 agent 仓库 contracts.py（D:\\study\\aiprojcet\\<agent>\\backend\\app\\api\\contracts.py）；
v2 contract 段由 seed_data 对应 ADAPTER_CFG 反写（占位符 {{input.*}}/{{auth.*}}/{{prepare.*}}）。
"""
import re
import sys
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.core.contracts import parse_manifest
from app.seed_data import (
    CC_ADAPTER_CFG, CS_ADAPTER_CFG, GQ_ADAPTER_CFG, GQ_LIBRARY_ID,
    SP_ADAPTER_CFG,
)

# ================= 一、v2 manifest 提案模型（脚本内临时定义，未入 app/core） =================

class V2Prepare(BaseModel):
    """前置步骤：登录取 token / 建会话 / 上传 / 轮询。poll 为完整 dict（until/interval/timeout）。
    path 可为空（纯 poll 步骤如 cc wait_done，path 在 poll 段内）。"""
    name: str
    method: str = "POST"
    path: str | None = None
    headers: dict | None = None
    body: dict | None = None
    files: dict | None = None
    poll: dict | None = None
    extract: dict | None = None


class V2Request(BaseModel):
    """评测主请求：路径/方法/头/体，占位符引用 prepare 产物与用例输入。"""
    path: str
    method: str = "POST"
    headers: dict | None = None
    body: dict | None = None


class V2Contract(BaseModel):
    """agent 声明「平台该怎么驱动我」——即 adapter_config 的收编。"""
    type: Literal["sse", "sync"]
    timeout: int = 120
    prepare: list[V2Prepare] = Field(default_factory=list)
    request: V2Request
    sse: dict | None = None


class V2Manifest(BaseModel):
    """v2 = v1（interfaces/scenes 全保留）+ contract 段。interfaces/scenes 用 dict 复用 v1 解析。"""
    agent: str = Field(min_length=1, max_length=128)
    contract_version: str | None = Field(default=None, max_length=32)
    interfaces: list[dict]
    scenes: list[dict] = Field(default_factory=list)
    contract: V2Contract


# ================= 二、占位符转换：{{...}} → 现有 adapter 的 {...} 语法 =================

_TMPL_RE = re.compile(r"\{\{\s*([A-Za-z_][\w.]*)\s*\}\}")


def _conv(s: str) -> str:
    """{{input.x}}→{case.input.x}；{{auth.x}}→{auth.x}；{{prepare.name.f}}→{prepare.name.f}。"""

    def repl(m: re.Match) -> str:
        inner = m.group(1)
        if inner.startswith("input."):
            return "{" + "case.input." + inner[len("input."):] + "}"
        if inner.startswith("auth.") or inner.startswith("prepare."):
            return "{" + inner + "}"
        raise ValueError(f"未知占位符域: {inner}")

    return _TMPL_RE.sub(repl, s)


def _render(v):
    """递归转换 dict/list/str 中的模板，非字符串（int/bool）原样。"""
    if isinstance(v, str):
        return _conv(v)
    if isinstance(v, dict):
        return {k: _render(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_render(x) for x in v]
    return v


# ================= 三、v2 → adapter_config 生成器（提案实现） =================

def build_adapter_config(payload: dict) -> tuple[dict | None, list[str]]:
    try:
        m = V2Manifest.model_validate(payload)
    except ValidationError as exc:
        return None, [f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()]

    c = m.contract
    cfg: dict = {"contract_type": c.type, "timeout": c.timeout}

    if c.prepare:
        cfg["prepare"] = []
        for p in c.prepare:
            # 与 seed_data 现状完全一致：常规步骤输出 method+path；纯 poll 步骤（如 cc wait_done）
            # 仅 name+poll（seed_data 中无 method/path，跟随现状）
            step: dict = {"name": p.name}
            if p.path is not None:
                step["method"] = p.method
                step["path"] = _conv(p.path)
            for k in ("headers", "body", "files", "poll", "extract"):
                v = getattr(p, k)
                if v is not None:
                    step[k] = _render(v)
            cfg["prepare"].append(step)

    req: dict = {"path": _conv(c.request.path), "method": c.request.method}
    for k in ("headers", "body"):
        v = getattr(c.request, k)
        if v is not None:
            req[k] = _render(v)
    cfg["request"] = req

    if c.sse:
        cfg["sse"] = _render(c.sse)

    return cfg, []


# ================= 四、验证输入：4 家真实 v1 manifest + 手写 v2 contract 段 =================

V1_MANIFESTS = {
    "customer-service": {
        "agent": "customer-service", "contract_version": "1.0",
        "interfaces": [
            {"name": "chat", "path": "/api/v1/sessions/{sid}/messages", "method": "POST",
             "contract_type": "sse", "llm": True, "description": "客服会话对话"},
            {"name": "login", "path": "/api/v1/auth/login", "method": "POST",
             "llm": False, "description": "会话鉴权"},
        ],
        "scenes": [
            {"tag": "greeting", "description": "问候与闲聊"},
            {"tag": "order_query", "description": "订单查询"},
            {"tag": "after_sales", "description": "售后服务"},
            {"tag": "human_handoff", "description": "转人工客服"},
        ],
    },
    "contract-check": {
        "agent": "contract-check", "contract_version": "1.0",
        "interfaces": [
            {"name": "result", "path": "/api/tasks/{task_id}/result", "method": "GET",
             "contract_type": "sync", "llm": True, "description": "合同校验结果"},
            {"name": "upload", "path": "/api/files/upload", "method": "POST",
             "llm": False, "description": "上传合同文件"},
        ],
        "scenes": [
            {"tag": "missing_date", "description": "缺失生效日期"},
            {"tag": "single_party", "description": "单方签署"},
            {"tag": "scanned_pdf", "description": "扫描件识别"},
            {"tag": "conflict", "description": "条款冲突"},
            {"tag": "genuine", "description": "合规合同"},
        ],
    },
    "smart-procurement": {
        "agent": "smart-procurement", "contract_version": "1.0",
        "interfaces": [
            {"name": "chat", "path": "/api/v1/reviews/{review_id}/chat", "method": "POST",
             "contract_type": "sse", "llm": True, "description": "评审对话"},
            {"name": "score", "path": "/api/v1/reviews/{review_id}/score", "method": "POST",
             "contract_type": "sse", "llm": True, "description": "AI 评分"},
            {"name": "login", "path": "/api/v1/auth/login", "method": "POST",
             "llm": False, "description": "专家/管理员鉴权"},
        ],
        "scenes": [
            {"tag": "tech_scheme", "description": "技术方案评审"},
            {"tag": "price", "description": "报价评审"},
            {"tag": "conflict_interest", "description": "利益冲突检测"},
            {"tag": "collusion", "description": "围串标检测"},
        ],
    },
    "good-question": {
        "agent": "good-question", "contract_version": "1.0",
        "interfaces": [
            {"name": "chat", "path": "/api/chat/{session_id}", "method": "POST",
             "contract_type": "sse", "llm": True, "description": "知识问答"},
            {"name": "login", "path": "/api/auth/login", "method": "POST",
             "llm": False, "description": "会话鉴权"},
        ],
        "scenes": [
            {"tag": "greeting", "description": "问候与闲聊"},
            {"tag": "doc_qa", "description": "文档检索问答"},
            {"tag": "no_hit", "description": "无命中兜底"},
            {"tag": "summarize", "description": "文档内容总结"},
        ],
    },
}

# 从 seed_data ADAPTER_CFG 反写：{{input.*}}/{{auth.*}}/{{prepare.*}} 取代 {case.input.*}/{auth.*}/{prepare.*}
V2_CONTRACTS = {
    "customer-service": {
        "type": "sse", "timeout": 120,
        "prepare": [
            {"name": "login", "method": "POST", "path": "/api/v1/auth/login",
             "body": {"username": "{{auth.username}}", "password": "{{auth.password}}"},
             "extract": {"token": "access_token"}},
            {"name": "session", "method": "POST", "path": "/api/v1/sessions",
             "headers": {"Authorization": "Bearer {{prepare.login.token}}"},
             "extract": {"id": "session_id"}},
        ],
        "request": {
            "path": "/api/v1/sessions/{{prepare.session.id}}/messages", "method": "POST",
            "headers": {"Authorization": "Bearer {{prepare.login.token}}",
                        "Content-Type": "application/json"},
            "body": {"content": "{{input.content}}"},
        },
    },
    "contract-check": {
        "type": "sync", "timeout": 300,
        "prepare": [
            {"name": "upload", "method": "POST", "path": "/api/files/upload",
             "files": {"file": "{{input.file_path}}"},
             "extract": {"task_id": "task_id"}},
            {"name": "wait_done", "poll": {
                "path": "/api/tasks/{{prepare.upload.task_id}}",
                "until": {"status": ["WAITING_REVIEW", "SUCCESS", "FAILED", "CANCELLED"]},
                "interval": 2, "timeout": 300}},
        ],
        "request": {"path": "/api/tasks/{{prepare.upload.task_id}}/result", "method": "GET"},
    },
    "smart-procurement": {
        "type": "sse", "timeout": 120,
        "prepare": [
            {"name": "login", "method": "POST", "path": "/api/v1/auth/login",
             "body": {"username": "{{auth.username}}", "password": "{{auth.password}}"},
             "extract": {"token": "access_token"}},
            {"name": "review", "method": "POST", "path": "/api/v1/reviews",
             "headers": {"Authorization": "Bearer {{prepare.login.token}}",
                         "Content-Type": "application/json"},
             "body": {"bid_id": "{{input.bid_id}}", "dimension_id": "{{input.dimension_id}}"},
             "extract": {"review_id": "review_id"}},
        ],
        "request": {
            "path": "/api/v1/reviews/{{prepare.review.review_id}}/chat", "method": "POST",
            "headers": {"Authorization": "Bearer {{prepare.login.token}}",
                        "Content-Type": "application/json"},
            "body": {"question": "{{input.question}}"},
        },
    },
    "good-question": {
        "type": "sse", "timeout": 180,
        "prepare": [
            {"name": "login", "method": "POST", "path": "/api/auth/login",
             "body": {"username": "{{auth.username}}", "password": "{{auth.password}}"},
             "extract": {"token": "access_token"}},
            {"name": "session", "method": "POST", "path": "/api/sessions",
             "headers": {"Authorization": "Bearer {{prepare.login.token}}"},
             "body": {"library_id": GQ_LIBRARY_ID},
             "extract": {"id": "id"}},
        ],
        "request": {
            "path": "/api/chat/{{prepare.session.id}}", "method": "POST",
            "headers": {"Authorization": "Bearer {{prepare.login.token}}",
                        "Content-Type": "application/json"},
            "body": {"content": "{{input.content}}", "stream": True},
        },
        "sse": {"field_map": {"token": "answer"}},
    },
}

EXPECTED = {
    "customer-service": CS_ADAPTER_CFG,
    "contract-check": CC_ADAPTER_CFG,
    "smart-procurement": SP_ADAPTER_CFG,
    "good-question": GQ_ADAPTER_CFG,
}


# ================= 五、验证执行 =================

def main() -> int:
    ok = True

    print("── 1/2 向后兼容：现有 v1 parse_manifest 是否接受 v1 与 v2 payload ──")
    for name, m1 in V1_MANIFESTS.items():
        r1 = parse_manifest(m1)
        if r1[0] is None:
            print(f"  ❌ {name}: v1 解析失败 → {r1[1]}"); ok = False; continue
        r2 = parse_manifest({**m1, "contract": V2_CONTRACTS[name]})
        if r2[0] is None:
            print(f"  ❌ {name}: v2 payload 被现有 parse_manifest 拒绝 → {r2[1]}"); ok = False; continue
        print(f"  ✅ {name}: v1 与 v2 payload 均接受（v2.contract 对 v1 透明）")

    print("── 2/2 充分性 + 等价性：build_adapter_config(v2) == seed_data ADAPTER_CFG ──")
    for name, expected in EXPECTED.items():
        payload = {**V1_MANIFESTS[name], "contract": V2_CONTRACTS[name]}
        cfg, errs = build_adapter_config(payload)
        if cfg is None:
            print(f"  ❌ {name}: v2 解析失败 → {errs}"); ok = False; continue
        if cfg == expected:
            print(f"  ✅ {name}: 完全等价（{len(json_dumps(cfg))} 字符）")
        else:
            print(f"  ❌ {name}: 不等价"); ok = False
            import difflib
            a = json_dumps(expected).splitlines(); b = json_dumps(cfg).splitlines()
            for line in difflib.unified_diff(a, b, "seed_data", "v2 生成", lineterm=""):
                print(f"      {line}")

    print()
    if ok:
        print("结论：充分性 ✅ / 等价性 ✅ / 向后兼容 ✅ —— 阶段 3「v2 自动生成 adapter_config」可行性成立")
    else:
        print("结论：存在失败项，需修正后重验")
    return 0 if ok else 1


def json_dumps(v) -> str:
    import json
    return json.dumps(v, ensure_ascii=False, indent=1, sort_keys=True)


if __name__ == "__main__":
    raise SystemExit(main())
