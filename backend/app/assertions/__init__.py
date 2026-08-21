"""结构断言算子模块（§7.1 / 3.1）：内置算子 + 注册表 + 执行入口。"""
from app.assertions import ops  # noqa: F401  触发内置算子注册
from app.assertions.run import run_assertions

__all__ = ["run_assertions"]
