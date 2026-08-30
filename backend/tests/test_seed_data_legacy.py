"""Q3 迁移回归：派生 adapter_config == 迁移前手写 ADAPTER_CFG（逐字符）。

迁移前 seed_data 手写 4 家 adapter_config（来源 git HEAD），Q3 改为由 MANIFEST_SNAPSHOTS
派生。本文件冻结迁移前基线，锁定「迁移不改变 adapter 语义」——未来任何人改动
MANIFEST_SNAPSHOTS 若意外改变派生值，此处即红。

注意：比较的是「原始 adapter_config」（不含 seed.py 写库时注入的 _manifest_v2）。
"""
import pytest

from app.seed_data import (
    CC_ADAPTER_CFG, CS_ADAPTER_CFG, GQ_ADAPTER_CFG, SP_ADAPTER_CFG,
)

# ---------------- 迁移前基线（2026-08-30 Q3 前 seed_data.py 手写值，git HEAD 冻结） ----------------

LEGACY_CS = {
    "contract_type": "sse",
    "timeout": 120,
    "prepare": [
        {"name": "login", "method": "POST", "path": "/api/v1/auth/login",
         "body": {"username": "{auth.username}", "password": "{auth.password}"},
         "extract": {"token": "access_token"}},
        {"name": "session", "method": "POST", "path": "/api/v1/sessions",
         "headers": {"Authorization": "Bearer {prepare.login.token}"},
         "extract": {"id": "session_id"}},
    ],
    "request": {
        "path": "/api/v1/sessions/{prepare.session.id}/messages", "method": "POST",
        "headers": {"Authorization": "Bearer {prepare.login.token}",
                    "Content-Type": "application/json"},
        "body": {"content": "{case.input.content}"},
    },
}

LEGACY_CC = {
    "contract_type": "sync",
    "timeout": 300,
    "prepare": [
        {"name": "upload", "method": "POST", "path": "/api/files/upload",
         "files": {"file": "{case.input.file_path}"},
         "extract": {"task_id": "task_id"}},
        {"name": "wait_done", "poll": {
            "path": "/api/tasks/{prepare.upload.task_id}",
            "until": {"status": ["WAITING_REVIEW", "SUCCESS", "FAILED", "CANCELLED"]},
            "interval": 2, "timeout": 300}},
    ],
    "request": {"path": "/api/tasks/{prepare.upload.task_id}/result", "method": "GET"},
}

LEGACY_SP = {
    "contract_type": "sse",
    "timeout": 120,
    "prepare": [
        {"name": "login", "method": "POST", "path": "/api/v1/auth/login",
         "body": {"username": "{auth.username}", "password": "{auth.password}"},
         "extract": {"token": "access_token"}},
        {"name": "review", "method": "POST", "path": "/api/v1/reviews",
         "headers": {"Authorization": "Bearer {prepare.login.token}",
                     "Content-Type": "application/json"},
         "body": {"bid_id": "{case.input.bid_id}", "dimension_id": "{case.input.dimension_id}"},
         "extract": {"review_id": "review_id"}},
    ],
    "request": {
        "path": "/api/v1/reviews/{prepare.review.review_id}/chat", "method": "POST",
        "headers": {"Authorization": "Bearer {prepare.login.token}",
                    "Content-Type": "application/json"},
        "body": {"question": "{case.input.question}"},
    },
}

LEGACY_GQ = {
    "contract_type": "sse",
    "timeout": 180,
    "prepare": [
        {"name": "login", "method": "POST", "path": "/api/auth/login",
         "body": {"username": "{auth.username}", "password": "{auth.password}"},
         "extract": {"token": "access_token"}},
        {"name": "session", "method": "POST", "path": "/api/sessions",
         "headers": {"Authorization": "Bearer {prepare.login.token}"},
         "body": {"library_id": 3},
         "extract": {"id": "id"}},
    ],
    "request": {
        "path": "/api/chat/{prepare.session.id}", "method": "POST",
        "headers": {"Authorization": "Bearer {prepare.login.token}",
                    "Content-Type": "application/json"},
        "body": {"content": "{case.input.content}", "stream": True},
    },
    "sse": {"field_map": {"token": "answer"}},
}

LEGACY = {
    "customer-service": LEGACY_CS,
    "contract-check": LEGACY_CC,
    "smart-procurement": LEGACY_SP,
    "good-question": LEGACY_GQ,
}

ACTUAL = {
    "customer-service": CS_ADAPTER_CFG,
    "contract-check": CC_ADAPTER_CFG,
    "smart-procurement": SP_ADAPTER_CFG,
    "good-question": GQ_ADAPTER_CFG,
}


@pytest.mark.parametrize("name", ["customer-service", "contract-check",
                                  "smart-procurement", "good-question"])
def test_derived_matches_legacy(name):
    assert ACTUAL[name] == LEGACY[name], f"{name} 派生 adapter_config 与迁移前手写值不一致"
