"""统一响应体 {code, message, data} 与分页结构。"""
from typing import Any


def ok(data: Any = None, message: str = "ok", code: int = 0) -> dict:
    return {"code": code, "message": message, "data": data}


def page(items: list, total: int, page: int, page_size: int) -> dict:
    """分页 {items, total, page, page_size}（上限 100）。"""
    return {"items": items, "total": total, "page": page, "page_size": page_size}
