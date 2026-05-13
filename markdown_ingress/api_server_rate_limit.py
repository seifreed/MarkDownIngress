"""In-memory rate-limit bookkeeping for the API server."""

from __future__ import annotations

import time
from collections import deque
from threading import Lock

RequestWindow = deque[float] | list[float]


def _drop_oldest_request(requests: RequestWindow) -> None:
    if isinstance(requests, deque):
        requests.popleft()
    else:
        del requests[0]


def _latest_request_time(requests: RequestWindow) -> float:
    return max(requests) if requests else float("-inf")


def check_memory_rate_limit(
    client_id: str,
    *,
    request_counts: dict[str, RequestWindow],
    lock: Lock,
    cleanup_counter: int,
    cleanup_threshold: int,
    max_clients: int,
    rate_limit_requests: int,
    rate_limit_window_seconds: int,
) -> tuple[bool, int, int]:
    """Check an in-memory rate limit and return the updated cleanup counter."""
    now = time.time()
    with lock:
        cleanup_counter += 1
        if cleanup_counter >= cleanup_threshold:
            cleanup_counter = 0
            stale_clients = [
                cid
                for cid, reqs in request_counts.items()
                if all(now - t >= rate_limit_window_seconds for t in reqs)
            ]
            for cid in stale_clients:
                del request_counts[cid]

        if client_id not in request_counts:
            request_counts[client_id] = deque()
        requests = request_counts[client_id]
        while requests and now - requests[0] >= rate_limit_window_seconds:
            _drop_oldest_request(requests)

        rate_limit_hard_cap = rate_limit_requests * 10
        while len(requests) > rate_limit_hard_cap:
            _drop_oldest_request(requests)

        if len(requests) >= rate_limit_requests:
            oldest = requests[0]
            retry_after = max(1, int(rate_limit_window_seconds - (now - oldest)) + 1)
            return False, retry_after, cleanup_counter
        requests.append(now)

        if len(request_counts) > max_clients:
            client_ages = [
                (cid, _latest_request_time(reqs)) for cid, reqs in request_counts.items()
            ]
            client_ages.sort(key=lambda x: x[1])
            for cid, _ in client_ages[: len(client_ages) - max_clients]:
                del request_counts[cid]

        return True, 0, cleanup_counter
