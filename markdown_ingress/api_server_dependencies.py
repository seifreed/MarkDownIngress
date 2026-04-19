"""FastAPI dependency injection helpers: API key auth and rate limiting enforcement."""

from __future__ import annotations

import hashlib
import os
import secrets

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


def _rate_limit_client_id(request: Request, x_api_key: str | None) -> str:
    import markdown_ingress.api_server as _srv

    if _srv.OPTIONAL_API_KEY is not None and x_api_key is not None:
        return hashlib.sha256(x_api_key.encode()).hexdigest()[:16]
    # Support X-Forwarded-For / X-Real-IP when behind trusted proxies
    trusted_proxies = os.getenv("MDI_TRUSTED_PROXY_IPS", "").strip()
    if trusted_proxies and request.client is not None and request.client.host:
        trusted_set = {ip.strip() for ip in trusted_proxies.split(",") if ip.strip()}
        if request.client.host in trusted_set:
            # Use X-Real-IP first, then rightmost untrusted IP from X-Forwarded-For.
            # Each header value MUST parse as a valid IP; otherwise an attacker
            # could send arbitrary strings to bypass per-IP rate-limit buckets.
            real_ip = request.headers.get("x-real-ip")
            if real_ip:
                candidate = real_ip.strip()
                if _is_valid_ip(candidate):
                    return f"ip:{candidate}"
            xff = request.headers.get("x-forwarded-for")
            if xff:
                parts = [p.strip() for p in xff.split(",")]
                for part in reversed(parts):
                    if part in trusted_set:
                        continue
                    if _is_valid_ip(part):
                        return f"ip:{part}"
    if request.client is not None and request.client.host:
        return f"ip:{request.client.host}"
    return "anonymous:unknown"


def _require_rate_limit(request: Request, x_api_key: str | None = Header(default=None)) -> None:
    """Enforce rate limiting for batch endpoints.

    Uses API key (if available) or falls back to the client IP for anonymous clients.
    Raises HTTP 429 if rate limit is exceeded.
    """
    import markdown_ingress.api_server as _srv

    client_id = _rate_limit_client_id(request, x_api_key)
    allowed, retry_after = _srv._check_rate_limit(client_id)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Retry after {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)},
        )
