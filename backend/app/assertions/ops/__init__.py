"""内置断言算子包（§7.1）：import 时自动注册内置算子集。"""
from app.assertions.ops import collection, retrieval, structure, text, tool
from app.assertions.registry import register

for _cls in (
    structure.FieldPresentOp,
    structure.FieldNonemptyOp,
    structure.ValueEqualsOp,
    structure.ValueRangeOp,
    text.KeywordContainsOp,
    tool.ToolCalledOp,
    tool.ToolNotCalledOp,
    retrieval.SourceHitOp,
    collection.ListContainsOp,
    collection.ListPrecisionRecallOp,
):
    register(_cls())
