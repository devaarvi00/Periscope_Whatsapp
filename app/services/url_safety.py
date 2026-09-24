"""Outbound URL validation (SSRF protection).

Two policies:
- ``assert_public_url``: for URLs supplied by users that the platform (or
  WAHA on our behalf) will fetch/POST to — media URLs, outbound webhooks.
  Only http(s), and every resolved address must be publicly routable.
- ``validate_waha_base_url``: for per-phone WAHA servers. WAHA normally
  lives on localhost / a private Docker network, so private ranges are
  allowed, but link-local (cloud metadata) targets are rejected.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

_METADATA_HOSTS = {
    "metadata", "metadata.google.internal", "metadata.goog",
    "instance-data", "instance-data.ec2.internal",
}


class UnsafeURLError(ValueError):
    pass


def _split(url: str) -> tuple[str, str, int]:
    if not isinstance(url, str) or not url.strip():
        raise UnsafeURLError("URL is empty")
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise UnsafeURLError("URL must start with http:// or https://")
    host = (parts.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise UnsafeURLError("URL has no host")
    if parts.username or parts.password:
        raise UnsafeURLError("URLs with embedded credentials are not allowed")
    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise UnsafeURLError("URL has an invalid port") from exc
    return scheme, host, port


def _resolve(host: str, port: int) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError) as exc:
        raise UnsafeURLError(f"Could not resolve host '{host}'") from exc
    addrs = []
    for info in infos:
        raw = info[4][0]
        ip = ipaddress.ip_address(raw.split("%", 1)[0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        addrs.append(ip)
    if not addrs:
        raise UnsafeURLError(f"Could not resolve host '{host}'")
    return addrs


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
        or ip.is_multicast or ip.is_unspecified or not ip.is_global
    )


def check_public_url(url: str) -> str:
    """Blocking check; raises UnsafeURLError. Returns the stripped URL."""
    _, host, port = _split(url)
    if host in _METADATA_HOSTS or host == "localhost" or host.endswith(".localhost"):
        raise UnsafeURLError("URL points to a non-public host")
    for ip in _resolve(host, port):
        if not _is_public(ip):
            raise UnsafeURLError("URL points to a private or reserved address")
    return url.strip()


async def assert_public_url(url: str) -> str:
    """Async wrapper (DNS resolution happens in a worker thread)."""
    return await asyncio.to_thread(check_public_url, url)


def validate_waha_base_url(url: str) -> str:
    """Allow http(s) WAHA servers on public or private networks, but never
    link-local / cloud-metadata targets. Returns the normalised URL."""
    _, host, port = _split(url)
    if host in _METADATA_HOSTS:
        raise UnsafeURLError("WAHA URL points to a metadata endpoint")
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
        candidates = [literal]
    except ValueError:
        try:
            candidates = _resolve(host, port)
        except UnsafeURLError:
            # Unresolvable right now (e.g. docker hostname not up yet) — allow.
            # The global WAHA key is never sent to a non-global base URL
            # (see WAHAService.from_phone), so this cannot leak it.
            candidates = []
    for ip in candidates:
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            raise UnsafeURLError("WAHA URL points to a link-local or reserved address")
    return url.strip().rstrip("/")
