"""Tests for API server rate-limit orchestration helpers."""

from __future__ import annotations

import threading
from collections import deque

from markdown_ingress.api_server_rate_limit_runtime import _check_rate_limit_runtime


def test_check_rate_limit_runtime_delegates_to_redis_checker():
    calls: list[tuple[str, int]] = []

    def redis_checker(client_id: str) -> tuple[bool, int]:
        calls.append((client_id, len(calls)))
        return False, 12

    allowed, retry_after, counter = _check_rate_limit_runtime(
        client_id="redis-client",
        request_counts={},
        lock=threading.Lock(),
        cleanup_counter=99,
        cleanup_threshold=10,
        max_clients=100,
        rate_limit_requests=5,
        rate_limit_window_seconds=60,
        backend="redis",
        check_rate_limit_redis=redis_checker,
    )

    assert calls == [("redis-client", 0)]
    assert allowed is False
    assert retry_after == 12
    assert counter == 99


def test_check_rate_limit_runtime_uses_memory_path_when_not_redis():
    request_counts: dict[str, deque[float] | list[float]] = {}
    allowed, retry_after, counter = _check_rate_limit_runtime(
        client_id="ip:127.0.0.1",
        request_counts=request_counts,
        lock=threading.Lock(),
        cleanup_counter=5,
        cleanup_threshold=10,
        max_clients=2,
        rate_limit_requests=1,
        rate_limit_window_seconds=60,
        backend="memory",
        check_rate_limit_redis=lambda _client_id: (False, 0),
    )

    assert allowed is True
    assert retry_after == 0
    assert counter == 6
    assert request_counts["ip:127.0.0.1"]
