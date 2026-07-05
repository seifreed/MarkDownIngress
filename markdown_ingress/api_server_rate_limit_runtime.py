"""Compatibility helpers shared by runtime internals and API server tests."""

from __future__ import annotations

import threading
from collections.abc import Callable

from markdown_ingress.api_server_rate_limit import (
    MemoryRateLimitPolicy,
    MemoryRateLimitState,
    RequestWindow,
    check_memory_rate_limit,
)

_RedisRateLimitChecker = Callable[[str], tuple[bool, int]]


def _check_rate_limit_runtime(
    *,
    client_id: str,
    request_counts: dict[str, RequestWindow],
    lock: threading.Lock,
    cleanup_counter: int,
    cleanup_threshold: int,
    max_clients: int,
    rate_limit_requests: int,
    rate_limit_window_seconds: int,
    backend: str,
    check_rate_limit_redis: _RedisRateLimitChecker,
) -> tuple[bool, int, int]:
    """Run in-memory or Redis-backed rate limiting and return updated state."""
    if backend == "redis":
        allowed, retry_after = check_rate_limit_redis(client_id)
        return allowed, retry_after, cleanup_counter

    rate_limit_state = MemoryRateLimitState(
        request_counts=request_counts,
        lock=lock,
        cleanup_counter=cleanup_counter,
    )
    policy = MemoryRateLimitPolicy(
        cleanup_threshold=cleanup_threshold,
        max_clients=max_clients,
        rate_limit_requests=rate_limit_requests,
        rate_limit_window_seconds=rate_limit_window_seconds,
    )
    return check_memory_rate_limit(client_id, rate_limit_state, policy)
