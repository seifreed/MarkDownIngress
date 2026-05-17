"""Validation and SSRF safeguards for the persistent job queue."""

import ipaddress
import math
import os
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from markdown_ingress.core.ssrf import (
    is_blocked_hostname,
    is_blocked_ip_address,
    normalize_hostname,
    normalize_ip_for_ssrf,
    validate_http_url_no_ssrf,
)

_BLOCKED_SCHEMES = {
    "file",
    "ftp",
    "data",
    "gopher",
    "dict",
    "ldap",
    "ldaps",
    "jar",
    "mailto",
    "news",
    "nntp",
    "irc",
    "mms",
    "rtsp",
    "svn",
    "git",
}


def validate_int_config(field_name: str, value: object, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an int, got {type(value).__name__}")
    return max(minimum, value)


def validate_positive_finite_float(field_name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number, got {type(value).__name__}")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
    if numeric <= 0:
        raise ValueError(f"{field_name} must be > 0")
    return numeric


def validate_optional_positive_finite_float(field_name: str, value: object | None) -> float | None:
    if value is None:
        return None
    return validate_positive_finite_float(field_name, value)


def allowed_db_roots() -> list[Path]:
    """Return directories where the persistent job DB is allowed to live."""
    import tempfile

    override = os.getenv("MDI_ALLOWED_DB_DIRS")
    if override:
        roots: list[Path] = []
        for raw_root in override.split(os.pathsep):
            stripped_root = raw_root.strip()
            if not stripped_root:
                continue
            try:
                roots.append(Path(stripped_root).expanduser().resolve())
            except OSError:
                continue
        if roots:
            return roots
    candidates = [
        Path.cwd(),
        Path.home() / ".markdown_ingress",
        Path(tempfile.gettempdir()),
    ]
    resolved: list[Path] = []
    for candidate in candidates:
        try:
            resolved.append(candidate.resolve())
        except OSError:
            continue
    return resolved


def validate_job_db_path(db_path: str | Path) -> Path:
    """Resolve and validate a job-queue DB path against the allowed roots."""
    raw = str(db_path).strip()
    if not raw:
        raise ValueError("Job DB path cannot be empty")
    if "\x00" in raw:
        raise ValueError("Job DB path contains null byte")
    candidate = Path(raw).expanduser()
    resolved = (candidate if candidate.is_absolute() else (Path.cwd() / candidate)).resolve()
    for root in allowed_db_roots():
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        return resolved
    raise ValueError(
        f"Job DB path {resolved!s} is outside the allowed roots. "
        "Set MDI_ALLOWED_DB_DIRS to customise."
    )


def is_private_ip(ip_str: str) -> bool:
    """Check if an IP address is in a blocked private/reserved range."""
    try:
        for _, _, _, _, sockaddr in socket.getaddrinfo(ip_str, None):
            ip_obj = ipaddress.ip_address(sockaddr[0])
            if is_private_ip_address(ip_obj):
                return True
    except (socket.gaierror, OSError, ValueError):
        return True
    else:
        return False


def is_private_ip_address(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Check if an IP address object is in a blocked private/reserved range."""
    return is_blocked_ip_address(normalize_ip_for_ssrf(ip_obj))


def _validate_resolved_ip(
    hostname: str,
    normalized_hostname: str,
    ip_str: str,
    *,
    allow_local: bool,
) -> str | None:
    try:
        ip_obj = normalize_ip_for_ssrf(ipaddress.ip_address(ip_str))
    except ValueError:
        return None

    if allow_local:
        return ip_str

    if is_blocked_hostname(ip_str) or is_blocked_hostname(normalized_hostname):
        raise ValueError(f"Hostname {hostname} resolves to blocked IP {ip_str}")
    if is_blocked_ip_address(ip_obj):
        raise ValueError(f"Hostname {hostname} resolves to private IP {ip_str}")
    return ip_str


def _first_validated_resolved_ip(
    hostname: str,
    normalized_hostname: str,
    addr_info: list[Any],
    *,
    allow_local: bool,
) -> str:
    if not addr_info:
        raise ValueError(f"No IP addresses resolved for {hostname}")

    validated_ip: str | None = None
    for _family, _, _, _, sockaddr in addr_info:
        ip_str = str(sockaddr[0])
        resolved_ip = _validate_resolved_ip(
            hostname,
            normalized_hostname,
            ip_str,
            allow_local=allow_local,
        )
        if resolved_ip is not None and validated_ip is None:
            validated_ip = resolved_ip

    if validated_ip is None:
        raise ValueError(f"No valid public IP found for {hostname}")
    return validated_ip


def resolve_and_validate_ip(hostname: str, *, allow_local: bool = False) -> str:
    """Resolve hostname and validate the IP is not private/blocked."""
    try:
        normalized_hostname = normalize_hostname(hostname)
        addr_info = socket.getaddrinfo(normalized_hostname, None)
        return _first_validated_resolved_ip(
            hostname,
            normalized_hostname,
            addr_info,
            allow_local=allow_local,
        )
    except socket.gaierror:
        raise
    except OSError as exc:
        raise socket.gaierror(str(exc)) from exc


def validate_webhook_url(url: str | None, *, allow_local: bool = False) -> None:
    """Validate webhook URL to prevent SSRF attacks."""
    if url is None:
        return
    if not isinstance(url, str):
        raise ValueError(f"webhook_url must be a string, got {type(url).__name__}")
    if len(url) > 2048:
        raise ValueError("webhook_url exceeds maximum length of 2048 characters")
    try:
        validate_http_url_no_ssrf(url, allow_local=allow_local, resolve_dns=True)
    except Exception as exc:
        message = str(exc)
        if "valid network location" in message or "valid host" in message:
            raise ValueError("webhook_url must include a hostname") from exc
        if "hostname blocked" in message:
            parsed = urlparse(url)
            hostname = normalize_hostname(parsed.hostname or "")
            raise ValueError(f"webhook_url blocked: hostname {hostname!r} is not allowed") from exc
        if "blocked range" in message:
            parsed = urlparse(url)
            hostname = normalize_hostname(parsed.hostname or "")
            raise ValueError(
                f"webhook_url blocked: hostname {hostname!r} is a private IP address"
            ) from exc
        raise ValueError(f"webhook_url blocked: {message.removeprefix('URL ')}") from exc
