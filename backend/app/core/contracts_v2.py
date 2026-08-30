"""manifest v2：解析 + adapter_config 生成 + 占位符引用校验（快捷接入 Q1 转正）。

v2 = v1（interfaces/scenes 全保留）+ contract 段：agent 声明「平台该怎么驱动我」
（prepare 前置链路 / request 主请求 / sse field_map / timeout）。build_adapter_config
将其生成为现有 ConfigEngine 的 adapter_config 格式，并输出元信息：

- input_fields：{{input.*}} 去重域（Q5 用例骨架的输入模板；嵌套路径收全路径，如 {{input.a.b}} → "a.b"）
- requires_auth：是否引用 {{auth.*}}（Q7 wizard 提示配凭证）
- warnings：不阻塞的软提示（如 extract 声明但从未被引用）

占位符域（{{...}}）：
- {{input.*}}    → {case.input.*}        用例输入
- {{auth.*}}     → {auth.*}              平台凭证（注册 agent 时配置）
- {{prepare.X.f}}→ {prepare.X.f}         前置步骤产物，X 必须存在且 f 在该步骤 extract 声明的 key 内

硬错误（拒绝生成，防坏 adapter 入库）：引用不存在的 prepare 步骤 / extract 未声明字段 /
未知占位符域 / 无 llm=true 接口 / schema 非法。软警告（extract 未引用）不阻塞。
v1 完全不动（parse_manifest 对 v2 payload 因 pydantic 默认 extra=ignore 透明）。
"""
import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from pydantic_core import ValidationError

from app.core.contracts import ContractInterface, ContractScene

# ---------------- v2 模型 ----------------

class V2Prepare(BaseModel):
    """前置步骤：登录取 token / 建会话 / 上传 / 轮询。path 可为空（纯 poll 步骤如 cc wait_done）。"""
    name: str = Field(min_length=1, max_length=128)
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

    @model_validator(mode="after")
    def _check_prepare_unique(self) -> "V2Contract":
        names = [p.name for p in self.prepare]
        dups = sorted({n for n in names if names.count(n) > 1})
        if dups:
            raise ValueError(f"prepare 步骤名重复: {dups}")
        return self


class V2Manifest(BaseModel):
    """v2 = v1（interfaces/scenes 全保留，复用 v1 模型校验）+ contract 段。"""
    agent: str = Field(min_length=1, max_length=128)
    contract_version: str | None = Field(default=None, max_length=32)
    interfaces: list[ContractInterface] = Field(default_factory=list)
    scenes: list[ContractScene] = Field(default_factory=list)
    contract: V2Contract

    @model_validator(mode="after")
    def _has_llm_interface(self) -> "V2Manifest":
        if not any(i.llm for i in self.interfaces):
            raise ValueError("无 llm=true 评测接口，无法生成 adapter")
        return self


# ---------------- 占位符转换：{{...}} → 现有 adapter 的 {...} 语法 ----------------

_TMPL_RE = re.compile(r"\{\{\s*([A-Za-z_][\w.]*)\s*\}\}")


def _conv(s: str) -> str:
    """{{input.x}}→{case.input.x}；{{auth.x}}→{auth.x}；{{prepare.name.f}}→{prepare.name.f}。"""
    def repl(m: re.Match) -> str:
        inner = m.group(1)
        if inner.startswith("input."):
            return "{" + "case.input." + inner[len("input."):] + "}"
        if inner.startswith("auth.") or inner.startswith("prepare."):
            return "{" + inner + "}"
        raise ValueError(f"未知占位符域: {inner}")  # 不应触发：_validate_refs 已先拦截
    return _TMPL_RE.sub(repl, s)


