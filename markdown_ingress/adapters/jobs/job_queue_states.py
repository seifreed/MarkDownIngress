"""Shared status constants for SQLite job queue jobs and lifecycle states."""

from __future__ import annotations

from typing import Final

from markdown_ingress.core.job_status import (
    JOB_STATUS_ACTIVE as _JOB_STATUS_ACTIVE,
)
from markdown_ingress.core.job_status import (
    JOB_STATUS_COMPLETED as _JOB_STATUS_COMPLETED,
)
from markdown_ingress.core.job_status import (
    JOB_STATUS_FAILED as _JOB_STATUS_FAILED,
)
from markdown_ingress.core.job_status import (
    JOB_STATUS_FINISHED as _JOB_STATUS_FINISHED,
)
from markdown_ingress.core.job_status import (
    JOB_STATUS_QUEUED as _JOB_STATUS_QUEUED,
)
from markdown_ingress.core.job_status import (
    JOB_STATUS_RUNNING as _JOB_STATUS_RUNNING,
)

# ponytail: keep queue-layer constants as a direct re-export from core.
JOB_STATUS_ACTIVE = _JOB_STATUS_ACTIVE
JOB_STATUS_COMPLETED = _JOB_STATUS_COMPLETED
JOB_STATUS_FAILED = _JOB_STATUS_FAILED
JOB_STATUS_FINISHED = _JOB_STATUS_FINISHED
JOB_STATUS_QUEUED = _JOB_STATUS_QUEUED
JOB_STATUS_RUNNING = _JOB_STATUS_RUNNING

ACTIVE_JOB_STATUSES: Final[frozenset[str]] = frozenset(JOB_STATUS_ACTIVE)

QUEUE_STATE_OPEN: Final[str] = "open"
QUEUE_STATE_CLOSED: Final[str] = "closed"
QUEUE_STATE_CLOSING: Final[str] = "closing"
QUEUE_STATE_LEASE_LOST: Final[str] = "lease_lost"
QUEUE_STATE_EXTERNAL_OWNER: Final[str] = "external_owner"
QUEUE_STATE_BACKEND_ERROR: Final[str] = "backend_error"
