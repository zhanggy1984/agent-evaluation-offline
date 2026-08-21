"""报告渲染：纯函数 payload dict → bytes（无 DB、无 async、无状态）。

在线程（asyncio.to_thread）中执行，天然规避 ProcessPool fork/pickle 死锁
（solution_detail :946 风险 #22 的结构性消除）。payload 由调用方（父进程）
组装为可序列化 dict，本模块只负责渲染，不碰数据库。

单位约定（与全站一致）：
- 分数 0-100（[[score-scale-hundred]]）
- 成本金额（元），单价单位 = 元/百万 token（[[cost-unit-per-million]]）
"""
import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

# 中文渲染：容器无中文字体文件，用 reportlab 内置 CID 字体 STSong-Light。
# 模块级注册一次即可（重复注册幂等）。
pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))

DIM_LABEL = {
    "completeness": "完成度",
    "factuality": "事实性",
    "reasoning_quality": "思考链",
    "tool_usage": "工具使用",
    "ttft": "首字延迟",
    "e2e": "端到端延迟",
    "token_cost": "Token 成本",
}
RUN_STATUS_LABEL = {
    "pending": "等待中", "running": "执行中", "scoring": "评分中",
    "scoring_failed": "评分失败", "completed": "完成", "partial_failed": "部分失败",
    "timeout": "超时", "cancelled": "已取消",
}
PF_LABEL = {"pass": "通过", "fail": "失败", "error": "错误", "na": "N/A"}

_PDF_PAGE_W = A4[0] - 4 * cm  # A4 宽 - 左右 2cm 边距


def _fmt(v, digits: int = 2):
    """数值归一化为 float 并保留位数；None 原样返回（上游显示 N/A）。"""
    return round(float(v), digits) if v is not None else None


def _num(v, digits: int = 2) -> str:
    if v is None:
        return "N/A"
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    return str(v)


def _dims(dims: list | None) -> str:
    """维度分 → 可读文本，如「事实性: 92.50  Token 成本: 1.23」。N/A 维度带原因。"""
    parts = []
    for d in dims or []:
        label = DIM_LABEL.get(d.get("code"), d.get("code", ""))
        if d.get("na"):
            why = f"（{d['na_reason']}）" if d.get("na_reason") else ""
            parts.append(f"{label}: N/A{why}")
        else:
            parts.append(f"{label}: {_num(d.get('value'))}")
    return "  ".join(parts)


def _err(r: dict) -> str:
    t, d = r.get("error_type"), r.get("error_detail")
    if not t and not d:
        return ""
    return f"{t}: {d}" if t else d


# ---- 注入防护（7.6 A6）-----------------------------------------------------

_CSV_FORBIDDEN = ("=", "+", "-", "@", "\t", "\r")


def _sanitize_cell(v):
    """公式注入防护：可控串以 = + - @ 或制表/换行开头时加 ' 前缀使其成为纯文本（Excel 公式不执行）。
    非字符串（数值/None）原样返回，openpyxl 正常写数字/空单元格。"""
    if not isinstance(v, str):
        return v
    return "'" + v if v.startswith(_CSV_FORBIDDEN) else v


def _esc_html(s) -> str:
    """reportlab Paragraph 文本转义（Paragraph 走 mini-HTML 解析，未转义的 <&> 会被当标签/实体解析）。"""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---- PDF -------------------------------------------------------------------

def _pdf_styles() -> dict:
    title = ParagraphStyle("title", fontName="STSong-Light", fontSize=18, leading=24, spaceAfter=6)
    h2 = ParagraphStyle("h2", fontName="STSong-Light", fontSize=12, leading=16,
                        spaceBefore=10, spaceAfter=6)
    cell = ParagraphStyle("cell", fontName="STSong-Light", fontSize=8, leading=11)
    head = ParagraphStyle("head", fontName="STSong-Light", fontSize=8, leading=11,
                          textColor=colors.white)
    label = ParagraphStyle("label", fontName="STSong-Light", fontSize=8, leading=11,
                           textColor=colors.HexColor("#404040"))
    return {"title": title, "h2": h2, "cell": cell, "head": head, "label": label}


