"""Shared status constants for SQLite job queue jobs and lifecycle states."""

from __future__ import annotations

from typing import Final, Literal

JOB_STATUS_QUEUED: Final[Literal["queued"]] = "queued"
JOB_STATUS_RUNNING: Final[Literal["running"]] = "running"
JOB_STATUS_COMPLETED: Final[Literal["completed"]] = "completed"
JOB_STATUS_FAILED: Final[Literal["failed"]] = "failed"

JOB_STATUS_ACTIVE: Final[tuple[str, str]] = (JOB_STATUS_QUEUED, JOB_STATUS_RUNNING)
ACTIVE_JOB_STATUSES: Final[frozenset[str]] = frozenset(JOB_STATUS_ACTIVE)
JOB_STATUS_FINISHED: Final[tuple[str, str]] = (JOB_STATUS_COMPLETED, JOB_STATUS_FAILED)

QUEUE_STATE_OPEN: Final[str] = "open"
QUEUE_STATE_CLOSED: Final[str] = "closed"
QUEUE_STATE_CLOSING: Final[str] = "closing"
QUEUE_STATE_LEASE_LOST: Final[str] = "lease_lost"
QUEUE_STATE_EXTERNAL_OWNER: Final[str] = "external_owner"
QUEUE_STATE_BACKEND_ERROR: Final[str] = "backend_error"
