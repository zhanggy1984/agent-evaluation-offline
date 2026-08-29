"""7.1 安全基座单测（app/core/security.py）。

JWT(HS256 硬编码) / MultiFernet / bcrypt。宿主跑依赖 conftest 兜底注入
JWT_SECRET / FERNET_KEYS / DB_PASSWORD（Settings #31 强校验，见 conftest.py）。
"""
import jwt as pyjwt
import pytest

from app.core import security
from app.core.errors import ApiError


# ---------------- 密码 ----------------
def test_password_hash_verify_roundtrip():
    h = security.hash_password("Eval#2026")
    assert h != "Eval#2026"
    assert security.verify_password("Eval#2026", h) is True
    assert security.verify_password("wrong", h) is False


def test_verify_password_bad_hash():
    # 非法 bcrypt 串 → ValueError → 兜底返回 False（不抛出）
    assert security.verify_password("x", "not-a-bcrypt-hash") is False


# ---------------- Fernet ----------------
def test_fernet_roundtrip():
    raw = b"secret-token-bytes"
    tok = security.fernet_encrypt(raw)
    assert tok != raw
    assert security.fernet_decrypt(tok) == raw


def test_fernet_tampered_token_rejected():
    raw = security.fernet_encrypt(b"data")
    bad = raw[:-1] + bytes([raw[-1] ^ 0xFF])  # 篡改末字节 → InvalidToken → ApiError
    with pytest.raises(ApiError):
        security.fernet_decrypt(bad)


# ---------------- JWT（HS256） ----------------
def test_jwt_roundtrip():
    token = security.create_access_token(user_id=7, role="evaluator")
    payload = security.decode_access_token(token)
    assert payload["sub"] == "7"
    assert payload["role"] == "evaluator"
    assert payload["type"] == "access"


def test_jwt_expired(monkeypatch):
    monkeypatch.setattr(security.settings, "jwt_access_minutes", -1)  # 签发即过期
    token = security.create_access_token(user_id=1, role="admin")
    with pytest.raises(ApiError):
        security.decode_access_token(token)


def test_jwt_wrong_secret():
    token = pyjwt.encode({"sub": "1", "type": "access"},
                         "another-secret-key-00000000000000000000000000000000",
                         algorithm="HS256")
    with pytest.raises(ApiError):
        security.decode_access_token(token)


def test_jwt_wrong_type():
    # 载荷 type 非 access → 拒绝（refresh 不能当 access 用）
    token = pyjwt.encode({"sub": "1", "role": "admin", "type": "refresh"},
                         security.settings.jwt_secret, algorithm="HS256")
    with pytest.raises(ApiError):
        security.decode_access_token(token)


def test_alg_none_rejected():
    # 无签名 token：algorithms=["HS256"] 显式拒绝 alg=none
    token = pyjwt.encode({"sub": "1", "type": "access", "exp": 9999999999},
                         None, algorithm="none",
                         headers={"alg": "none", "typ": "JWT"})
    with pytest.raises(ApiError):
        security.decode_access_token(token)


# ---------------- 随机值 ----------------
def test_random_ids():
    assert len(security.new_family_id()) == 32  # 32 hex 字符
    assert len(security.new_token_value()) >= 32  # urlsafe 48 bytes（refresh token 原文）


# ---------------- SSRF（7.6 A4：build_networks 拒全放行 / 169.254 移出白名单） ----------------
from app.core.http import DEFAULT_AGENT_CIDRS, JUDGE_DENY_CIDRS, AllowlistAsyncClient, build_agent_client, build_networks


def test_build_networks_rejects_any_any():
    # 0.0.0.0/0、::/0 全地址放行 → 直接拒绝
    with pytest.raises(ApiError):
        build_networks(["0.0.0.0/0"])
    with pytest.raises(ApiError):
        build_networks(["::/0"])


def test_build_networks_mixed_still_rejects():
    # 白名单里混入 0/0 也要整体拒绝（不允许"借道"）
    with pytest.raises(ApiError):
        build_networks(["10.0.0.0/8", "0.0.0.0/0"])


