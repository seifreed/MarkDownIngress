"""Job queue repair policy helpers for the API server."""

from __future__ import annotations

from typing import Any

from markdown_ingress.api_server_job_queue_states import (
    STATE_BACKEND_ERROR,
    STATE_EXTERNAL_OWNER,
)


def job_queue_repair_retry_delay(
    state: str | None,
    *,
    external_owner_seconds: float,
    backend_error_seconds: float,
) -> float:
    if state == STATE_EXTERNAL_OWNER:
        return external_owner_seconds
    if state == STATE_BACKEND_ERROR:
        return backend_error_seconds
    return 0.25


def job_queue_repair_finished(queue: Any, replacement_queue: Any, state: str | None) -> bool:
    return replacement_queue is not queue or state == STATE_BACKEND_ERROR
