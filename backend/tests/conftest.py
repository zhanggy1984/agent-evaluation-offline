"""宿主单测全局兜底：为 Settings 强校验注入最小密钥 env（app/core/config.py #31）。

security 等模块顶层 import app.core.config（Settings），宿主 shell 无这些密钥
env 时会 ValidationError 阻断 import。此处 setdefault 兜底：.env 已有合法值时
不被覆盖（行为与容器一致），宿主缺失时用固定测试值补足。

注意：create_async_engine 是惰性的（不真连库），单测一律不触真实连接。
"""
import base64
import os

# JWT：强校验要求 ≥256bit（32 字节 UTF-8），固定值便于断言
os.environ.setdefault("JWT_SECRET", "unit-test-jwt-secret-" + "0" * 40)
# MultiFernet：逗号分隔的 32 字节 urlsafe-base64 密钥（多代轮换，单测给一代即可）
os.environ.setdefault("FERNET_KEYS", base64.urlsafe_b64encode(b"0" * 32).decode("ascii"))
os.environ.setdefault("DB_PASSWORD", "test-password")
