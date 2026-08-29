"""P2-D4 上传/导出端点安全分支集成测试（真库 + 直调路由函数，临时目录落盘）。

上传（uploads.py）：路由层行为——扩展名白名单 / 大小上限 / pdf 魔数兜底
（validate_upload 纯函数已由 test_cases.py 覆盖，此处测路由层落盘与拒绝）；
UPLOAD_DIR 为容器路径 /app/uploads，monkeypatch 到临时目录。
导出（exports.py）：create_export 非终态 400 / format 白名单；download_export 函数体
显式安全分支——viewer 403 / token 一次性 / 过期 / 归属他人 403 / 无效 404 / 成功下载。
权限 Depends 层拦截（create_export/upload_file 的 Staff、get_current_user）由
test_auth_guard.py（deps.py）覆盖，直调绕过依赖链故不重复。

ExportToken 随 eval_run ON DELETE CASCADE（挂 env.runs 自动清理）；
audit_log 无级联，测试用专属 IP 清理（同 P2-D2 惯例）。
"""
import io
import secrets
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("aiomysql")

from pydantic import ValidationError
from sqlalchemy import delete, select
from starlette.datastructures import UploadFile

from app.api import exports as exports_mod
from app.api import uploads as uploads_mod
from app.core.db import SessionLocal
from app.core.errors import (
    ApiError, E_FILE_SIZE, E_FILE_TYPE, E_NO_PERMISSION, E_NOT_FOUND, E_VALIDATION,
)
from app.models import ExportToken
from app.models.misc import AuditLog
from helpers import create_chain, make_run

_staff = SimpleNamespace(id=1, username="it-d4-staff", role="evaluator")
_viewer = SimpleNamespace(id=2, username="it-d4-viewer", role="viewer")
_other = SimpleNamespace(id=3, username="it-d4-other", role="evaluator")


def _req(ip: str):
    """fake Request：审计读 headers x-forwarded-for（None 兜底 client.host）。"""
    return SimpleNamespace(headers={"x-forwarded-for": None}, client=SimpleNamespace(host=ip))


def _pdf(extra: bytes = b"") -> bytes:
    return b"%PDF-1.7 eval report" + extra


async def _clean_audit(ip: str) -> None:
    async with SessionLocal() as s:
        await s.execute(delete(AuditLog).where(AuditLog.ip == ip))
        await s.commit()


def _async_max(n: int):
    """monkeypatch _max_bytes 用：固定字节上限，避免造 50MB 文件。"""
    async def _f(db):
        return n
    return _f


async def _seed_terminal_run(env, db):
    """终态（completed）run；导出无需结果数据（payload 可为空）。"""
    ch = await create_chain(db)
    run = make_run(ch["agent"].id, ch["suite"].id)
    run.status = "completed"
    db.add(run)
    await db.commit()
    env.agents.append(ch["agent"]); env.interfaces.append(ch["iface"])
    env.suites.append(ch["suite"]); env.cases.extend(ch["cases"]); env.runs.append(run)
    return ch, run


async def _seed_export_token(export_dir: Path, db, run, user_id: int, *,
                             used: bool = False, expired: bool = False) -> str:
    """造 ExportToken + 落盘文件，返回明文 token。"""
    export_dir.mkdir(parents=True, exist_ok=True)
    tk = secrets.token_urlsafe(32)
    token_hash = sha256(tk.encode()).hexdigest()
    expires = datetime.now() - timedelta(hours=1) if expired else datetime.now() + timedelta(hours=1)
    db.add(ExportToken(token_hash=token_hash, run_id=run.id, format="pdf",
                       user_id=user_id, expires_at=expires, used=used))
    await db.commit()
    (export_dir / f"{token_hash}.pdf").write_bytes(_pdf())
    return tk


