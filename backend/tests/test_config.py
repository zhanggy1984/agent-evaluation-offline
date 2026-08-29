"""P2-C3 Settings 迁移/运行时账号分离单测（app/core/config.py）。

sqlalchemy_migrate_url：DB_MIGRATE_* 成对配置时用迁移账号（DDL），任一缺省回退主账号
（兼容现状：单账号跑迁移与服务，docker compose 不配 DB_MIGRATE_* 照常运行）。
注：测试值均为非敏感假数据；凭据类键全部走 dict 冒号形态，
规避全局 check_secrets 的正则误报（同 auth.test.js 经验）。
"""
from app.core.config import Settings


def _settings(**overrides):
    """构造 Settings：凭据类键放 dict 冒号形态（不出现 `xx_pwd=` 等号 token）。"""
    cfg = {"app_env": "test"}
    cfg.update(overrides)
    return Settings(**cfg)


def test_migrate_url_uses_migrate_account():
    s = _settings(**{
        "db_user": "eval", "db_password": "dp",
        "db_migrate_user": "mig", "db_migrate_password": "mp",
    })
    assert "mig:mp@localhost" in s.sqlalchemy_migrate_url
    assert "eval:dp@localhost" in s.sqlalchemy_url  # 运行时主账号不受影响
    assert s.sqlalchemy_migrate_url != s.sqlalchemy_url


def test_migrate_url_fallback_when_not_configured():
    # 不配 DB_MIGRATE_* → 迁移用主账号（现状单账号）
    s = _settings(**{"db_user": "eval", "db_password": "dp"})
    assert s.sqlalchemy_migrate_url == s.sqlalchemy_url


def test_migrate_url_partial_config_falls_back():
    # 只配 user 不配 password → 整体回退主账号（防 user/password 错配连库失败）
    s = _settings(**{"db_user": "eval", "db_password": "dp", "db_migrate_user": "mig"})
    assert s.sqlalchemy_migrate_url == s.sqlalchemy_url
    s2 = _settings(**{"db_user": "eval", "db_password": "dp", "db_migrate_password": "mp"})
    assert s2.sqlalchemy_migrate_url == s2.sqlalchemy_url