def _watermark(canvas, doc) -> None:
    """每页斜向半透明水印（用户名 + 时间），防报告外泄追溯。"""
    text = getattr(doc, "watermark", "")
    if not text:
        return
    canvas.saveState()
    canvas.setFont("STSong-Light", 36)
    canvas.setFillAlpha(0.12)
    canvas.setFillColor(colors.HexColor("#888888"))
    canvas.translate(A4[0] / 2, A4[1] / 2)
    canvas.rotate(45)
    canvas.drawCentredString(0, 0, text)
    canvas.restoreState()


def render_pdf(payload: dict, watermark: str) -> bytes:
    run, results = payload["run"], payload["results"]
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, rightMargin=2 * cm, leftMargin=2 * cm,
        topMargin=2.2 * cm, bottomMargin=2 * cm,
        title=f"评测报告 run#{run['run_id']}",
    )
    doc.watermark = watermark
    st = _pdf_styles()
    story = [Paragraph("AI Agent 评测报告", st["title"])]

    # 汇总 key-value 表（2 列 × 4 段）
    kv = [
        ("Run ID", str(run["run_id"]), "Agent", run.get("agent_name") or ""),
        ("版本", run.get("version") or "", "状态", RUN_STATUS_LABEL.get(run.get("status"), run.get("status", ""))),
        ("触发方式", run.get("trigger_type") or "", "总分", _num(run.get("agent_score"))),
        ("用例统计", f"通过 {run.get('pass_case', 0)} / 失败 {run.get('fail_case', 0)}"
                   f" / 错误 {run.get('error_case', 0)} / N/A {run.get('na_case', 0)}",
         "用例总数", _num(run.get("total_case"), 0)),
        ("TTFT p50/p95 (ms)", f"{_num(run.get('ttft_p50'))} / {_num(run.get('ttft_p95'))}",
         "E2E p50/p95 (ms)", f"{_num(run.get('e2e_p50'))} / {_num(run.get('e2e_p95'))}"),
        ("Token 总量", _num(run.get("total_tokens"), 0), "成本（元）", _num(run.get("total_cost"), 6)),
        ("模型", ", ".join(run.get("models") or []) or "N/A", "导出时间", watermark),
    ]
    kv_rows = [[Paragraph(k, st["label"]), Paragraph(_esc_html(v), st["cell"]),
                Paragraph(k2, st["label"]), Paragraph(_esc_html(v2), st["cell"])]
               for k, v, k2, v2 in kv]
    kv_table = Table(kv_rows, colWidths=[3.2 * cm, 5.6 * cm, 3.2 * cm, 5.0 * cm])
    kv_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f2f2f2")),
        ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#f2f2f2")),
    ]))
    story.append(kv_table)
    story.append(Spacer(1, 0.3 * cm))

    # 用例明细
    story.append(Paragraph("用例明细", st["h2"]))
    header = [Paragraph(h, st["head"]) for h in ("#", "用例", "接口", "结果", "总分", "维度分", "错误")]
    body = [header]
    for i, r in enumerate(results, 1):
        body.append([
            Paragraph(str(i), st["cell"]),
            Paragraph(_esc_html(r.get("case_name") or ""), st["cell"]),
            Paragraph(_esc_html(r.get("interface_name") or ""), st["cell"]),
            Paragraph(_esc_html(PF_LABEL.get(r.get("pass_fail"), r.get("pass_fail", ""))), st["cell"]),
            Paragraph(_num(r.get("score_total")), st["cell"]),
            Paragraph(_esc_html(_dims(r.get("score_per_dimension"))), st["cell"]),
            Paragraph(_esc_html(_err(r)), st["cell"]),
        ])
    # 列宽合计 = _PDF_PAGE_W，包一层让长文本换行
    col_w = [1.0, 3.8, 2.2, 1.2, 1.4, 5.0, 2.8]
    tbl = Table(body, colWidths=[c * cm for c in col_w], repeatRows=1)
    tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F5597")),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(tbl)

    doc.build(story, onFirstPage=_watermark, onLaterPages=_watermark)
    return buf.getvalue()


