"""6.1 报告导出：POST /runs/{id}/export 生成一次性下载链接，GET /exports/{token} 一次性下载。

安全设计（风险 #27/#22 消化，见 solution_detail §4.7）：
- token 明文只在响应返回，库中仅存 sha256（token_hash CHAR(64)），防 DB 泄露直接可用
- 下载强制 token.user_id==当前用户 + 拒绝 viewer（#27 双重绑定）
- 渲染为纯函数（payload dict → bytes），在线程 asyncio.to_thread 执行，
  结构性规避 ProcessPool fork/pickle 死锁，无需 spawn/分页取数（#22）
- 一次性：used 置 1 后拒绝再次下载；expires_at 24h 过期
"""
import asyncio
import logging
import secrets
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ROLE_VIEWER, get_current_user, require_role
from app.core.audit import write_audit
from app.core.dashboard_rules import PF_ORDER, TERMINAL_STATUS
from app.core.db import get_db
from app.core.errors import ApiError, E_NO_PERMISSION, E_NOT_FOUND, E_VALIDATION
from app.core.response import ok
from app.exporter.render import _fmt, render_pdf, render_xlsx
from app.models import Agent, AgentInterface, EvalResult, EvalRun, ExportToken, TestCase
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/runs", tags=["exports"])
download_router = APIRouter(prefix="/exports", tags=["exports"])

Staff = Depends(require_role("admin", "evaluator"))

EXPORT_DIR = Path("/app/exports")
EXPORT_TTL_HOURS = 24
_MEDIA = {
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


class ExportCreate(BaseModel):
    format: str = Field(..., pattern="^(pdf|xlsx)$")


def _iso(dt) -> str | None:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else None


async def _build_payload(db: AsyncSession, run: EvalRun, results: list[EvalResult]) -> dict:
    """组装可序列化 payload：父进程取数，渲染线程只消费 dict（同 runs.run_results 的 join 口径）。"""
    cases: dict = {}
    if results:
        rows = await db.execute(select(TestCase).where(TestCase.id.in_([r.case_id for r in results])))
        cases = {c.id: c for c in rows.scalars().all()}
    ifaces: dict = {}
    if cases:
        rows = await db.execute(select(AgentInterface).where(AgentInterface.agent_id == run.agent_id))
        ifaces = {i.id: i.name for i in rows.scalars().all()}
    agent = await db.get(Agent, run.agent_id)
    models = sorted({r.model for r in results if r.model})

    results_out = [{
        "case_name": cases[r.case_id].name if r.case_id in cases else str(r.case_id),
        "interface_name": ifaces.get(cases[r.case_id].interface_id, "") if r.case_id in cases else "",
        "pass_fail": r.pass_fail,
        "score_total": _fmt(r.score_total),
        "score_per_dimension": r.score_per_dimension or [],
        "error_type": r.error_type,
        "error_detail": r.error_detail,
        "model": r.model,
        "total_tokens": r.total_tokens,
        "total_cost": _fmt(r.total_cost, 6),
    } for r in results]
    results_out.sort(key=lambda x: (PF_ORDER.get(x["pass_fail"], 9), -(x["score_total"] or -1)))

    return {
        "run": {
            "run_id": run.id,
            "agent_name": agent.name if agent else "",
            "version": run.version,
            "trigger_type": run.trigger_type,
            "status": run.status,
            "started_at": _iso(run.started_at),
            "finished_at": _iso(run.finished_at),
            "agent_score": _fmt(run.agent_score),
            "total_case": run.total_case,
            "pass_case": run.pass_case,
            "fail_case": run.fail_case,
            "error_case": run.error_case,
            "na_case": run.na_case,
            "ttft_p50": _fmt(run.ttft_p50, 3), "ttft_p95": _fmt(run.ttft_p95, 3),
            "e2e_p50": _fmt(run.e2e_p50, 3), "e2e_p95": _fmt(run.e2e_p95, 3),
            "total_tokens": run.total_tokens,
            "total_cost": _fmt(run.total_cost, 6),
            "models": models,
        },
        "results": results_out,
    }


@router.post("/{run_id}/export")
async def create_export(run_id: int, body: ExportCreate, request: Request,
                        db: AsyncSession = Depends(get_db), user: User = Staff):
    """异步生成报告（线程渲染不阻塞 event loop）→ 落一次性下载 token（viewer 403）。"""
    logger.debug("export in: run_id=%s format=%s", run_id, body.format)
    run = await db.get(EvalRun, run_id)
    if run is None:
        raise ApiError(E_NOT_FOUND, "run 不存在", 404)
    if run.status not in TERMINAL_STATUS:
        raise ApiError(E_VALIDATION, "仅终态 run 可导出报告", 400)

    results = (await db.execute(select(EvalResult).where(
        EvalResult.run_id == run_id))).scalars().all()
    payload = await _build_payload(db, run, results)
    watermark = f"{user.username} @ {datetime.now():%Y-%m-%d %H:%M:%S}"

    render = render_pdf if body.format == "pdf" else render_xlsx
    content = await asyncio.to_thread(render, payload, watermark)

    token = secrets.token_urlsafe(32)
    token_hash = sha256(token.encode()).hexdigest()
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    (EXPORT_DIR / f"{token_hash}.{body.format}").write_bytes(content)

    expires_at = datetime.now() + timedelta(hours=EXPORT_TTL_HOURS)
    filename = f"eval_report_run{run.id}.{body.format}"
    db.add(ExportToken(token_hash=token_hash, run_id=run.id, format=body.format,
                       user_id=user.id, expires_at=expires_at))
    await write_audit(db, user, request, "export.generate", "eval_run", run.id,
                      {"format": body.format, "filename": filename})
    await db.commit()
    logger.debug("export out: run_id=%s format=%s", run_id, body.format)
    return ok({
        "token": token, "format": body.format,
        "expires_at": expires_at.strftime("%Y-%m-%d %H:%M:%S"),
        "filename": filename,
    })


@download_router.get("/{token}")
async def download_export(token: str, request: Request,
                          db: AsyncSession = Depends(get_db),
                          user: User = Depends(get_current_user)):
    """一次性下载：sha256 匹配 + 未用 + 未过期 + token.user_id==当前用户 + 拒绝 viewer。"""
    logger.debug("export download in: token_len=%s", len(token))
    if user.role == ROLE_VIEWER:
        raise ApiError(E_NO_PERMISSION, "viewer 无权下载报告", 403)
    token_hash = sha256(token.encode()).hexdigest()
    row = (await db.execute(select(ExportToken).where(
        ExportToken.token_hash == token_hash))).scalars().first()
    if row is None:
        raise ApiError(E_NOT_FOUND, "导出链接无效", 404)
    if row.used:
        raise ApiError(E_VALIDATION, "导出链接已被使用", 400)
    if row.expires_at < datetime.now():
        raise ApiError(E_VALIDATION, "导出链接已过期", 400)
    if row.user_id != user.id:
        raise ApiError(E_NO_PERMISSION, "无权下载该报告", 403)

    path = EXPORT_DIR / f"{row.token_hash}.{row.format}"
    if not path.exists():
        raise ApiError(E_NOT_FOUND, "导出文件不存在", 404)
    row.used = True
    await write_audit(db, user, request, "export.download", "eval_run", row.run_id,
                      {"format": row.format})
    await db.commit()
    logger.debug("export download out: run_id=%s format=%s", row.run_id, row.format)
    filename = f"eval_report_run{row.run_id}.{row.format}"
    return FileResponse(path, media_type=_MEDIA[row.format], filename=filename)
