"""断言算子注册表：内置算子注册 + 自定义算子白名单加载（安全基线 §九-2）。

白名单：class_path 必须 ∈ ALLOWED_CLASS_PATHS（constants 内 frozenset，禁止运行时
增删），DB 只存指向该集合的索引，加载统一走 importlib + getattr。
"""
from __future__ import annotations

import importlib

from app.assertions.base import AssertionOp, AssertionOpError
from app.core.constants import ALLOWED_CLASS_PATHS

_registry: dict[str, AssertionOp] = {}


def register(op: AssertionOp) -> None:
    """注册算子实例（内置 seed 与自定义加载时调用；同 op 后注册覆盖）。"""
    if not op.op:
        raise AssertionOpError("算子 op 标识为空")
    _registry[op.op] = op


def get_op(op_name: str) -> AssertionOp:
    """按 op 名取算子；未注册抛 AssertionOpError。"""
    try:
        return _registry[op_name]
    except KeyError:
        raise AssertionOpError(f"断言算子未注册: {op_name}") from None


def list_ops() -> list[str]:
    return sorted(_registry)


def load_class(class_path: str) -> AssertionOp:
    """白名单校验 + importlib 加载并实例化自定义算子（不自动注册）。"""
    if class_path not in ALLOWED_CLASS_PATHS:
        raise AssertionOpError(f"class_path 不在白名单: {class_path}")
    full = f"app.{class_path}"  # class_path 相对 app 包（constants 约定）
    module_name, _, cls_name = full.rpartition(".")
    module = importlib.import_module(module_name)
    cls = getattr(module, cls_name)
    if not (isinstance(cls, type) and issubclass(cls, AssertionOp)):
        raise AssertionOpError(f"class_path 非 AssertionOp 子类: {class_path}")
    return cls()


async def load_from_db(db) -> int:
    """从 assertion_op_def 表加载全部已登记算子（内置重复注册无害）。

    startup 时调用一次；返回加载数。新增自定义算子后需重启或动态调用本函数。
    """
    from sqlalchemy import select

    from app.models import AssertionOpDef

    rows = (await db.execute(select(AssertionOpDef))).scalars().all()
    for r in rows:
        register(load_class(r.class_path))
    return len(rows)
