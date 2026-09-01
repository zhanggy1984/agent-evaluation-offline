"""统一出站 HTTP 客户端（SSRF 防护，§九-1）。

核心机制：解析 → 校验全部 IP 在白名单内 → 用校验过的 IP 直连并透传 Host 头（禁止二次解析，防 DNS rebinding）→ 禁 redirect（防重定向绕过）。

分两类白名单：
- agent 出站（base_url）：内网 CIDR 白名单 + 明确 host（localhost/host.docker.internal 等）
- judge 出站（llm_profile.base_url）：独立 llm_allowlist（模型厂商域名/IP），不套 agent 内网白名单
allowlist 未配置默认 deny。
"""
import ipaddress
import socket
from typing import Iterable

import httpx

from app.core.errors import ApiError, E_VALIDATION


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def build_networks(cidrs: Iterable[str]) -> list:
    nets = []
    for cidr in cidrs:
        if not cidr.strip():
            continue
        if "/" not in cidr:
            # 裸 IP 视为单个地址
            net = ipaddress.ip_network(f"{cidr.strip()}/32", strict=False)
        else:
            net = ipaddress.ip_network(cidr.strip(), strict=False)
        # 7.6 A4：prefixlen==0（0.0.0.0/0、::/0）即全地址放行 → 直接拒绝
        if net.prefixlen == 0:
            raise ApiError(E_VALIDATION, f"SSRF: 全地址段放行禁止（{cidr}）")
        nets.append(net)
    return nets


# P1-2：连接池显式配置。agent 出站 client 每 run 独立（orchestrator._run 建一次复用），
# 并发连接数 ≈ run 内 inflight（默认 global_max_inflight=16），64 留 4x 余量防流式请求互相
# 挤占；judge 出站 client 每 worker 重建、并发更低，同池足够。池耗尽（PoolTimeout）不再
# 依赖 httpx 默认隐式值，executor 据此分类为 pool_error（与上游慢 timeout 区分）。
DEFAULT_OUTBOUND_LIMITS = httpx.Limits(max_connections=64, max_keepalive_connections=16)


class AllowlistAsyncClient(httpx.AsyncClient):
    """解析→校验→IP 直连+透传 Host 的出站客户端。follow_redirects 固定 False。"""

    def __init__(self, allow_hosts: Iterable[str], allow_cidrs: Iterable[str],
                 deny_cidrs: Iterable[str] = (), timeout: float = 60.0, **kwargs):
        kwargs.setdefault("follow_redirects", False)
        kwargs.setdefault("limits", DEFAULT_OUTBOUND_LIMITS)
        super().__init__(timeout=timeout, **kwargs)
        self._allow_hosts = {h.strip() for h in allow_hosts if h.strip()}
        self._networks = build_networks(allow_cidrs)
        # P2-C1：deny 黑名单（judge 出站用，拒绝内网/云元数据段）。agent 不传 → 空，行为不变。
        self._deny_networks = build_networks(deny_cidrs)

    def _check_denied(self, host: str, port: int) -> None:
        """deny 校验：host（IP 或域名）解析出的全部 IP 命中拒绝段 → 拒绝。deny 空直接放行。"""
        if not self._deny_networks:
            return
        if _is_ip(host):
            ips = {host}
        else:
            try:
                infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            except socket.gaierror:
                raise ApiError(E_VALIDATION, f"SSRF: {host} 无法解析")
            ips = {info[4][0].split("%")[0] for info in infos}
        for ip_str in ips:
            ip_obj = ipaddress.ip_address(ip_str)
            if any(ip_obj in net for net in self._deny_networks):
                raise ApiError(E_VALIDATION, f"SSRF: {host} 命中拒绝段（内网/元数据）IP {ip_str}")

    def _resolve(self, host: str, port: int) -> str:
        """返回可直连的 host：白名单 host 原样返回；否则解析并校验 IP 后返回 IP。"""
        if host in self._allow_hosts:
            # P2-C1：白名单 host 命中也要过 deny 校验（judge 出站主路径，防 admin 热改白名单指内网/元数据）
            self._check_denied(host, port)
            return host
        if _is_ip(host):
            ip_obj = ipaddress.ip_address(host)
            if not self._networks or not any(ip_obj in net for net in self._networks):
                raise ApiError(E_VALIDATION, f"SSRF: {host} 不在出站白名单")
            return host
        # 解析：getaddrinfo 全部结果都校验（防部分 IP 逃逸）
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror:
            raise ApiError(E_VALIDATION, f"SSRF: {host} 无法解析")
        ips = {info[4][0].split("%")[0] for info in infos}
        if not ips:
            raise ApiError(E_VALIDATION, f"SSRF: {host} 无解析结果")
        for ip_str in ips:
            ip_obj = ipaddress.ip_address(ip_str)
            if not self._networks or not any(ip_obj in net for net in self._networks):
                raise ApiError(E_VALIDATION, f"SSRF: {host} 解析到非白名单 IP {ip_str}")
        return ips.pop()

    async def send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        host = request.url.host
        port = request.url.port or (443 if request.url.scheme == "https" else 80)
        resolved = self._resolve(host, port)
        if resolved != host:
            new_url = request.url.copy_with(host=resolved)
            new_headers = dict(request.headers)
            new_headers["Host"] = host  # 透传原始 Host，业务侧无感
            request = httpx.Request(
                request.method, new_url, headers=new_headers,
                content=request.content, extensions=request.extensions,
            )
        return await super().send(request, **kwargs)


