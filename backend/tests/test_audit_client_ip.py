"""P2-D20 X-Forwarded-For 校验单测（audit._client_ip：只信可信代理链右侧第 N 个值）。

原实现取 XFF 最左值——攻击者可在 XFF 头填任意 IP 污染审计日志。
现取从右数第 N 个（N=settings.xff_trusted_proxy_count，默认 2：
前端 nginx + api-gateway 各 append 一次），缺失/分段不足兜底 socket 直连 IP。

链路形态（nginx/gateway 用 $proxy_add_x_forwarded_for append）：
backend 收到 XFF = [攻击者伪造可选, 真实客户端IP, nginx容器IP] → [-2] = 真实客户端 IP。
"""
import types

from app.core.audit import _client_ip


def _req(xff=None, socket_host="10.0.0.9"):
    return types.SimpleNamespace(
        headers={"x-forwarded-for": xff} if xff else {},
        client=types.SimpleNamespace(host=socket_host),
    )


# ---------------- 默认 N=2：两层可信代理 ----------------
def test_two_proxies_takes_second_from_right():
    r = _req("1.1.1.1, 192.168.1.5, 172.18.0.2")  # 伪造, 真实客户端, nginx 容器 IP
    assert _client_ip(r) == "192.168.1.5"


def test_attacker_many_forged_values_still_real_ip():
    # 攻击者连发 4 个伪造值，nginx/gateway append 真实 IP 顶右侧 → 仍取 [-2]=真实客户端
    r = _req("a, b, c, d, 192.168.1.5, 172.18.0.2")
    assert _client_ip(r) == "192.168.1.5"


def test_blank_segments_stripped():
    r = _req("1.1.1.1, , 192.168.1.5, 172.18.0.2")  # 空段忽略后仍 [-2]=真实客户端
    assert _client_ip(r) == "192.168.1.5"


# ---------------- 兜底：socket 直连 IP（不可伪造） ----------------
def test_no_xff_falls_back_socket():
    r = _req()
    assert _client_ip(r) == "10.0.0.9"


def test_segments_less_than_count_falls_back_socket():
    # 单段（可信层数不足，无法确认来源可信）→ 宁取 socket 也不取不可信 XFF
    r = _req("192.168.1.5")
    assert _client_ip(r) == "10.0.0.9"