def _render(v):
    """递归转换 dict/list/str 中的模板；非字符串（int/bool/None）原样。"""
    if isinstance(v, str):
        return _conv(v)
    if isinstance(v, dict):
        return {k: _render(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_render(x) for x in v]
    return v


# ---------------- 占位符引用收集与校验 ----------------

def _collect_refs(c: V2Contract) -> dict[str, set]:
    """递归收集 request + 全部 prepare + sse 中的 {{...}} 引用 → {引用: {出现处文本}}。

    必须覆盖 sse：build_adapter_config 对 sse 同样走 _render 渲染，漏扫会让坏占位符
    绕过校验（未知域 → _conv 抛未捕获 ValueError；引用缺失 prepare → 静默生成坏 adapter）。
    """
    refs: dict[str, set] = {}

    def _scan(v):
        if isinstance(v, str):
            for m in _TMPL_RE.finditer(v):
                refs.setdefault(m.group(1), set()).add(v)
        elif isinstance(v, dict):
            for x in v.values():
                _scan(x)
        elif isinstance(v, list):
            for x in v:
                _scan(x)

    _scan(c.request.model_dump())
    for p in c.prepare:
        _scan(p.model_dump(exclude_none=True))
    if c.sse:
        _scan(c.sse)
    return refs


def _validate_refs(c: V2Contract, refs: dict[str, set]) -> tuple[list[str], list[str]]:
    """占位符引用校验 → (errors, warnings)。errors 非空即拒绝生成。

    extract 校验语义：引用 f 是 extract 的「声明 key」（如 {"token": "access_token"} 里
    引用 {{prepare.login.token}}），不是响应里的点号路径（access_token）。
    """
    errors: list[str] = []
    prepare_by_name = {p.name: p for p in c.prepare}
    for ref in sorted(refs):
        if ref.startswith("prepare."):
            parts = ref.split(".")
            if len(parts) < 3:
                errors.append(f"{ref}: prepare 引用必须形如 {{prepare.步骤.字段}}，缺少字段名")
                continue
            if parts[1] not in prepare_by_name:
                errors.append(f"引用不存在的 prepare 步骤: {ref}")
                continue
            step = prepare_by_name[parts[1]]
            if parts[2] not in (step.extract or {}):
                # 注意：纯 poll 步骤（cc wait_done）无 extract，引用其产物字段同样报错——
                # poll 步骤不产出可引用字段，统一按未声明处理
                errors.append(f"{ref}: prepare.{parts[1]} 的 extract 未声明字段 {parts[2]}"
                              f"（extract 声明 key 为 {sorted(step.extract) if step.extract else []}，非响应路径）")
        elif ref.startswith("input.") or ref.startswith("auth."):
            continue  # 合法域
        else:
            errors.append(f"未知占位符域: {ref}")

    # 软警告：extract 声明但从未被引用（不影响执行，agent 自查用）
    referenced_steps = {r.split(".")[1] for r in refs
                        if r.startswith("prepare.") and len(r.split(".")) >= 2}
    warnings = [
        f"prepare.{p.name}.extract 声明但未被任何引用使用"
        for p in c.prepare if p.extract and p.name not in referenced_steps
    ]
    return errors, warnings


# ---------------- 生成器 ----------------

@dataclass
class AdapterDraft:
    """v2 → adapter_config 生成的产物（含元信息，供 Q5/Q7）。"""
    adapter_config: dict
    input_fields: list[str]           # {{input.*}} 去重域（嵌套收全路径）
    requires_auth: bool               # 是否引用 {{auth.*}}
    warnings: list[str] = field(default_factory=list)


def parse_manifest_v2(payload: dict) -> tuple[V2Manifest | None, list[str]]:
    """解析 v2 manifest。成功 (manifest, [])；失败 (None, errors) 逐条可读。"""
    try:
        manifest = V2Manifest.model_validate(payload)
    except ValidationError as exc:
        return None, [f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()]
    return manifest, []


def build_adapter_config(payload: dict) -> tuple[AdapterDraft | None, list[str]]:
    """v2 manifest → AdapterDraft。硬错误（schema/引用/无 llm 接口）→ (None, errors)。"""
    manifest, errs = parse_manifest_v2(payload)
    if manifest is None:
        return None, errs
    c = manifest.contract
    refs = _collect_refs(c)
    errors, warnings = _validate_refs(c, refs)
    if errors:
        return None, errors

    cfg: dict = {"contract_type": c.type, "timeout": c.timeout}
    if c.prepare:
        cfg["prepare"] = []
        for p in c.prepare:
            # 常规步骤输出 method+path；纯 poll 步骤（path 空）仅 name+poll（与 seed_data 现状一致）
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

    input_fields = sorted({r[len("input."):] for r in refs if r.startswith("input.")})
    requires_auth = any(r.startswith("auth.") for r in refs)
    return AdapterDraft(adapter_config=cfg, input_fields=input_fields,
                        requires_auth=requires_auth, warnings=warnings), []
