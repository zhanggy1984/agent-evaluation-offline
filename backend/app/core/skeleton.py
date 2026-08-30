"""Q5 用例骨架半自动：v2 manifest（input_fields/scenes）→ suite/case 骨架（待确认，不落库）。

上限声明：expected/golden_answer/reference_docs 是业务知识，**永远人工**；自动化终点是
「骨架 input 有值 + 冒烟通过」。骨架生成是纯函数（不触 DB），供 POST /agents/{id}/skeleton
与 Q7 wizard 复用。与 Q4 联动：文件型骨架 input.file_path 需为平台 uploads 内样例文件路径。
"""
from app.core.contracts_v2 import build_adapter_config, parse_manifest_v2

# 可跑最小集（与 seed/helpers 同口径）：结构验证型断言，操作者后续按业务改
DEFAULT_ASSERTIONS = [
    {"dimension": "completeness", "op": "field_nonempty", "args": {"path": "answer"}},
]
DEFAULT_METRICS = {"completeness": {"enabled": True}}


def _unpack_fields(input_fields: list[str]) -> dict:
    """{{input.a.b}} 嵌套路径展开 → {"a": {"b": ""}}（叶节点空串待填）。"""
    out: dict = {}
    for f in sorted(input_fields):
        node = out
        parts = f.split(".")
        for p in parts[:-1]:
            child = node.setdefault(p, {})
            # 冲突路径防御：中间层被赋过空串（如同时有 `a` 与 `a.b`）→ 提升为
            # dict 并回挂父级（否则会写到游离对象，嵌套字段被静默丢弃）
            if not isinstance(child, dict):
                child = {}
                node[p] = child
            node = child
        node[parts[-1]] = ""
    return out


def _merge_input(base: dict, probe_input: dict | None) -> dict:
    """冒烟探测输入 merge 到骨架：probe 有值的键覆盖空串（半自动核心）。

    probe_input 为 None/非 dict 时原样返回 base（不动调用方对象）。
    """
    if not isinstance(probe_input, dict):
        return base

    def _m(a: dict, b: dict) -> None:
        for k, v in b.items():
            if isinstance(v, dict) and isinstance(a.get(k), dict):
                _m(a[k], v)
            else:
                a[k] = v

    _m(base, probe_input)
    return base


def _input_type(input_: dict) -> str:
    """文件型判定（平台接入标准：file_path 键声明 uploads 内样例文件），否则 text。"""
    return "file" if "file_path" in input_ else "text"


def build_case_skeleton(input_fields: list[str], probe_input: dict | None = None,
                        interface_name: str | None = None,
                        scene_tag: str | None = None) -> dict:
    """单个 case 骨架：input 有值（probe 优先 + 空串补全），expected 留空待填。

    断言/metrics 给可跑最小集（结构验证）；name 建议 scene-interface 便于识别。
    """
    input_ = _merge_input(_unpack_fields(input_fields), probe_input)
    name = scene_tag or interface_name or "示例"
    if scene_tag and interface_name:
        name = f"{scene_tag}-{interface_name}"
    return {
        "name": name,
        "interface_name": interface_name,  # Q7：落库时按名匹配 AgentInterface.id
        "scene_tag": scene_tag,            # Q7：落库时 CaseCreate.scenes 打标
        "input_type": _input_type(input_),
        "input": input_,
        "expected": {},
        "assertions": [dict(a) for a in DEFAULT_ASSERTIONS],
        "metrics": {k: dict(v) for k, v in DEFAULT_METRICS.items()},
    }


def build_suite_skeleton(payload: dict, agent_name: str | None = None,
                         probe_input: dict | None = None) -> tuple[dict | None, list[str]]:
    """v2 manifest → suite 骨架：每 scene × 每 llm 接口一个 case。

    无 contract 段 / build 硬错误 → (None, errors)。不落库（骨架=待确认产物），
    agent_id 由接口层补（纯函数不触 DB）。
    """
    manifest, errs = parse_manifest_v2(payload)
    if manifest is None:
        return None, errs
    draft, errs = build_adapter_config(payload)
    if draft is None:
        return None, errs
    llm_ifaces = [i for i in manifest.interfaces if i.llm]
    scenes = [s.tag for s in manifest.scenes] or [None]
    cases = [
        build_case_skeleton(draft.input_fields, probe_input,
                            interface_name=iface.name, scene_tag=scene)
        for scene in scenes for iface in llm_ifaces
    ]
    return {
        "agent_name": agent_name,
        "suite": {
            "name": f"{agent_name or 'agent'} 接入示例",
            "description": "Q5 自动生成的示例骨架：input 已预填，expected/golden_answer/reference_docs 需人工补全",
        },
        "interfaces": [{"name": i.name, "path": i.path, "contract_type": i.contract_type}
                       for i in llm_ifaces],
        "cases": cases,
    }, []
