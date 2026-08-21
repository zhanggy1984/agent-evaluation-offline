#!/usr/bin/env python3
"""生成 .env（随机密钥 + 默认值），首次启动前执行一次。
用法：
  python scripts/init_env.py
  JUDGE_API_KEY=sk-xxx python scripts/init_env.py   # 顺带写入 judge key
已存在 .env 时跳过。
"""
import base64
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"


def b64url(n: int) -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(n)).decode("ascii").rstrip("=")


def main() -> int:
    if ENV_PATH.exists():
        print(".env 已存在，跳过生成。如需重新生成请先删除 .env。")
        return 0

    fernet = b64url(32)   # Fernet key = urlsafe base64(32B)
    jwt = b64url(48)      # 远大于 256bit
    db_pwd = b64url(18)
    judge_key = os.environ.get("JUDGE_API_KEY", "").strip()

    content = f"""# ============ AI Agent 评测系统 .env（由 scripts/init_env.py 生成） ============
APP_ENV=dev
APP_HOST=0.0.0.0
APP_PORT=8000

# MySQL（docker-compose 内网地址）
DB_HOST=mysql
DB_PORT=3306
DB_USER=evaluation
DB_NAME=ai_evaluation
DB_PASSWORD={db_pwd}

# 密钥（启动强校验：jwt_secret ≥256bit；fernet_keys 非空）
FERNET_KEYS={fernet}
JWT_SECRET={jwt}

# LLM profile 密钥（judge 用，OpenAI 兼容端点；base_url/model 配在 system_config）
JUDGE_API_KEY={judge_key}
"""
    ENV_PATH.write_text(content, encoding="utf-8")
    print(f"已生成 .env：{ENV_PATH}")
    print("DB_PASSWORD / FERNET_KEYS / JWT_SECRET 均为随机值。")
    print("下一步：docker compose up -d")
    return 0


if __name__ == "__main__":
    sys.exit(main())
