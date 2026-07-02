"""DNS resolution helpers for SSRF validation."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterable
from typing import Any

from markdown_ingress.core.ssrf_ip_policy import (
    is_blocked_hostname,
    is_blocked_ip_address,
    normalize_ip_for_ssrf,
)
from markdown_ingress.core.ssrf_normalization import normalize_hostname

AddressInfo = tuple[Any, Any, Any, Any, tuple[Any, ...]]
DnsResolver = Callable[..., Iterable[AddressInfo]]


def _resolve_hostname_ips_for_ssrf(
    hostname: str,
    *,
    resolver: DnsResolver = socket.getaddrinfo,
) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    """Resolve a hostname and return unique IPs for SSRF validation."""
    try:
        addr_info = resolver(hostname, None)
    except (socket.gaierror, OSError, UnicodeError) as exc:
        raise ValueError(f"URL host could not be resolved (SSRF protection): {hostname}") from exc

    resolved: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for _, _, _, _, sockaddr in addr_info:
        ip_text = str(sockaddr[0]).split("%", 1)[0]
        try:
            resolved.append(normalize_ip_for_ssrf(ipaddress.ip_address(ip_text)))
        except ValueError:
            continue

    if not resolved:
        raise ValueError(f"URL host could not be resolved (SSRF protection): {hostname}")

    unique: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    seen: set[str] = set()
    for ip in resolved:
        key = str(ip)
        if key not in seen:
            seen.add(key)
            unique.append(ip)
    return tuple(unique)


def validate_hostname_dns_ips_for_ssrf(
    hostname: str,
    *,
    allow_local: bool = False,
    resolver: DnsResolver = socket.getaddrinfo,
) -> tuple[str, ...]:
    """Resolve and validate all DNS answers for a hostname."""
    normalized = normalize_hostname(hostname)
    if not normalized:
        raise ValueError("URL must have a valid host")
    if not allow_local and is_blocked_hostname(normalized):
        raise ValueError(f"URL hostname blocked (SSRF protection): {normalized}")

    resolved_ips = _resolve_hostname_ips_for_ssrf(normalized, resolver=resolver)
    validated_ips: list[str] = []
    for resolved_ip in resolved_ips:
        if not allow_local and is_blocked_ip_address(resolved_ip):
            raise ValueError(
                f"URL hostname resolves to blocked IP (SSRF protection): "
                f"{normalized} -> {resolved_ip}"
            )
        validated_ips.append(str(resolved_ip))
    return tuple(validated_ips)


def dns_pin_matches_hostname(
    hostname: str,
    pinned_ip: str,
    *,
    allow_local: bool = False,
    resolver: DnsResolver = socket.getaddrinfo,
) -> bool:
    """Return whether an installed browser DNS pin is valid for a hostname."""
    try:
        pin = normalize_ip_for_ssrf(ipaddress.ip_address(normalize_hostname(pinned_ip)))
    except ValueError:
        return False
    if not allow_local and is_blocked_ip_address(pin):
        return False
    return str(pin) in validate_hostname_dns_ips_for_ssrf(
        hostname,
        allow_local=allow_local,
        resolver=resolver,
    )


def validate_dns_hostname_for_ssrf(
    hostname: str,
    *,
    allow_local: bool,
    resolver: DnsResolver = socket.getaddrinfo,
) -> str:
    """Return the first safe resolved IP for a DNS hostname."""
    resolved_ips = validate_hostname_dns_ips_for_ssrf(
        hostname,
        allow_local=allow_local,
        resolver=resolver,
    )
    # Return the first resolved IP to pin DNS and prevent rebinding.
    if resolved_ips:
        return resolved_ips[0]
    return hostname
