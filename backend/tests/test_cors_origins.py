"""P2-E6 CORS 源配置化单测：逗号分隔解析 + 置空关闭。

原实现硬编码 allow_origins=["http://localhost:5173", "http://localhost:8180"]。
现改读 config.cors_origins（逗号分隔 env），置空则不注册 CORS 中间件（生产同源部署收紧）。
"""
from app.main import _parse_cors_origins


def test_parse_default_list():
    assert _parse_cors_origins("http://localhost:5173, http://localhost:8180 ") == [
        "http://localhost:5173",
        "http://localhost:8180",
    ]


def test_parse_empty_disables():
    assert _parse_cors_origins("") == []


def test_parse_blank_entries_stripped():
    assert _parse_cors_origins("http://a.com, ,http://b.com") == [
        "http://a.com",
        "http://b.com",
    ]
