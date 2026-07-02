"""Job queue selection helpers for API server state management."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class JobQueueSelection:
    queue_to_return: Any | None
    queue_to_repair: Any | None
    start_repair: bool = False


def select_job_queue_for_use(queue: Any | None, repairable_states: set[str]) -> JobQueueSelection:
    if queue is None:
        raise RuntimeError("Job queue is unavailable")
    state = getattr(queue, "state", None)
    if state not in repairable_states:
        return JobQueueSelection(queue_to_return=queue, queue_to_repair=None)
    if state == "external_owner":
        return JobQueueSelection(
            queue_to_return=queue,
            queue_to_repair=None,
            start_repair=True,
        )
    return JobQueueSelection(queue_to_return=None, queue_to_repair=queue)


def current_queue_after_repair_close_failure(
    queue_to_repair: Any,
    current_queue: Any | None,
    repairable_states: set[str],
) -> Any | None:
    if getattr(queue_to_repair, "state", None) not in repairable_states:
        return None
    if current_queue is None:
        raise RuntimeError("Job queue is unavailable")
    return current_queue


def current_queue_if_expected_changed(expected_queue: Any, current_queue: Any) -> Any | None:
    if current_queue is not expected_queue:
        return current_queue
    return None


def queue_if_expected_state(expected_queue: Any, states: set[str]) -> Any | None:
    if getattr(expected_queue, "state", None) in states:
        return expected_queue
    return None
