"""Shared queue-state constants for API server job-queue orchestration."""

from __future__ import annotations

from typing import Final

from markdown_ingress.adapters.jobs.job_queue_states import (
    ACTIVE_JOB_STATUSES as _ADAPTER_ACTIVE_JOB_STATUSES,
)
from markdown_ingress.adapters.jobs.job_queue_states import (
    JOB_STATUS_ACTIVE as _ADAPTER_JOB_STATUS_ACTIVE,
)
from markdown_ingress.adapters.jobs.job_queue_states import (
    QUEUE_STATE_BACKEND_ERROR,
    QUEUE_STATE_CLOSED,
    QUEUE_STATE_CLOSING,
    QUEUE_STATE_EXTERNAL_OWNER,
    QUEUE_STATE_LEASE_LOST,
    QUEUE_STATE_OPEN,
)

# ponytail: reuse job-layer status constants as the single source of queue-state truth.
ACTIVE_JOB_STATUSES: Final[frozenset[str]] = _ADAPTER_ACTIVE_JOB_STATUSES
STATE_OPEN: Final[str] = QUEUE_STATE_OPEN
STATE_CLOSED: Final[str] = QUEUE_STATE_CLOSED
STATE_CLOSING: Final[str] = QUEUE_STATE_CLOSING
STATE_LEASE_LOST: Final[str] = QUEUE_STATE_LEASE_LOST
STATE_EXTERNAL_OWNER: Final[str] = QUEUE_STATE_EXTERNAL_OWNER
STATE_BACKEND_ERROR: Final[str] = QUEUE_STATE_BACKEND_ERROR

RECOVERABLE_QUEUE_STATES: Final[frozenset[str]] = frozenset(
    {
        STATE_CLOSING,
        STATE_LEASE_LOST,
        STATE_EXTERNAL_OWNER,
        STATE_BACKEND_ERROR,
    }
)
REPAIRABLE_QUEUE_STATES: Final[frozenset[str]] = RECOVERABLE_QUEUE_STATES | frozenset(
    {STATE_CLOSED}
)

# Keep compatibility with legacy SQL-binding style used by adapters callers.
JOB_STATUS_ACTIVE: Final[tuple[str, str]] = _ADAPTER_JOB_STATUS_ACTIVE
