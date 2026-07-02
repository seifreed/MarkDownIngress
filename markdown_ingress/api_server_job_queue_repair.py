"""Job queue repair policy helpers for the API server."""

from __future__ import annotations


def job_queue_repair_retry_delay(
    state: str | None,
    *,
    external_owner_seconds: float,
    backend_error_seconds: float,
) -> float:
    if state == "external_owner":
        return external_owner_seconds
    if state == "backend_error":
        return backend_error_seconds
    return 0.25