# ---------------- 上传：路由层行为 ----------------
@pytest.mark.asyncio(loop_scope="session")
async def test_upload_ok_pdf(tmp_path, db, env, monkeypatch):
    """pdf 魔数通过 → 落盘返回 file_ref（容器内路径），size 正确。"""
    monkeypatch.setattr(uploads_mod, "UPLOAD_DIR", tmp_path / "uploads")
    resp = await uploads_mod.upload_file(
        UploadFile(file=io.BytesIO(_pdf()), filename="report.pdf"), _staff, db)
    ref = resp["data"]["file_ref"]
    assert ref.endswith(".pdf")
    assert (tmp_path / "uploads" / Path(ref).name).exists()
    assert resp["data"]["size"] == len(_pdf())


@pytest.mark.asyncio(loop_scope="session")
async def test_upload_rejects_ext(tmp_path, db, monkeypatch):
    """扩展名不在白名单（exe）→ 400 E_FILE_TYPE。"""
    monkeypatch.setattr(uploads_mod, "UPLOAD_DIR", tmp_path / "uploads")
    with pytest.raises(ApiError) as ei:
        await uploads_mod.upload_file(
            UploadFile(file=io.BytesIO(b"MZ"), filename="evil.exe"), _staff, db)
    assert ei.value.status_code == 400 and ei.value.code == E_FILE_TYPE