# ---- Excel -----------------------------------------------------------------

def _style_header(ws) -> None:
    fill = PatternFill("solid", fgColor="2F5597")
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = fill
        c.alignment = Alignment(vertical="center")
    ws.freeze_panes = "A2"


def render_xlsx(payload: dict, watermark: str) -> bytes:
    run, results = payload["run"], payload["results"]
    wb = Workbook()

    # Sheet1 汇总
    ws = wb.active
    ws.title = "汇总"
    rows = [
        ("Run ID", run["run_id"]), ("Agent", run.get("agent_name") or ""),
        ("版本", run.get("version") or ""),
        ("状态", RUN_STATUS_LABEL.get(run.get("status"), run.get("status", ""))),
        ("触发方式", run.get("trigger_type") or ""), ("总分", run.get("agent_score")),
        ("用例总数", run.get("total_case", 0)),
        ("通过 / 失败 / 错误 / N/A", f"{run.get('pass_case', 0)} / {run.get('fail_case', 0)}"
                                   f" / {run.get('error_case', 0)} / {run.get('na_case', 0)}"),
        ("TTFT p50/p95 (ms)", f"{run.get('ttft_p50')} / {run.get('ttft_p95')}"),
        ("E2E p50/p95 (ms)", f"{run.get('e2e_p50')} / {run.get('e2e_p95')}"),
        ("Token 总量", run.get("total_tokens")), ("成本（元）", run.get("total_cost")),
        ("模型", ", ".join(run.get("models") or []) or "N/A"),
    ]
    for i, (k, v) in enumerate(rows, start=1):
        ws.cell(i, 1, k).font = Font(bold=True)
        ws.cell(i, 2, _sanitize_cell(v))
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 46

    # Sheet2 用例明细
    ws2 = wb.create_sheet("用例明细")
    ws2.append(["序号", "用例", "接口", "结果", "总分", "维度分", "错误", "模型", "Tokens", "成本(元)"])
    for i, r in enumerate(results, 1):
        ws2.append([
            i, _sanitize_cell(r.get("case_name")), _sanitize_cell(r.get("interface_name")),
            _sanitize_cell(PF_LABEL.get(r.get("pass_fail"), r.get("pass_fail", ""))),
            r.get("score_total"), _sanitize_cell(_dims(r.get("score_per_dimension"))),
            _sanitize_cell(_err(r)),
            _sanitize_cell(r.get("model")), r.get("total_tokens"), r.get("total_cost"),
        ])
    for idx, w in enumerate([6, 30, 16, 8, 8, 40, 28, 14, 10, 12], 1):
        ws2.column_dimensions[get_column_letter(idx)].width = w
    _style_header(ws2)

    # Sheet3 成本明细
    ws3 = wb.create_sheet("成本明细")
    ws3.append(["用例", "模型", "Total Tokens", "成本(元)"])
    for r in results:
        ws3.append([_sanitize_cell(r.get("case_name")), _sanitize_cell(r.get("model")),
                    r.get("total_tokens"), r.get("total_cost")])
    if results:
        ws3.append(["合计", "", sum(r.get("total_tokens") or 0 for r in results),
                    round(sum((r.get("total_cost") or 0) for r in results), 6)])
        for c in ws3[1]:
            c.font = Font(bold=True)
    for idx, w in enumerate([30, 14, 14, 14], 1):
        ws3.column_dimensions[get_column_letter(idx)].width = w
    _style_header(ws3)

    # 页眉/页脚水印（Excel 无水印标准实现，落页脚最接近）
    for s in wb.worksheets:
        s.oddFooter.center.text = watermark
        s.evenFooter.center.text = watermark

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
