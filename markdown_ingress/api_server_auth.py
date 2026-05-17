"""Authentication and rate limiting for the API server."""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from markdown_ingress.api_server_env import _read_positive_int_env

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# API key configuration
# ---------------------------------------------------------------------------

_RAW_API_KEY = os.getenv("MDI_API_KEY")
API_KEY_CONFIG_ERROR: bool = _RAW_API_KEY is not None and _RAW_API_KEY.strip() == ""
OPTIONAL_API_KEY: str | None = None if API_KEY_CONFIG_ERROR else _RAW_API_KEY

# ---------------------------------------------------------------------------
# Rate limiting configuration
# ---------------------------------------------------------------------------

RATE_LIMIT_REQUESTS: int = _read_positive_int_env("MDI_API_RATE_LIMIT_REQUESTS", 100)
RATE_LIMIT_WINDOW_SECONDS: int = _read_positive_int_env("MDI_API_RATE_LIMIT_WINDOW", 60)

_RATE_LIMIT_BACKEND: str = os.getenv("MDI_RATE_LIMIT_BACKEND", "memory").strip().lower()
_RATE_LIMIT_REDIS_URL: str = os.getenv("MDI_RATE_LIMIT_REDIS_URL", "redis://localhost:6379/0")
_RATE_LIMIT_REDIS_PREFIX: str = os.getenv("MDI_RATE_LIMIT_REDIS_PREFIX", "mdi:rl:")
_rate_limit_redis_client: Any | None = None
_rate_limit_redis_lock = threading.Lock()


def _get_redis_rate_limit_client():
    """Lazily initialise the Redis client for distributed rate limiting (S9)."""
    global _rate_limit_redis_client
    if _rate_limit_redis_client is not None:
        return _rate_limit_redis_client
    with _rate_limit_redis_lock:
        if _rate_limit_redis_client is not None:
            return _rate_limit_redis_client
        try:
            import redis  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "MDI_RATE_LIMIT_BACKEND=redis requires the 'redis' package. "
                "Install with: pip install redis"
            ) from exc
        client = redis.Redis.from_url(_RATE_LIMIT_REDIS_URL, decode_responses=True)
        try:
            client.ping()
        except Exception as exc:  # pragma: no cover — depends on env
            raise RuntimeError(
                f"Cannot connect to Redis at {_RATE_LIMIT_REDIS_URL!r}: {exc}"
            ) from exc
        _rate_limit_redis_client = client
        return client


def _check_rate_limit_redis(client_id: str) -> tuple[bool, int]:
    """Fixed-window rate limit backed by Redis (S9).

    Uses INCR + EXPIRE in a pipeline. The key lives for the remainder of the
    window; once it expires the counter resets, avoiding sliding-window cost.
    """
    redis_client = _get_redis_rate_limit_client()
    key = f"{_RATE_LIMIT_REDIS_PREFIX}{client_id}"
    pipe = redis_client.pipeline()
    pipe.incr(key, 1)
    pipe.ttl(key)
    count, ttl = pipe.execute()
    if ttl is None or ttl < 0:
        redis_client.expire(key, RATE_LIMIT_WINDOW_SECONDS)
        ttl = RATE_LIMIT_WINDOW_SECONDS
    if int(count) > RATE_LIMIT_REQUESTS:
        return False, max(1, int(ttl))
    return True, 0


def _is_valid_ip(value: str) -> bool:
    """Return True if the string parses as a valid IPv4/IPv6 address."""
    import ipaddress

    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    else:
        return True
