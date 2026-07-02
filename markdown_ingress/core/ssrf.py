"""Shared SSRF validation helpers for HTTP-facing entrypoints."""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlsplit

from markdown_ingress.core.ssrf_hostname import (
    validate_hostname_literal_for_ssrf,
    validate_non_ip_hostname_for_ssrf,
)
from markdown_ingress.core.ssrf_ip_policy import (
    is_blocked_hostname as is_blocked_hostname,
)
from markdown_ingress.core.ssrf_ip_policy import (
    is_blocked_ip_address as is_blocked_ip_address,
)
from markdown_ingress.core.ssrf_ip_policy import (
    normalize_ip_for_ssrf as normalize_ip_for_ssrf,
)
from markdown_ingress.core.ssrf_normalization import (
    normalize_domain_pattern as normalize_domain_pattern,
)
from markdown_ingress.core.ssrf_normalization import (
    normalize_hostname as normalize_hostname,
)
from markdown_ingress.core.ssrf_normalization import (
    normalize_url_for_identity as normalize_url_for_identity,
)
from markdown_ingress.core.ssrf_normalization import (
    resolve_allow_local_urls as resolve_allow_local_urls,
)
from markdown_ingress.core.ssrf_url import (
    normalize_http_url_text,
    reconstruct_url_with_pinned_target,
    split_http_url_for_ssrf,
    validate_http_url_authority,
)

logger = logging.getLogger(__name__)


def _resolve_hostname_ips_for_ssrf(
    hostname: str,
) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    """Resolve a hostname and return unique IPs for SSRF validation."""
    try:
        addr_info = socket.getaddrinfo(hostname, None)
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
) -> tuple[str, ...]:
    """Resolve and validate all DNS answers for a hostname."""
    normalized = normalize_hostname(hostname)
    if not normalized:
        raise ValueError("URL must have a valid host")
    if not allow_local and is_blocked_hostname(normalized):
        raise ValueError(f"URL hostname blocked (SSRF protection): {normalized}")

    resolved_ips = _resolve_hostname_ips_for_ssrf(normalized)
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
    )


def _validate_dns_hostname_for_ssrf(hostname: str, *, allow_local: bool) -> str:
    resolved_ips = validate_hostname_dns_ips_for_ssrf(hostname, allow_local=allow_local)
    # Return the first resolved IP to pin DNS and prevent rebinding
    if resolved_ips:
        return resolved_ips[0]
    return hostname


def validate_hostname_for_ssrf(
    hostname: str,
    *,
    allow_local: bool = False,
    resolve_dns: bool = True,
) -> str:
    """Validate a hostname or IP literal for SSRF safety and return a safe address.

    When DNS resolution is performed, returns the first resolved IP address
    instead of the hostname to prevent DNS rebinding attacks (where the hostname
    resolves to a different IP between validation time and request time).
    Callers should use the returned value as the target for HTTP requests
    and set the original hostname in the Host header.
    """
    normalized = normalize_hostname(hostname)
    if not normalized:
        raise ValueError("URL must have a valid host")

    if allow_local:
        return normalized

    if is_blocked_hostname(normalized):
        raise ValueError(f"URL hostname blocked (SSRF protection): {normalized}")

    literal = validate_hostname_literal_for_ssrf(normalized)
    if literal is not None:
        return literal
    return validate_non_ip_hostname_for_ssrf(
        normalized,
        allow_local=allow_local,
        resolve_dns=resolve_dns,
        validate_dns_hostname=lambda host: _validate_dns_hostname_for_ssrf(
            host,
            allow_local=allow_local,
        ),
    )


def validate_http_url_no_ssrf(
    url: str,
    *,
    allow_local: bool = False,
    resolve_dns: bool = True,
) -> str:
    """Validate an HTTP(S) URL against common SSRF destinations.

    BUG FIX: Also validates URL for CRLF injection and null bytes to prevent
    header injection attacks via malformed URLs.
    """
    normalized_url = normalize_http_url_text(url)
    parsed = split_http_url_for_ssrf(normalized_url, url)
    port = validate_http_url_authority(parsed, url)
    original_hostname = parsed.hostname or ""
    validated_target = validate_hostname_for_ssrf(
        original_hostname,
        allow_local=allow_local,
        resolve_dns=resolve_dns,
    )

    # If DNS was resolved and an IP was returned, reconstruct the URL with the
    # IP-pinned target to prevent DNS rebinding attacks. The original hostname
    # is preserved as the Host header hint for the caller.
    if validated_target != original_hostname and resolve_dns:
        return reconstruct_url_with_pinned_target(
            parsed,
            validated_target=validated_target,
            port=port,
        )

    return normalized_url


def validate_http_url_no_ssrf_with_dns_check(
    url: str,
    *,
    allow_local: bool = False,
) -> str:
    """Validate URL syntax and DNS-resolved targets, returning the pinned URL."""
    normalized_url = str(url).strip()
    return validate_http_url_no_ssrf(
        normalized_url,
        allow_local=allow_local,
        resolve_dns=True,
    )


def dns_pin_for_validated_http_url(
    original_url: str,
    validated_url: str,
) -> tuple[str, str] | None:
    """Return ``(logical_hostname, pinned_ip)`` when validation rewrote the host."""
    try:
        original_hostname = normalize_hostname(urlsplit(str(original_url).strip()).hostname or "")
        validated_hostname = normalize_hostname(urlsplit(str(validated_url).strip()).hostname or "")
    except ValueError:
        return None
    if not original_hostname or not validated_hostname:
        return None
    if original_hostname == validated_hostname:
        return None
    return original_hostname, validated_hostname
