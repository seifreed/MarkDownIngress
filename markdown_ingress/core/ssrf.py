"""Shared SSRF validation helpers for HTTP-facing entrypoints."""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from urllib.parse import SplitResult, urlsplit, urlunsplit

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

logger = logging.getLogger(__name__)

_UNSPECIFIED_IPV4_HOST = ".".join(("0", "0", "0", "0"))
_NUMERIC_IP_LITERAL_RE = re.compile(r"0x[0-9a-fA-F]+|0[0-7]+|\d+")

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
        "ip6-localhost",
        "ip6-loopback",
        "metadata.google.internal",
        "metadata.azure.internal",
        "metadata.azure.net",
        "metadata.oracle.internal",
        "instance-data.ec2.internal",
        "metadata.packet.net",
        "metadata.scaleway.internal",
        "metadata.aliyun.internal",
        "169.254.169.254",
        _UNSPECIFIED_IPV4_HOST,
        "::1",  # IPv6 loopback
        "0:0:0:0:0:0:0:1",  # IPv6 loopback (long form)
    }
)


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


def _validate_unresolved_hostname_for_ssrf(hostname: str) -> str:
    if hostname.lower() in _BLOCKED_HOSTNAMES:
        raise ValueError(f"URL hostname blocked (SSRF protection): {hostname}") from None
    return hostname


def _validate_dns_hostname_for_ssrf(hostname: str, *, allow_local: bool) -> str:
    resolved_ips = validate_hostname_dns_ips_for_ssrf(hostname, allow_local=allow_local)
    # Return the first resolved IP to pin DNS and prevent rebinding
    if resolved_ips:
        return resolved_ips[0]
    return hostname


def _validate_non_ip_hostname_for_ssrf(
    hostname: str,
    *,
    allow_local: bool,
    resolve_dns: bool,
) -> str:
    # Reject numeric-looking hostnames that some HTTP clients resolve as IPs
    # (decimal IPv4 like 2130706433, hex like 0x7f000001, octal like 017700000001)
    _raise_for_numeric_ip_literal_hostname(hostname)

    # Catch compressed IPv4 like "127.1" or "10.0.1" that inet_aton accepts
    # but ipaddress.ip_address() rejects (2- or 3-part dotted decimal).
    compressed_ip = _validate_compressed_ipv4_hostname(hostname)
    if compressed_ip is not None:
        return compressed_ip

    if not resolve_dns:
        return _validate_unresolved_hostname_for_ssrf(hostname)
    return _validate_dns_hostname_for_ssrf(hostname, allow_local=allow_local)


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

    ip = _parse_ip_literal_for_ssrf(normalized)
    if ip is not None:
        return _validate_ip_literal_for_ssrf(ip)
    return _validate_non_ip_hostname_for_ssrf(
        normalized,
        allow_local=allow_local,
        resolve_dns=resolve_dns,
    )


def _normalize_http_url_text(url: str) -> str:
    normalized_url = str(url).strip()
    if not normalized_url:
        raise ValueError("URL cannot be empty")

    # BUG FIX: Check for CRLF injection characters that could be used for header injection
    if "\r" in normalized_url or "\n" in normalized_url:
        raise ValueError(f"URL contains forbidden CRLF characters: {url!r}")

    # BUG FIX: Check for null bytes that could be used to bypass validation
    if "\x00" in normalized_url:
        raise ValueError(f"URL contains null byte: {url!r}")
    return normalized_url


def _split_http_url_for_ssrf(normalized_url: str, original_url: str) -> SplitResult:
    try:
        return urlsplit(normalized_url)
    except Exception as exc:
        raise ValueError(f"Invalid URL format: {original_url!r}: {exc}") from exc


def _validate_http_url_authority(parsed: SplitResult, original_url: str) -> int | None:
    # BUG FIX: Validate that URL has a network location (netloc)
    # URLs like "http:///example.com" (triple slash) have empty netloc
    if not parsed.netloc:
        raise ValueError(f"URL must have a valid network location: {original_url!r}")

    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(
            f"Invalid URL scheme: {scheme!r}. Only http and https are allowed. "
            f"URL: {original_url!r}"
        )

    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"Invalid URL port: {original_url!r}: {exc}") from exc
    if port is not None and (port < 1 or port > 65535):
        raise ValueError(f"Invalid URL port: {original_url!r}. Port must be between 1 and 65535")
    return port


def _build_pinned_url_netloc(
    parsed: SplitResult,
    validated_target: str,
    port: int | None,
) -> str:
    if ":" in validated_target:
        new_netloc = f"[{validated_target}]"
    else:
        new_netloc = validated_target

    if port is not None:
        new_netloc = f"{new_netloc}:{port}"

    if parsed.username:
        auth = parsed.username
        if parsed.password:
            auth = f"{auth}:{parsed.password}"
        new_netloc = f"{auth}@{new_netloc}"
    return new_netloc


def _reconstruct_url_with_pinned_target(
    parsed: SplitResult,
    *,
    validated_target: str,
    port: int | None,
) -> str:
    new_netloc = _build_pinned_url_netloc(parsed, validated_target, port)
    return urlunsplit((parsed.scheme, new_netloc, parsed.path, parsed.query, parsed.fragment))


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
    normalized_url = _normalize_http_url_text(url)
    parsed = _split_http_url_for_ssrf(normalized_url, url)
    port = _validate_http_url_authority(parsed, url)
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
        return _reconstruct_url_with_pinned_target(
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
    except Exception:
        return None
    if not original_hostname or not validated_hostname:
        return None
    if original_hostname == validated_hostname:
        return None
    return original_hostname, validated_hostname


def validated_http_url_requires_dns_pinning(original_url: str, validated_url: str) -> bool:
    """Return True when SSRF validation rewrote the URL host to a pinned address."""
    try:
        original_hostname = normalize_hostname(urlsplit(str(original_url).strip()).hostname or "")
        validated_hostname = normalize_hostname(urlsplit(str(validated_url).strip()).hostname or "")
    except Exception:
        return True
    if not original_hostname or not validated_hostname:
        return True
    return dns_pin_for_validated_http_url(original_url, validated_url) is not None
