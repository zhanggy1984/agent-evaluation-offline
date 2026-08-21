"""结构化日志 + 敏感字段脱敏（§九-8）。

脱敏规则：key 命中 authorization/token/password/secret/api_key/fernet/credential/auth_config
的字段值统一打码；正文（answer/reasoning）默认不落日志。
"""
import json
import logging
import re
from datetime import datetime, timezone

_SENSITIVE_KEY = re.compile(
    r"(authorization|token|password|secret|api[_-]?key|fernet|credential|auth_config)",
    re.IGNORECASE,
)

# 需要整体脱敏的请求头
_SENSITIVE_HEADERS = {"authorization", "x-csrf-token", "cookie", "set-cookie"}


def mask_dict(value, depth: int = 0):
    if depth > 6:
        return "***"
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if _SENSITIVE_KEY.search(str(k)):
                out[k] = "***"
            else:
                out[k] = mask_dict(v, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [mask_dict(x, depth + 1) for x in value]
    return value


def mask_headers(headers: dict) -> dict:
    return {k: "***" if k.lower() in _SENSITIVE_HEADERS else v for k, v in headers.items()}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        extra = getattr(record, "extra_fields", None)
        if extra:
            data.update(mask_dict(extra))
        return json.dumps(data, ensure_ascii=False, default=str)


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)
    # 控制第三方噪音
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
