"""Job queue repair policy helpers for the API server."""

from __future__ import annotations

from typing import Any


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


def job_queue_repair_finished(queue: Any, replacement_queue: Any, state: str | None) -> bool:
    return replacement_queue is not queue or state == "backend_error"
