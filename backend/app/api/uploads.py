"""5.2 文件上传：用例附件（决策 #12，宿主 ./uploads ↔ 容器 /app/uploads）。

- 扩展名白名单 + 大小上限（system_config file_max_size，MB）
- UUID 文件名落盘，返回 file_ref（容器内路径，executor 直接可读）
- pdf 魔数兜底（%PDF）；其余类型按扩展名放宽（task.md 验收口径）
"""
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.core.case_rules import DEFAULT_MAX_MB, validate_upload
from app.core.db import get_db
from app.core.errors import ApiError
from app.core.response import ok
from app.models import SystemConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/uploads", tags=["uploads"])

Staff = Depends(require_role("admin", "evaluator"))

UPLOAD_DIR = Path("/app/uploads")


async def _max_bytes(db: AsyncSession) -> int:
    cfg = await db.get(SystemConfig, "file_max_size")
    mb = float(cfg.value) if cfg and isinstance(cfg.value, (int, float)) else DEFAULT_MAX_MB
    return int(mb * 1024 * 1024)


@router.post("")
async def upload_file(file: UploadFile = File(...), _: object = Staff,
                      db: AsyncSession = Depends(get_db)):
    filename = file.filename or ""
    ext = Path(filename).suffix.lower()
    logger.debug("upload in: filename=%s size=None ext=%s", filename, ext)
    max_bytes = await _max_bytes(db)
    # 流式限读：超过上限直接拒绝，避免整文件入内存
    content = await file.read(max_bytes + 1)
    err = validate_upload(ext, content, max_bytes)
    if err is not None:
        code, message = err
        raise ApiError(code, message, 400)

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stored = f"{uuid.uuid4().hex}{ext}"
    (UPLOAD_DIR / stored).write_bytes(content)
    file_ref = str(UPLOAD_DIR / stored)
    logger.debug("upload out: file_ref=%s size=%s", file_ref, len(content))
    return ok({"file_ref": file_ref, "filename": filename, "size": len(content)})
