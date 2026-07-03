"""FastAPI dependency injection helpers: API key auth and rate limiting enforcement."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Collection

from fastapi import Header, HTTPException, Request

from markdown_ingress.api_server_auth import _is_valid_ip


def _require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Enforce API key auth when configured.

    Uses secrets.compare_digest for constant-time comparison to prevent timing attacks.
    Returns an identical "Unauthorized" detail for both missing and wrong keys so
    callers cannot enumerate whether a key is configured or simply incorrect.
    """
    import markdown_ingress.api_server as _srv

    if _srv.API_KEY_CONFIG_ERROR:
        raise HTTPException(status_code=500, detail="Server API key configuration is invalid")
    if _srv.OPTIONAL_API_KEY is None:
        return
    provided = x_api_key if x_api_key is not None else ""
    if not secrets.compare_digest(provided, _srv.OPTIONAL_API_KEY):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _api_key_client_id(x_api_key: str | None) -> str | None:
    import markdown_ingress.api_server as _srv

    if _srv.OPTIONAL_API_KEY is not None and x_api_key is not None:
        return hashlib.sha256(x_api_key.encode()).hexdigest()[:16]
    return None


def _request_client_host(request: Request) -> str | None:
    if request.client is not None and request.client.host:
        return request.client.host
    return None


def _trusted_proxy_ips(trusted_proxy_ips: Collection[str] | None = None) -> set[str]:
    return set(trusted_proxy_ips or ())


def _real_ip_client_id(request: Request) -> str | None:
    real_ip = request.headers.get("x-real-ip")
    if not real_ip:
        return None
    candidate = real_ip.strip()
    if _is_valid_ip(candidate):
        return f"ip:{candidate}"
    return None


def _forwarded_for_client_id(request: Request, trusted_set: set[str]) -> str | None:
    xff = request.headers.get("x-forwarded-for")
    if not xff:
        return None
    parts = [part.strip() for part in xff.split(",")]
    for part in reversed(parts):
        if part in trusted_set:
            continue
        if _is_valid_ip(part):
            return f"ip:{part}"
    return None


def _trusted_proxy_client_id(request: Request, trusted_set: set[str]) -> str | None:
    # Use X-Real-IP first, then rightmost untrusted IP from X-Forwarded-For.
    # Each header value MUST parse as a valid IP; otherwise an attacker
    # could send arbitrary strings to bypass per-IP rate-limit buckets.
    return _real_ip_client_id(request) or _forwarded_for_client_id(request, trusted_set)


def _rate_limit_client_id(
    request: Request, x_api_key: str | None, trusted_proxy_ips: Collection[str] | None = None
) -> str:
    api_key_client_id = _api_key_client_id(x_api_key)
    if api_key_client_id is not None:
        return api_key_client_id

    client_host = _request_client_host(request)
    trusted_set = _trusted_proxy_ips(trusted_proxy_ips)
    if client_host is not None and client_host in trusted_set:
        proxy_client_id = _trusted_proxy_client_id(request, trusted_set)
        if proxy_client_id is not None:
            return proxy_client_id

    if client_host is not None:
        return f"ip:{client_host}"
    return "anonymous:unknown"


def _require_rate_limit(request: Request, x_api_key: str | None = Header(default=None)) -> None:
    """Enforce rate limiting for batch endpoints.

    Uses API key (if available) or falls back to the client IP for anonymous clients.
    Raises HTTP 429 if rate limit is exceeded.
    """
    import markdown_ingress.api_server as _srv

    client_id = _rate_limit_client_id(request, x_api_key, _srv.TRUSTED_PROXY_IPS)
    try:
        allowed, retry_after = _srv._check_rate_limit(client_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Retry after {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)},
        )
