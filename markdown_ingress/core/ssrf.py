"""Shared SSRF validation helpers for HTTP-facing entrypoints."""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import socket
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)

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
        "0.0.0.0",
        "::1",                # IPv6 loopback
        "0:0:0:0:0:0:0:1",    # IPv6 loopback (long form)
    }
)

_ALLOW_LOCAL_TRUE_VALUES = frozenset({"true", "1", "yes", "on", "enabled"})
_ALLOW_LOCAL_FALSE_VALUES = frozenset({"false", "0", "no", "off", "disabled"})


def normalize_hostname(hostname: str) -> str:
    """Normalize hostnames before SSRF checks.

    For IPv6 literals, strips brackets and normalizes via ipaddress module
    to a canonical form so that all representations of the same address match.
    """
    stripped = hostname.strip().rstrip(".").lower()
    # Strip URL-style brackets around IPv6 addresses
    if stripped.startswith("[") and stripped.endswith("]"):
        stripped = stripped[1:-1]
    # Attempt IPv6/IPv4 canonical normalization
    try:
        ip = ipaddress.ip_address(stripped)
        return str(ip)
    except ValueError:
        return stripped


def resolve_allow_local_urls(allow_local_urls: bool | None) -> bool:
    """Resolve explicit allow-local input with the env-based fallback."""
    if allow_local_urls is not None:
        return bool(allow_local_urls)

    raw = os.getenv("MDI_ALLOW_LOCAL_URLS")
    if raw is None:
        return False

    normalized = raw.strip().lower()
    if normalized in _ALLOW_LOCAL_TRUE_VALUES:
        return True
    if normalized in _ALLOW_LOCAL_FALSE_VALUES:
        return False

    logger.warning(
        "Invalid MDI_ALLOW_LOCAL_URLS=%r for SSRF resolution; defaulting to False.",
        raw,
    )
    return False


def normalize_domain_pattern(raw_domain: str) -> str:
    """Normalize a domain policy value for matching and identity keys.

    Accepts hostnames, host:port values, bracketed IPv6, and URL-like inputs
    such as ``https://example.com/path`` by extracting the host component.
    """
    normalized_raw = raw_domain.strip().lstrip(".").rstrip(".")
    if not normalized_raw:
        return ""

    if "://" in normalized_raw:
        return normalize_hostname(urlsplit(normalized_raw).hostname or "")

    host_part = normalized_raw.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]

    if not host_part:
        return ""
    if normalized_raw.startswith("["):
        return normalize_hostname(urlsplit(f"//{host_part}").hostname or "")
    if host_part.count(":") == 1:
        return normalize_hostname(host_part.rsplit(":", 1)[0])
    return normalize_hostname(host_part)