def test_build_networks_normal_cidrs_ok():
    nets = build_networks(["10.0.0.0/8", "192.168.1.5"])
    assert len(nets) == 2


def test_default_cidrs_exclude_cloud_metadata():
    # 169.254.0.0/16（云元数据段 metadata 169.254.169.254）不得在默认白名单
    assert "169.254.0.0/16" not in DEFAULT_AGENT_CIDRS


# ---------------- P2-C1 judge 出站 SSRF：域名白名单（llm_allowlist）+ IP 黑名单（deny_cidrs） ----------------
import socket

# judge 路径：allow_hosts=llm_allowlist、allow_cidrs=[]（域名白名单）、deny_cidrs=JUDGE_DENY_CIDRS（IP 黑名单）
def _judge_client(hosts):
    return AllowlistAsyncClient(allow_hosts=hosts, allow_cidrs=[], deny_cidrs=JUDGE_DENY_CIDRS)


def test_judge_deny_rejects_metadata_ip():
    # admin 热改白名单加云元数据 IP（169.254.169.254）→ 白名单命中但出站被 deny 拦截
    client = _judge_client(["169.254.169.254"])
    with pytest.raises(ApiError):
        client._resolve("169.254.169.254", 80)


def test_judge_deny_rejects_private_ip():
    # admin 热改白名单加私网 IP → 拒绝
    client = _judge_client(["10.0.0.5"])
    with pytest.raises(ApiError):
        client._resolve("10.0.0.5", 443)


def test_judge_deny_rejects_domain_resolving_private(monkeypatch):
    # 白名单域名解析到私网（DNS 被控 / 域名过期被抢注）→ 拒绝
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda h, p, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", p))])
    client = _judge_client(["evil.example.com"])
    with pytest.raises(ApiError):
        client._resolve("evil.example.com", 443)


def test_judge_deny_allows_public_domain(monkeypatch):
    # 厂商公网域名解析到公网 IP → 正常放行（不影响 seed 预设 4 家）
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda h, p, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("111.111.111.111", p))])
    client = _judge_client(["api.deepseek.com"])
    assert client._resolve("api.deepseek.com", 443) == "api.deepseek.com"


def test_judge_deny_excludes_cloud_metadata():
    # 云元数据段必须在 judge 拒绝段（与 DEFAULT_AGENT_CIDRS 剔除口径一致）
    assert "169.254.0.0/16" in JUDGE_DENY_CIDRS


def test_agent_client_no_deny_no_regression():
    # agent 路径不传 deny_cidrs（默认空）→ 白名单 host 命中直接返回，行为不变（回归护栏）
    client = build_agent_client()
    assert client._resolve("localhost", 80) == "localhost"
    assert client._resolve("127.0.0.1", 80) == "127.0.0.1"


# ---------------- P2-C4 version 严格 semver（防 tooltip XSS） ----------------
from app.api.runs import _SEMVER


def test_run_version_full_semver_accepted():
    # 合法 semver 通过
    assert _SEMVER.match("1.2.3")
    assert _SEMVER.match("10.20.300")
    assert _SEMVER.match("0.0.1")


def test_run_version_injection_suffix_rejected():
    # 原 `^\d+\.\d+\.\d+` 只验前缀，`1.2.3<script>` 等可入库 → Dashboard tooltip XSS；
    # 必须拒绝任何后缀（含 HTML/空白/换行/额外字符）
    assert not _SEMVER.match("1.2.3<script>")
    assert not _SEMVER.match("1.2.3 <img src=x onerror=alert(1)>")
    assert not _SEMVER.match("1.2.3abc")
    assert not _SEMVER.match("1.2.3\n")
    assert not _SEMVER.match("1.2.3/1")
    assert not _SEMVER.match("1.2")  # 缺 patch
    assert not _SEMVER.match("1.2.3.4")  # 超 3 段
