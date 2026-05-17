"""In-memory rate-limit bookkeeping for the API server."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from threading import Lock

RequestWindow = deque[float] | list[float]


@dataclass
class MemoryRateLimitState:
    request_counts: dict[str, RequestWindow]
    lock: Lock
    cleanup_counter: int


@dataclass(frozen=True)
class MemoryRateLimitPolicy:
    cleanup_threshold: int
    max_clients: int
    rate_limit_requests: int
    rate_limit_window_seconds: int


def _drop_oldest_request(requests: RequestWindow) -> None:
    if isinstance(requests, deque):
        requests.popleft()
    else:
        del requests[0]


def _latest_request_time(requests: RequestWindow) -> float:
    return max(requests) if requests else float("-inf")


def check_memory_rate_limit(
    client_id: str,
    state: MemoryRateLimitState,
    policy: MemoryRateLimitPolicy,
) -> tuple[bool, int, int]:
    """Check an in-memory rate limit and return the updated cleanup counter."""
    now = time.time()
    with state.lock:
        cleanup_counter = state.cleanup_counter + 1
        if cleanup_counter >= policy.cleanup_threshold:
            cleanup_counter = 0
            stale_clients = [
                cid
                for cid, reqs in state.request_counts.items()
                if all(now - t >= policy.rate_limit_window_seconds for t in reqs)
            ]
            for cid in stale_clients:
                del state.request_counts[cid]
        state.cleanup_counter = cleanup_counter

        if client_id not in state.request_counts:
            state.request_counts[client_id] = deque()
        requests = state.request_counts[client_id]
        while requests and now - requests[0] >= policy.rate_limit_window_seconds:
            _drop_oldest_request(requests)

        rate_limit_hard_cap = policy.rate_limit_requests * 10
        while len(requests) > rate_limit_hard_cap:
            _drop_oldest_request(requests)

        if len(requests) >= policy.rate_limit_requests:
            oldest = requests[0]
            retry_after = max(1, int(policy.rate_limit_window_seconds - (now - oldest)) + 1)
            return False, retry_after, cleanup_counter
        requests.append(now)

        if len(state.request_counts) > policy.max_clients:
            client_ages = [
                (cid, _latest_request_time(reqs)) for cid, reqs in state.request_counts.items()
            ]
            client_ages.sort(key=lambda x: x[1])
            for cid, _ in client_ages[: len(client_ages) - policy.max_clients]:
                del state.request_counts[cid]

        return True, 0, cleanup_counter
