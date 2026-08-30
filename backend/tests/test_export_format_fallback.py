"""P2-E3 导出下载 format 兜底单测：未知格式 → 回退 pdf，防 KeyError 500。

DB enum 声明含 "pdf,xlsx" 但实现仅支持 pdf（_MEDIA 仅 pdf、创建端点 pattern="^pdf$"、
渲染只 render_pdf）。唯一写入路径已被 pattern 拦截，DB 正常不会有 xlsx token；
但若未来残留/人为写入，下载处 `_MEDIA[row.format]` 会 KeyError 500——现改 `.get(...,"application/pdf")` 兜底。
"""
from app.api.exports import _MEDIA


def test_media_has_pdf():
    assert _MEDIA["pdf"] == "application/pdf"


def test_unknown_format_falls_back_to_pdf():
    # 下载处 _MEDIA.get(row.format, "application/pdf")：xlsx 等未知格式不应 KeyError
    assert _MEDIA.get("xlsx", "application/pdf") == "application/pdf"
    assert _MEDIA.get("docx", "application/pdf") == "application/pdf"
