"""标准契约清单解析与差异对比（4.3 初始化脚手架）。

协议（平台定标准，agent 适配）：agent 侧实现统一 `GET /api/contracts`（公开无鉴权），
返回接口清单 + 场景声明。平台脚手架读取该端点 → parse_manifest 强校验 →
diff_manifest 与现有 agent_interface 比对，人工确认后落库。

本模块只含纯函数与 Pydantic 模型，无 IO，便于单测。

契约字段：
- 顶层 {agent, contract_version, interfaces[], scenes[]}
- interfaces[]: {name, path, method, contract_type(sse|sync), llm(bool,默认true), description}
  - llm=true 必须给 contract_type（有 LLM 参与才评测）；llm=false 是辅助接口
    （login/upload/poll 等），只登记不进 agent_interface
  - path 必须以 / 开头，且须与 adapter_config request.path 完全一致（判重键）
  - 禁重复 (method.upper(), path)
- scenes[]: {tag(≤64), description(≤256)}，对齐 scene_catalog 列宽
"""
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from pydantic_core import ValidationError


class ContractInterface(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    path: str = Field(min_length=1, max_length=512)
    method: str = Field(default="POST", max_length=8)
    contract_type: Literal["sse", "sync"] | None = None
    llm: bool = True
    description: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def _check_llm_contract(self) -> "ContractInterface":
        if self.llm and self.contract_type is None:
            raise ValueError("llm=true 必须提供 contract_type")
        if not self.path.startswith("/"):
            raise ValueError(f"path 必须以 / 开头: {self.path}")
        return self


class ContractScene(BaseModel):
    tag: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=256)


class ContractManifest(BaseModel):
    agent: str = Field(min_length=1, max_length=128)
    contract_version: str | None = Field(default=None, max_length=32)
    interfaces: list[ContractInterface] = Field(default_factory=list)
    scenes: list[ContractScene] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_dup_keys(self) -> "ContractManifest":
        seen = set()
        for i in self.interfaces:
            key = (i.method.upper(), i.path)
            if key in seen:
                raise ValueError(f"interfaces 存在重复 (method,path): {key}")
            seen.add(key)
        return self


def parse_manifest(payload: dict) -> tuple[ContractManifest | None, list[str]]:
    """解析标准契约响应。成功返回 (manifest, [])；失败返回 (None, errors)。

    errors 为非空列表，逐条可读（供 discover 返回给人工定位）。
    """
    try:
        manifest = ContractManifest.model_validate(payload)
    except ValidationError as exc:
        errors = []
        for err in exc.errors():
            loc = ".".join(str(x) for x in err["loc"])
            errors.append(f"{loc}: {err['msg']}")
        return None, errors
    return manifest, []


def _iface_key(method: str, path: str) -> tuple[str, str]:
    return (method.upper(), path)


def diff_manifest(manifest: ContractManifest, existing: list[dict]) -> dict:
    """对比标准契约与现有 agent_interface（匹配键 = (method.upper(), path)）。

    existing: 现有接口列表，每项 dict 含 name/path/method/contract_type/enabled。

    返回四类：
    - added：manifest 有、库没有的评测接口（llm=true，待人工确认后补录）
    - existing：manifest 与库都有的接口；changed 标出差异字段（空=一致）
    - missing：库中 enabled 但 manifest 未声明的接口（疑似下线，提示人工核对）
    - auxiliary：manifest 中 llm=false 的辅助接口（不建 agent_interface）
    """
    existing_by_key = {_iface_key(e["method"], e["path"]): e for e in existing}

    def _base(i: ContractInterface) -> dict:
        return {"name": i.name, "path": i.path, "method": i.method,
                "contract_type": i.contract_type}

    added, existing_rows, auxiliary = [], [], []
    for i in manifest.interfaces:
        if not i.llm:
            auxiliary.append(_base(i))
            continue
        row = existing_by_key.get(_iface_key(i.method, i.path))
        if row is None:
            added.append({**_base(i), "changed": []})
            continue
        changed = []
        if row.get("name") != i.name:
            changed.append("name")
        if (row.get("method") or "").upper() != i.method.upper():
            changed.append("method")
        if row.get("contract_type") != i.contract_type:
            changed.append("contract_type")
        existing_rows.append({**_base(i), "changed": changed})

    missing = [
        {"name": e["name"], "path": e["path"], "method": e["method"],
         "contract_type": e["contract_type"]}
        for e in existing
        if e.get("enabled", True)
        and _iface_key(e["method"], e["path"]) not in
        {_iface_key(i.method, i.path) for i in manifest.interfaces}
    ]
    return {"added": added, "existing": existing_rows,
            "missing": missing, "auxiliary": auxiliary}


def unique_name(base_name: str, taken: set[str]) -> str:
    """同 agent 内重名时自动追加 _2/_3...，返回不冲突的新名（sync 落库用）。"""
    if base_name not in taken:
        return base_name
    i = 2
    while f"{base_name}_{i}" in taken:
        i += 1
    return f"{base_name}_{i}"