# ---- 默认白名单：允许访问宿主机 agent（docker-compose extra_hosts 映射） ----
DEFAULT_AGENT_HOSTS = ("localhost", "127.0.0.1", "host.docker.internal")
# 内网保留段（SSRF 只放行这些网段内的地址）
# 7.6 A4：移除 169.254.0.0/16（云元数据段 metadata 169.254.169.254，严禁放行；
# 容器默认网关实际在 172.16.0.0/12 内，host.docker.internal 映射 host 网关，不受影响）
DEFAULT_AGENT_CIDRS = (
    "127.0.0.0/8",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
)

# ---- judge 出站拒绝段：只放行公网模型厂商，内网保留段/云元数据段一律拒绝 ----
# P2-C1：judge 用「域名白名单（llm_allowlist）+ IP 黑名单」组合；agent 用内网 CIDR 白名单，
# 语义相反（judge 目标是公网），故独立常量，不能复用 DEFAULT_AGENT_CIDRS。
JUDGE_DENY_CIDRS = (
    "0.0.0.0/8", "127.0.0.0/8", "169.254.0.0/16",    # 本机回环 / 云元数据段（metadata 169.254.169.254）
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",  # 私网
    "100.64.0.0/10",                                 # CGNAT
    "198.18.0.0/15", "224.0.0.0/4", "240.0.0.0/4",    # 保留/组播
    "::1/128", "fc00::/7", "fe80::/10", "::ffff:0:0/96",  # IPv6 环回/ULA/link-local/IPv4 映射
)


def build_agent_client(extra_hosts: Iterable[str] = (), extra_cidrs: Iterable[str] = ()) -> AllowlistAsyncClient:
    """agent 出站客户端（内网白名单）。allowlist 由调用方从 system_config 读取传入。"""
    return AllowlistAsyncClient(
        allow_hosts=[*DEFAULT_AGENT_HOSTS, *extra_hosts],
        allow_cidrs=[*DEFAULT_AGENT_CIDRS, *extra_cidrs],
        timeout=60.0,
    )


def validate_base_url(url_str: str, cidrs: Iterable[str] = ()) -> None:
    """agent 注册时的 base_url 白名单校验（§九-1）：解析全部 IP 校验内网段，防 SSRF。

    返回前不建立连接；出站时由 AllowlistAsyncClient 再次解析校验（防注册后 DNS 变化）。
    """
    import urllib.parse
    parsed = urllib.parse.urlparse(url_str)
    if parsed.scheme not in ("http", "https"):
        raise ApiError(E_VALIDATION, f"base_url 仅支持 http/https: {url_str}")
    host = parsed.hostname
    if not host:
        raise ApiError(E_VALIDATION, "base_url 缺少 host")
    # T15：urllib.parse 对非法端口（超 0-65535 或非数字）在访问 .port 时才抛 ValueError，
    # 不捕获会冒泡成 500。合法 URL 校验失败应回 4xx，故此处转成校验错误。
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        raise ApiError(E_VALIDATION, f"base_url 端口非法: {url_str}")
    networks = build_networks([*DEFAULT_AGENT_CIDRS, *cidrs])
    if host in DEFAULT_AGENT_HOSTS:
        return
    if _is_ip(host):
        ip_obj = ipaddress.ip_address(host)
        if any(ip_obj in net for net in networks):
            return
        raise ApiError(E_VALIDATION, f"SSRF: {host} 不在内网白名单")
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise ApiError(E_VALIDATION, f"SSRF: {host} 无法解析")
    ips = {info[4][0].split("%")[0] for info in infos}
    for ip_str in ips:
        ip_obj = ipaddress.ip_address(ip_str)
        if not any(ip_obj in net for net in networks):
            raise ApiError(E_VALIDATION, f"SSRF: {host} 解析到非白名单 IP {ip_str}")