def normalize_url_for_identity(url: str) -> str:
    """Normalize the hostname portion of an HTTP URL for cache and dedupe keys.

    This keeps path, query, userinfo, and port intact, discards fragment, while folding
    host variants such as trailing dots and case differences into one identity.
    If parsing fails or the URL has no host, the original string is returned.
    """
    normalized_url = str(url).strip()
    try:
        parsed = urlsplit(normalized_url)
    except Exception:
        logger.warning(
            "URL normalization failed for %r, skipping SSRF checks", normalized_url, exc_info=True
        )
        return normalized_url

    hostname = parsed.hostname
    if not hostname:
        return normalized_url

    normalized_hostname = normalize_hostname(hostname)
    if not normalized_hostname:
        return normalized_url

    try:
        port = parsed.port
    except ValueError:
        return normalized_url

    scheme = parsed.scheme.lower()
    if (scheme, port) in {("http", 80), ("https", 443)}:
        port = None

    if parsed.username is None:
        userinfo = ""
    elif parsed.password is None:
        userinfo = f"{parsed.username}@"
    else:
        userinfo = f"{parsed.username}:{parsed.password}@"

    if ":" in normalized_hostname and not normalized_hostname.startswith("["):
        host = f"[{normalized_hostname}]"
    else:
        host = normalized_hostname

    netloc = f"{userinfo}{host}"
    if port is not None:
        netloc = f"{netloc}:{port}"

    path = parsed.path or "/"
    return urlunsplit((scheme, netloc, path, parsed.query, ""))


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

    try:
        ip = normalize_ip_for_ssrf(ipaddress.ip_address(normalized))
    except ValueError:
        # Reject numeric-looking hostnames that some HTTP clients resolve as IPs
        # (decimal IPv4 like 2130706433, hex like 0x7f000001, octal like 017700000001)
        if re.fullmatch(r"0x[0-9a-fA-F]+|0[0-7]+|\d+", normalized):
            raise ValueError(
                f"URL hostname looks like a numeric IP literal and is blocked (SSRF protection): {normalized}"
            )
        # Catch compressed IPv4 like "127.1" or "10.0.1" that inet_aton accepts
        # but ipaddress.ip_address() rejects (2- or 3-part dotted decimal).
        if "." in normalized and not normalized.endswith("."):
            import socket

            try:
                raw = socket.inet_aton(normalized)
                packed_ip = normalize_ip_for_ssrf(ipaddress.ip_address(int.from_bytes(raw, "big")))
                if is_blocked_ip_address(packed_ip):
                    raise ValueError(
                        f"URL hostname is a compressed IPv4 blocked by SSRF protection: "
                        f"{normalized} -> {packed_ip}"
                    )
                return str(packed_ip)
            except OSError:
                pass
        if not resolve_dns:
            if normalized.lower() in _BLOCKED_HOSTNAMES:
                raise ValueError(f"URL hostname blocked (SSRF protection): {normalized}")
            return normalized
        resolved_ips = _resolve_hostname_ips_for_ssrf(normalized)
        for resolved_ip in resolved_ips:
            if is_blocked_ip_address(resolved_ip):
                raise ValueError(
                    f"URL hostname resolves to blocked IP (SSRF protection): {normalized} -> {resolved_ip}"
                )
        # Return the first resolved IP to pin DNS and prevent rebinding
        if resolved_ips:
            return str(resolved_ips[0])
        return normalized

    if is_blocked_ip_address(ip):
        raise ValueError(f"URL IP in blocked range (SSRF protection): {ip}")

    return str(ip)


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
    normalized_url = str(url).strip()
    if not normalized_url:
        raise ValueError("URL cannot be empty")

    # BUG FIX: Check for CRLF injection characters that could be used for header injection
    if "\r" in normalized_url or "\n" in normalized_url:
        raise ValueError(f"URL contains forbidden CRLF characters: {url!r}")

    # BUG FIX: Check for null bytes that could be used to bypass validation
    if "\x00" in normalized_url:
        raise ValueError(f"URL contains null byte: {url!r}")

    try:
        parsed = urlsplit(normalized_url)
    except Exception as exc:
        raise ValueError(f"Invalid URL format: {url!r}: {exc}") from exc

    # BUG FIX: Validate that URL has a network location (netloc)
    # URLs like "http:///example.com" (triple slash) have empty netloc
    if not parsed.netloc:
        raise ValueError(f"URL must have a valid network location: {url!r}")

    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(
            f"Invalid URL scheme: {scheme!r}. Only http and https are allowed. URL: {url!r}"
        )

    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"Invalid URL port: {url!r}: {exc}") from exc
    if port is not None and (port < 1 or port > 65535):
        raise ValueError(f"Invalid URL port: {url!r}. Port must be between 1 and 65535")

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
        # Replace the hostname in the URL with the validated IP
        if ":" in validated_target:
            # IPv6 address — bracket it in the URL
            new_netloc = f"[{validated_target}]"
        else:
            new_netloc = validated_target
        # Preserve port and auth info in netloc
        if parsed.port:
            new_netloc = f"{new_netloc}:{parsed.port}"
        if parsed.username:
            auth = parsed.username
            if parsed.password:
                auth = f"{auth}:{parsed.password}"
            new_netloc = f"{auth}@{new_netloc}"
        reconstructed = urlunsplit(
            (parsed.scheme, new_netloc, parsed.path, parsed.query, parsed.fragment)
        )
        return reconstructed

    return normalized_url


def validate_http_url_no_ssrf_with_dns_check(
    url: str,
    *,
    allow_local: bool = False,
) -> str:
    """Validate URL syntax and DNS-resolved targets while preserving the logical URL.

    Some callers, notably browser renderers, cannot safely consume the IP-pinned
    URL returned by ``validate_http_url_no_ssrf(resolve_dns=True)`` because doing
    so would break normal hostname-based browser behavior. They still need the
    DNS deny check so hostnames resolving to private/loopback ranges are blocked.
    """
    normalized_url = str(url).strip()
    validate_http_url_no_ssrf(
        normalized_url,
        allow_local=allow_local,
        resolve_dns=True,
    )
    return normalized_url


def validated_http_url_requires_dns_pinning(original_url: str, validated_url: str) -> bool:
    """Return True when SSRF validation rewrote the URL host to a pinned address."""
    try:
        original_hostname = normalize_hostname(urlsplit(str(original_url).strip()).hostname or "")
        validated_hostname = normalize_hostname(urlsplit(str(validated_url).strip()).hostname or "")
    except Exception:
        return True
    if not original_hostname or not validated_hostname:
        return True
    return original_hostname != validated_hostname