@pytest.mark.asyncio(loop_scope="session")
async def test_upload_rejects_size(tmp_path, db, monkeypatch):
    """超大小上限 → 400 E_FILE_SIZE（限读拒绝，不整文件入内存）。"""
    monkeypatch.setattr(uploads_mod, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(uploads_mod, "_max_bytes", _async_max(100))
    with pytest.raises(ApiError) as ei:
        await uploads_mod.upload_file(
            UploadFile(file=io.BytesIO(b"x" * 200), filename="a.txt"), _staff, db)
    assert ei.value.status_code == 400 and ei.value.code == E_FILE_SIZE


# ---------------- 导出：create_export ----------------
@pytest.mark.asyncio(loop_scope="session")
async def test_create_export_ok(tmp_path, db, env, monkeypatch):
    """终态 run → 生成一次性 token + 落盘 PDF（token 明文只回响应，库中仅存 sha256）。"""
    ip = "10.9.9.11"
    monkeypatch.setattr(exports_mod, "EXPORT_DIR", tmp_path / "exports")
    _, run = await _seed_terminal_run(env, db)
    resp = await exports_mod.create_export(
        run.id, exports_mod.ExportCreate(format="pdf"), _req(ip), db, _staff)
    data = resp["data"]
    assert data["format"] == "pdf"
    assert (tmp_path / "exports" / f"{sha256(data['token'].encode()).hexdigest()}.pdf").exists()
    await _clean_audit(ip)


@pytest.mark.asyncio(loop_scope="session")
async def test_create_export_non_terminal(db, env):
    """非终态（pending）run → 400。"""
    ip = "10.9.9.12"
    ch = await create_chain(db)
    run = make_run(ch["agent"].id, ch["suite"].id)  # pending 非终态
    db.add(run)
    await db.commit()
    env.agents.append(ch["agent"]); env.interfaces.append(ch["iface"])
    env.suites.append(ch["suite"]); env.cases.extend(ch["cases"]); env.runs.append(run)
    with pytest.raises(ApiError) as ei:
        await exports_mod.create_export(
            run.id, exports_mod.ExportCreate(format="pdf"), _req(ip), db, _staff)
    assert ei.value.status_code == 400
    assert ei.value.code == E_VALIDATION
    await _clean_audit(ip)  # 审计在 400 前未写；保险清理


@pytest.mark.asyncio(loop_scope="session")
async def test_create_export_format_whitelist(db, env):
    """format 非 pdf → pydantic 白名单拒绝（ExportCreate pattern）。"""
    ip = "9.9.9.13"
    _, run = await _seed_terminal_run(env, db)
    with pytest.raises(ValidationError):
        await exports_mod.create_export(
            run.id, exports_mod.ExportCreate(format="xlsx"), _req(ip), db, _staff)
    await _clean_audit(ip)


# ---------------- 导出：download_export 安全分支（函数体显式校验） ----------------
@pytest.mark.asyncio(loop_scope="session")
async def test_download_export_ok_then_once(tmp_path, db, env, monkeypatch):
    """成功下载（PDF + token used=1）；二次下载 → 400 一次性拒绝。"""
    ip = "10.9.9.20"
    monkeypatch.setattr(exports_mod, "EXPORT_DIR", tmp_path / "exports")
    _, run = await _seed_terminal_run(env, db)
    tk = await _seed_export_token(tmp_path / "exports", db, run, _staff.id)
    resp = await exports_mod.download_export(tk, _req(ip), db, _staff)
    assert resp.status_code == 200
    assert resp.media_type == "application/pdf"
    async with SessionLocal() as s:
        row = (await s.execute(select(ExportToken).where(ExportToken.run_id == run.id))).scalars().first()
        assert row.used is True  # 一次性：下载后置 used
    with pytest.raises(ApiError) as ei:
        await exports_mod.download_export(tk, _req(ip), db, _staff)
    assert ei.value.status_code == 400
    assert ei.value.code == E_VALIDATION
    await _clean_audit(ip)


@pytest.mark.asyncio(loop_scope="session")
async def test_download_viewer_forbidden(tmp_path, db, env, monkeypatch):
    """viewer 拒绝（函数体显式；get_current_user 依赖层另有 deps 测试）。"""
    ip = "10.9.9.21"
    monkeypatch.setattr(exports_mod, "EXPORT_DIR", tmp_path / "exports")
    _, run = await _seed_terminal_run(env, db)
    tk = await _seed_export_token(tmp_path / "exports", db, run, _staff.id)
    with pytest.raises(ApiError) as ei:
        await exports_mod.download_export(tk, _req(ip), db, _viewer)
    assert ei.value.status_code == 403 and ei.value.code == E_NO_PERMISSION
    await _clean_audit(ip)


@pytest.mark.asyncio(loop_scope="session")
async def test_download_expired(tmp_path, db, env, monkeypatch):
    """token 过期 → 400（used 校验之后、归属校验之前）。"""
    ip = "10.9.9.22"
    monkeypatch.setattr(exports_mod, "EXPORT_DIR", tmp_path / "exports")
    _, run = await _seed_terminal_run(env, db)
    tk = await _seed_export_token(tmp_path / "exports", db, run, _staff.id, expired=True)
    with pytest.raises(ApiError) as ei:
        await exports_mod.download_export(tk, _req(ip), db, _staff)
    assert ei.value.status_code == 400
    assert ei.value.code == E_VALIDATION
    await _clean_audit(ip)


@pytest.mark.asyncio(loop_scope="session")
async def test_download_wrong_owner(tmp_path, db, env, monkeypatch):
    """token 归属他人 → 403（下载者非生成者）。"""
    ip = "10.9.9.23"
    monkeypatch.setattr(exports_mod, "EXPORT_DIR", tmp_path / "exports")
    _, run = await _seed_terminal_run(env, db)
    tk = await _seed_export_token(tmp_path / "exports", db, run, _other.id)
    with pytest.raises(ApiError) as ei:
        await exports_mod.download_export(tk, _req(ip), db, _staff)
    assert ei.value.status_code == 403 and ei.value.code == E_NO_PERMISSION
    await _clean_audit(ip)


@pytest.mark.asyncio(loop_scope="session")
async def test_download_invalid_token(db, env):
    """库中不存在的 token → 404。"""
    ip = "10.9.9.24"
    with pytest.raises(ApiError) as ei:
        await exports_mod.download_export(secrets.token_urlsafe(16), _req(ip), db, _staff)
    assert ei.value.status_code == 404 and ei.value.code == E_NOT_FOUND
    await _clean_audit(ip)
