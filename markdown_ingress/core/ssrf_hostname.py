"""Hostname literal validation helpers for SSRF checks."""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable

from markdown_ingress.core.ssrf_ip_policy import (
    is_blocked_hostname,
    is_blocked_ip_address,
    normalize_ip_for_ssrf,
)

_NUMERIC_IP_LITERAL_RE = re.compile(r"0x[0-9a-fA-F]+|0[0-7]+|\d+")


def validate_hostname_literal_for_ssrf(hostname: str) -> str | None:
    """Validate IP-like hostnames; return a pinned IP when one is found."""
    ip = _parse_ip_literal_for_ssrf(hostname)
    if ip is not None:
        return _validate_ip_literal_for_ssrf(ip)

    _raise_for_numeric_ip_literal_hostname(hostname)
    return _validate_compressed_ipv4_hostname(hostname)


def validate_unresolved_hostname_for_ssrf(hostname: str) -> str:
    if is_blocked_hostname(hostname):
        raise ValueError(f"URL hostname blocked (SSRF protection): {hostname}") from None
    return hostname


def validate_non_ip_hostname_for_ssrf(
    hostname: str,
    *,
    allow_local: bool,
    resolve_dns: bool,
    validate_dns_hostname: Callable[[str], str],
) -> str:
    literal = validate_hostname_literal_for_ssrf(hostname)
    if literal is not None:
        return literal
    if not resolve_dns:
        return validate_unresolved_hostname_for_ssrf(hostname)
    return validate_dns_hostname(hostname)


def _parse_ip_literal_for_ssrf(
    hostname: str,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return normalize_ip_for_ssrf(ipaddress.ip_address(hostname))
    except ValueError:
        return None


def _validate_ip_literal_for_ssrf(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> str:
    if is_blocked_ip_address(ip):
        raise ValueError(f"URL IP in blocked range (SSRF protection): {ip}")
    return str(ip)


def _raise_for_numeric_ip_literal_hostname(hostname: str) -> None:
    if _NUMERIC_IP_LITERAL_RE.fullmatch(hostname):
        raise ValueError(
            "URL hostname looks like a numeric IP literal and is blocked "
            f"(SSRF protection): {hostname}"
        )


def _validate_compressed_ipv4_hostname(hostname: str) -> str | None:
    if "." not in hostname or hostname.endswith("."):
        return None

    try:
        raw = socket.inet_aton(hostname)
    except OSError:
        return None

    packed_ip = normalize_ip_for_ssrf(ipaddress.ip_address(int.from_bytes(raw, "big")))
    if is_blocked_ip_address(packed_ip):
        raise ValueError(
            f"URL hostname is a compressed IPv4 blocked by SSRF protection: "
            f"{hostname} -> {packed_ip}"
        )
    return str(packed_ip)
