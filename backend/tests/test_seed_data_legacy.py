"""Q3 迁移回归：派生 adapter_config == 手写 ADAPTER_CFG（逐字符）。

Q3 起 seed_data 由 MANIFEST_SNAPSHOTS 派生 4 家 adapter_config，本文件冻结「手写基线」，
锁定「改动 MANIFEST_SNAPSHOTS 若意外改变派生值，此处即红」。基线以各次 git HEAD 手写值为
准；2026-08-30 对接任务（Q9）有意修真实缺口后基线同步更新：
  - customer-service：加 sse.field_map{token→answer}（否则 answer 事件采空）
  - contract-check：加 login prepare + 受保护接口 Authorization（否则探 401）
  - T15：cs/sp 登录路由统一 /api/auth/login（4 家契约一致，仅 auth 路由，业务路由不变）

注意：比较的是「原始 adapter_config」（不含 seed.py 写库时注入的 _manifest_v2）。
"""
import pytest

from app.seed_data import (
    CC_ADAPTER_CFG, CS_ADAPTER_CFG, GQ_ADAPTER_CFG, SP_ADAPTER_CFG,
)

# ---------------- 手写基线（Q9 对接任务后冻结值，含 field_map/login prepare） ----------------

LEGACY_CS = {
    "contract_type": "sse",
    "timeout": 120,
    "prepare": [
        # T15：登录路由统一 /api/auth/login（cs 原为 /api/v1/auth/login，业务路由仍在 /api/v1）
        {"name": "login", "method": "POST", "path": "/api/auth/login",
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
    # Q9：token 事件承载最终答案，平台据此采集 answer
    "sse": {"field_map": {"token": "answer"}},
}

LEGACY_CC = {
    "contract_type": "sync",
    "timeout": 300,
    "prepare": [
        # Q9：files/tasks 均挂鉴权，先登录换 JWT 再带 Bearer 访问
        {"name": "login", "method": "POST", "path": "/api/auth/login",
         "body": {"username": "{auth.username}", "password": "{auth.password}"},
         "extract": {"token": "token"}},
        {"name": "upload", "method": "POST", "path": "/api/files/upload",
         "headers": {"Authorization": "Bearer {prepare.login.token}"},
         "files": {"file": "{case.input.file_path}"},
         "extract": {"task_id": "task_id"}},
        {"name": "wait_done", "poll": {
            "path": "/api/tasks/{prepare.upload.task_id}",
            "headers": {"Authorization": "Bearer {prepare.login.token}"},
            "until": {"status": ["WAITING_REVIEW", "SUCCESS", "FAILED", "CANCELLED"]},
            "interval": 2, "timeout": 300}},
    ],
    "request": {
        "path": "/api/tasks/{prepare.upload.task_id}/result", "method": "GET",
        "headers": {"Authorization": "Bearer {prepare.login.token}"},
    },
}

LEGACY_SP = {
    "contract_type": "sse",
    "timeout": 120,
    "prepare": [
        # T15：登录路由统一 /api/auth/login（sp 原为 /api/v1/auth/login）
        {"name": "login", "method": "POST", "path": "/api/auth/login",
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
