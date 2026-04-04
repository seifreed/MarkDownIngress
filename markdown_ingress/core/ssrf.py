"""Shared SSRF validation helpers for HTTP-facing entrypoints."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

_EXTRA_BLOCKED_NETWORKS = (
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("fec0::/10"),
)

_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata.google.internal",
        "metadata.azure.internal",
        "metadata.azure.net",
        "169.254.169.254",
    }
)


def normalize_hostname(hostname: str) -> str:
    """Normalize hostnames before SSRF checks."""
    return hostname.strip().rstrip(".").lower()


def normalize_ip_for_ssrf(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Collapse IPv4-mapped IPv6 addresses to IPv4 for network checks."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def is_blocked_hostname(hostname: str) -> bool:
    """Return whether a hostname is explicitly blocked."""
    return normalize_hostname(hostname) in _BLOCKED_HOSTNAMES


def is_blocked_ip_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return whether an IP belongs to a non-routable or internal range."""
    normalized = normalize_ip_for_ssrf(ip)
    if (
        normalized.is_private
        or normalized.is_loopback
        or normalized.is_link_local
        or normalized.is_multicast
        or normalized.is_reserved
        or normalized.is_unspecified
    ):
        return True
    return any(
        normalized in network
        for network in _EXTRA_BLOCKED_NETWORKS
        if network.version == normalized.version
    )


def validate_hostname_for_ssrf(hostname: str, *, allow_local: bool = False) -> str:
    """Validate a hostname or IP literal for SSRF safety and return its normalized form."""
    normalized = normalize_hostname(hostname)
    if not normalized:
        raise ValueError("URL must have a valid host")

    if allow_local:
        return normalized

    if is_blocked_hostname(normalized):
        raise ValueError(f"URL hostname blocked (SSRF protection): {normalized}")

    try:
        ip = normalize_ip_for_ssrf(ipaddress.ip_address(normalized))
    except ValueError:
        return normalized

    if is_blocked_ip_address(ip):
        raise ValueError(f"URL IP in blocked range (SSRF protection): {ip}")

    return normalized


def validate_http_url_no_ssrf(url: str, *, allow_local: bool = False) -> str:
    """Validate an HTTP(S) URL against common SSRF destinations."""
    normalized_url = str(url).strip()
    if not normalized_url:
        raise ValueError("URL cannot be empty")

    try:
        parsed = urlsplit(normalized_url)
    except Exception as exc:
        raise ValueError(f"Invalid URL format: {url!r}: {exc}") from exc

    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(
            f"Invalid URL scheme: {scheme!r}. Only http and https are allowed. URL: {url!r}"
        )

    validate_hostname_for_ssrf(parsed.hostname or "", allow_local=allow_local)
    return normalized_url
