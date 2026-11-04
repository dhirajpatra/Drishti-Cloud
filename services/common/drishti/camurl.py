"""Camera URL helpers: make private RTSP cameras usable from anywhere, safely.

* normalize_url   ngrok shows a TCP tunnel as tcp://0.tcp.in.ngrok.io:12345 - Drishti wants rtsp://...:12345/<path>
* check_host      which hosts a camera may point at (never loopback, link-local/metadata or Drishti's own services)
* guard_url       the same check after DNS, right before connecting (a name must not resolve to a forbidden address)
* diagnose        staged connection test (DNS -> TCP -> RTSP answer) with a plain-language hint for the stage that failed
* redact          a URL that is safe to log or show
"""
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlsplit

DEFAULT_PORTS = {"rtsp": 554, "rtsps": 322, "http": 80, "https": 443}
# Docker-compose service names and cloud metadata names: a camera host must never reach Drishti's own plumbing.
FORBIDDEN_NAMES = frozenset({
    "localhost", "ip6-localhost", "host.docker.internal", "gateway.docker.internal", "metadata", "metadata.google.internal",
    "redis", "postgres", "postgresql", "neo4j", "gateway", "frontend", "feed-ingestor", "sam3-segmenter", "ocr-reader",
    "spark-multimodal", "wonder-reasoner", "image-alert", "action-agent", "news-context", "fridge-inventory-agent",
    "grocery-price-agent", "whatsapp-connector", "pi-edge-simulator",
})
_CGNAT = ipaddress.ip_network("100.64.0.0/10")   # Tailscale / carrier-grade NAT: this is where VPN addresses live


class HostNotAllowed(ValueError):
    pass


def normalize_url(url: str) -> str:
    """Trim and map tunnel-style schemes: tcp:// (ngrok) is the same wire protocol as rtsp://."""
    u = (url or "").strip()
    if re.match(r"^tcp://", u, re.I):
        u = "rtsp://" + u[6:]
    return u


def redact(url: str) -> str:
    return re.sub(r"//[^/@\s]+@", "//***@", url or "")


def host_kind(host: str) -> str:
    """loopback | link_local | multicast | unspecified | private | vpn | public | name"""
    h = host.strip("[]").lower()
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return "name"
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link_local"
    if ip.is_multicast:
        return "multicast"
    if ip.is_unspecified:
        return "unspecified"
    if ip.version == 4 and ip in _CGNAT:
        return "vpn"
    return "private" if ip.is_private else "public"


def check_host(host: str, allow_private: bool = True) -> None:
    """Raise HostNotAllowed for a host a camera must never use."""
    h = host.strip("[]").lower().rstrip(".")
    if not h:
        raise HostNotAllowed("host is empty")
    if h in FORBIDDEN_NAMES:
        raise HostNotAllowed(f"'{h}' is part of the Drishti server itself, not a camera")
    kind = host_kind(h)
    if kind in ("loopback", "link_local", "multicast", "unspecified"):
        raise HostNotAllowed("that address cannot be a camera (loopback, link-local or reserved)")
    if kind == "private" and not allow_private:
        raise HostNotAllowed("private LAN addresses are not reachable from this server: use the Drishti edge agent, a VPN "
                             "(Tailscale / WireGuard) or a tunnel (ngrok / cloudflared) - see docs/REMOTE_CAMERAS.md")


def resolve(host: str) -> list[str]:
    infos = socket.getaddrinfo(host.strip("[]"), None, type=socket.SOCK_STREAM)
    return sorted({i[4][0] for i in infos})


def guard_url(url: str, allow_private: bool = True) -> None:
    """Called right before connecting: check the host AND every address it resolves to."""
    u = urlsplit(normalize_url(url))
    if not u.hostname:
        return                                    # a local file path
    check_host(u.hostname, allow_private)
    if host_kind(u.hostname) != "name":
        return
    try:
        addrs = resolve(u.hostname)
    except OSError:
        return                                    # unresolvable: the connection itself will report it
    for a in addrs:
        check_host(a, allow_private)


# ------------------------------------------------------------------ staged diagnosis
def _stage(name: str, ok: bool, detail: str, hint: str = "") -> dict:
    return {"stage": name, "ok": ok, "detail": detail, **({"hint": hint} if hint else {})}


def diagnose(url: str, timeout: float = 4.0, allow_private: bool = True) -> list[dict]:
    """DNS -> TCP -> protocol answer. Stops at the first failing stage and says what to try."""
    stages: list[dict] = []
    u = urlsplit(normalize_url(url))
    scheme = (u.scheme or "").lower()
    if scheme not in DEFAULT_PORTS or not u.hostname:
        return [_stage("url", True, "local file or unsupported scheme: nothing to check on the network")]
    host, port = u.hostname, u.port or DEFAULT_PORTS[scheme]
    try:
        guard_url(url, allow_private)
    except HostNotAllowed as exc:
        return [_stage("host", False, str(exc), "Use the camera's real address, or reach it through the edge agent / VPN / tunnel.")]
    try:
        addrs = resolve(host)
        stages.append(_stage("dns", True, f"{host} -> {', '.join(addrs[:3])}"))
    except OSError:
        return [_stage("dns", False, f"cannot find '{host}'",
                       "Use the IP address, or check that the VPN / tunnel is up and the name is spelled right.")]
    try:
        sock = socket.create_connection((host.strip("[]"), port), timeout=timeout)
    except socket.timeout:
        return stages + [_stage("tcp", False, f"{host}:{port} did not answer in {timeout:g}s",
                                "No route from this server. Private IPs (192.168.x.x) are only reachable on the same network: "
                                "use the edge agent, a VPN, or a tunnel. Also check the camera is on and the port is right.")]
    except OSError as exc:
        return stages + [_stage("tcp", False, f"{host}:{port}: {exc.strerror or exc}",
                                "Reachable but nothing is listening: a tunnel that was restarted gets a NEW host and port "
                                "(update both), or the camera's RTSP service is off.")]
    stages.append(_stage("tcp", True, f"connected to {host}:{port}"))
    with sock:
        if scheme not in ("rtsp",):
            return stages
        try:
            sock.settimeout(timeout)
            sock.sendall(f"OPTIONS rtsp://{host}:{port}/ RTSP/1.0\r\nCSeq: 1\r\nUser-Agent: drishti-diagnose\r\n\r\n".encode())
            first = sock.recv(256).split(b"\r\n", 1)[0].decode("latin-1", "replace").strip()
        except OSError as exc:
            return stages + [_stage("rtsp", False, f"no RTSP answer ({exc})",
                                    "The port is open but it is not an RTSP server (wrong port, or an HTTP page).")]
    m = re.match(r"RTSP/\d\.\d\s+(\d{3})", first)
    if not m:
        return stages + [_stage("rtsp", False, f"unexpected reply: {first[:60]!r}", "That port does not speak RTSP.")]
    code = int(m.group(1))
    if code in (200, 401):
        return stages + [_stage("rtsp", True, "RTSP server answered" + (" (login required: credentials are tried next)" if code == 401 else ""))]
    return stages + [_stage("rtsp", False, f"RTSP server replied {code}", "Check the stream path (for example /stream1 or /Streaming/Channels/101).")]
