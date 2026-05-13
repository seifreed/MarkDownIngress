"""Pure normalization helpers for SSRF validation and URL identity."""

from __future__ import annotations

import ipaddress
import logging
import os
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)

_ALLOW_LOCAL_TRUE_VALUES = frozenset({"true", "1", "yes", "on", "enabled"})
_ALLOW_LOCAL_FALSE_VALUES = frozenset({"false", "0", "no", "off", "disabled"})


def normalize_hostname(hostname: str) -> str:
    """Normalize hostnames before SSRF checks."""
    stripped = hostname.strip().rstrip(".").lower()
    if stripped.startswith("[") and stripped.endswith("]"):
        stripped = stripped[1:-1]
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
    """Normalize a domain policy value for matching and identity keys."""
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
    """Normalize the hostname portion of an HTTP URL for cache and dedupe keys."""
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
