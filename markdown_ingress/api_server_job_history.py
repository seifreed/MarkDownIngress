"""Job queue history helpers for the API server."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast


def remember_job_queue(history: list[Any], lock: Any, queue: Any | None) -> None:
    """Record a job queue in history for cleanup tracking."""
    if queue is None:
        return
    with lock:
        if getattr(queue, "state", None) == "external_owner":
            return
        if any(existing is queue for existing in history):
            return
        queue_db_path = getattr(queue, "db_path", None)
        if queue_db_path is not None:
            queue_db_path = str(queue_db_path)
            history[:] = [
                existing
                for existing in history
                if str(getattr(existing, "db_path", object())) != queue_db_path
            ]
        history.append(queue)


def prune_job_queue_history(
    history: list[Any],
    lock: Any,
    *,
    queue_still_has_visible_jobs: Callable[[Any], bool],
    transient_read_error: type[Exception],
    prune_error_threshold: int,
) -> None:
    """Remove job queues from history that no longer have visible jobs."""
    with lock:
        kept = []
        for queue in history:
            try:
                if queue_still_has_visible_jobs(queue):
                    cast(Any, queue)._history_read_failures = 0
                    kept.append(queue)
            except transient_read_error:
                failures = int(getattr(queue, "_history_read_failures", 0)) + 1
                cast(Any, queue)._history_read_failures = failures
                if failures < prune_error_threshold:
                    kept.append(queue)
        history[:] = kept
