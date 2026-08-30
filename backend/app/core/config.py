"""应用配置：pydantic-settings 读 env（仅密钥类，业务参数一律落 system_config 表）。

启动强校验（遗留清单 #31）：
- jwt_secret 非空且 ≥256bit（32 字节）
- fernet_keys 非空（逗号分隔多代）
- db_password 非空
测试环境（APP_ENV=test）跳过强校验（用 sqlite 内存）。
"""
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ---- 运行 ----
    app_env: str = "dev"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    workers: int = 1  # 单进程部署约束（方案决策）

    # ---- MySQL ----
    db_host: str = "localhost"
    db_port: int = 3306
    db_user: str = "evaluation"
    db_name: str = "ai_evaluation"
    db_password: str = ""
    # P2-C3 迁移专用账号（alembic DDL）：成对配置时用迁移账号，否则回退 db_user（兼容单账号现状）
    db_migrate_user: str = ""
    db_migrate_password: str = ""

    # ---- 密钥（强校验） ----
    jwt_secret: str = ""
    fernet_keys: str = ""
    jwt_access_minutes: int = 30
    jwt_refresh_days: int = 7

    # ---- LLM profile 密钥（judge 用，OpenAI 兼容端点） ----
    judge_api_key: str = ""

    # P2-D20：可信反向代理层数——X-Forwarded-For 从右数第 N 个取真实客户端 IP。
    # 当前链路 前端 nginx + api-gateway 各 append 一次 → 2；链路变化时调整（audit._client_ip）
    xff_trusted_proxy_count: int = 2

    # P2-E6：CORS 允许源（逗号分隔）。默认保留开发期 vite 直连（5173）+ 容器前端（8180）；
    # 生产同源部署可配 CORS_ORIGINS= 置空（不注册 CORS 中间件，跨域被浏览器默认拦截）
    cors_origins: str = "http://localhost:5173,http://localhost:8180"

    @model_validator(mode="after")
    def _validate_secrets(self) -> "Settings":
        if self.app_env == "test":
            return self
        if not self.jwt_secret or len(self.jwt_secret.encode("utf-8")) < 32:
            raise ValueError("jwt_secret 必须 ≥256bit（32 字节 UTF-8），禁止启动（#31）")
        if not self.fernet_keys.strip():
            raise ValueError("fernet_keys 为空，禁止启动（MultiFernet 多密钥，逗号分隔）")
        if not self.db_password:
            raise ValueError("db_password 为空，禁止启动")
        return self

    @property
    def sqlalchemy_url(self) -> str:
        return (
            f"mysql+aiomysql://{self.db_user}:{self.db_password}@"
            f"{self.db_host}:{self.db_port}/{self.db_name}?charset=utf8mb4"
        )

    @property
    def sqlalchemy_migrate_url(self) -> str:
        """迁移专用连接（P2-C3）：DB_MIGRATE_* 成对配置时用迁移账号（DDL），
        任一缺省回退主账号（兼容现状：单账号跑迁移与服务）。"""
        if self.db_migrate_user and self.db_migrate_password:
            user, pw = self.db_migrate_user, self.db_migrate_password
        else:
            user, pw = self.db_user, self.db_password
        return (
            f"mysql+aiomysql://{user}:{pw}@"
            f"{self.db_host}:{self.db_port}/{self.db_name}?charset=utf8mb4"
        )

    @property
    def fernet_key_list(self) -> list[str]:
        return [k.strip() for k in self.fernet_keys.split(",") if k.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
