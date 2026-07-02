"""Job queue initialization helpers for the API server."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def close_previous_job_queue_for_init(
    previous_queue: Any | None,
    *,
    recoverable_states: set[str],
    remember_job_queue: Callable[[Any], None],
) -> Any | None:
    if previous_queue is None:
        return None
    close = getattr(previous_queue, "close", None)
    if callable(close):
        try:
            close()
        except RuntimeError:
            if getattr(previous_queue, "state", None) in recoverable_states:
                return previous_queue
            raise
    remember_job_queue(previous_queue)
    return None


def fallback_queue_for_init_build_error(
    previous_queue: Any | None,
    exc: RuntimeError,
    *,
    is_active_owner_error: Callable[[RuntimeError], bool],
    promote_external_owner_queue: Callable[[Any], Any],
    external_owner_queue: Callable[[], Any],
) -> Any | None:
    if not is_active_owner_error(exc):
        return None
    if previous_queue is not None:
        return promote_external_owner_queue(previous_queue)
    return external_owner_queue()
