"""5.2 用例规则（纯函数，宿主单测直接测，不触 DB/网络/鉴权链）。

- is_agent_owner：留出集/字段级权限判定（agent.owner_id == 当前用户）
- recalc_annotation_status：双人标注整体状态机重算（draft→single→double→consensus/disputed）
- validate_upload：文件上传校验（扩展名白名单 / 大小上限 / pdf 魔数兜底）

API 层（app/api/{cases,annotations,uploads}.py）引用本模块，测试直接 import 本模块，
不经过 app.core.db（宿主无 aiomysql/aiosqlite 时仍可跑）。
"""
from app.core.errors import E_FILE_SIZE, E_FILE_TYPE, E_NO_PERMISSION, ApiError

ALLOWED_EXTS = {".pdf", ".txt", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".json"}
DEFAULT_MAX_MB = 50.0

# 二进制文件魔数（7.6 A7 魔数校验）：与扩展名白名单配套，防「改名换后缀」逃逸
_PDF_MAGIC = b"%PDF"
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # doc/xls（OLE2 复合文档）
_ZIP_MAGIC = b"PK\x03\x04"  # docx/xlsx（zip 容器）


def is_agent_owner(agent, user) -> bool:
    """当前用户是否为该 agent 的 owner（留出集不可见 / owner 禁标 golden 的判定依据）。"""
    return agent is not None and agent.owner_id is not None and agent.owner_id == user.id


def check_owner_denies_golden(agent_owner_id, user) -> None:
    """职责分离：owner 禁标/禁改自己 agent 的 golden_answer/assertions（admin 豁免）。

    等价于 deps.require_owner_or_qa，但纯逻辑（不触 DB/鉴权链），供单测直测。
    由用例更新接口按字段级权限调用。
    """
    if user.role == "admin":
        return
    if agent_owner_id is not None and agent_owner_id == user.id:
        raise ApiError(E_NO_PERMISSION, "owner 不可标/改自己 agent 的 golden_answer/assertions", 403)


def recalc_annotation_status(case, rows) -> None:
    """重算 case.annotation_status（整体口径，POST 标注后调用）。

    状态机：无标注→draft；存在维度仅 1 人→single；所有已标维度均双人且
    level 未标全→double；双人 level 全部一致→consensus；任一维度两人 level
    不一致→disputed（冲突优先于 single）。
    """
    if not rows:
        case.annotation_status = "draft"
        return
    by_dim: dict[str, list] = {}
    for r in rows:
        by_dim.setdefault(r.dimension_code, []).append(r)

    disputed = False
    has_single = False   # 存在维度仅 1 人标
    all_rated = True     # 所有已标维度均双人且全部标注带 level
    for anns in by_dim.values():
        annotators = {a.annotator_id for a in anns}
        levels = [float(a.level) for a in anns if a.level is not None]
        if len(annotators) < 2:
            has_single = True
            all_rated = False
            continue  # 单人维度不存在「双人不一致」，跳过冲突判定
        if len({round(l, 2) for l in levels}) >= 2:  # 双人 level 不一致
            disputed = True
        if len(levels) < len(anns):
            all_rated = False

    if disputed:
        case.annotation_status = "disputed"
    elif has_single:
        case.annotation_status = "single"
    elif all_rated:
        case.annotation_status = "consensus"
    else:
        case.annotation_status = "double"


def _magic_ok(ext: str, content: bytes) -> bool:
    """二进制文件魔数匹配（文本类 txt/csv/json 宽松放行，无强魔数）。"""
    if ext == ".pdf":
        return content.startswith(_PDF_MAGIC)
    if ext in (".docx", ".xlsx"):
        return content.startswith(_ZIP_MAGIC)
    if ext in (".doc", ".xls"):
        return content.startswith(_OLE2_MAGIC)
    return True


def validate_upload(ext: str, content: bytes, max_bytes: int) -> tuple[int, str] | None:
    """上传校验：扩展名白名单 + 大小上限 + 二进制文件魔数兜底。

    ext 为小写含点扩展名（如 ".pdf"）；返回 (错误码, 错误消息)，通过返回 None。
    魔数兜底只针对二进制格式（pdf/doc/docx/xls/xlsx），文本类按扩展名放宽（task.md 验收口径）。
    """
    if ext not in ALLOWED_EXTS:
        return E_FILE_TYPE, f"不支持的文件类型: {ext or '(无扩展名)'}"
    if len(content) > max_bytes:
        return E_FILE_SIZE, f"文件超过 {max_bytes // (1024 * 1024)}MB 上限"
    if ext in (".pdf", ".doc", ".docx", ".xls", ".xlsx") and not _magic_ok(ext, content):
        return E_FILE_TYPE, "文件内容校验失败（魔数不匹配）"
    return None
