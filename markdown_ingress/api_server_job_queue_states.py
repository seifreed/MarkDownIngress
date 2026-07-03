"""Shared queue-state constants for API server job-queue orchestration."""

from __future__ import annotations

from typing import Final

ACTIVE_JOB_STATUSES: Final[frozenset[str]] = frozenset({"queued", "running"})

STATE_OPEN: Final[str] = "open"
STATE_CLOSED: Final[str] = "closed"
STATE_CLOSING: Final[str] = "closing"
STATE_LEASE_LOST: Final[str] = "lease_lost"
STATE_EXTERNAL_OWNER: Final[str] = "external_owner"
STATE_BACKEND_ERROR: Final[str] = "backend_error"

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
