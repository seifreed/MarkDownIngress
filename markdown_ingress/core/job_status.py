"""Canonical job status constants used by queue and API contracts."""

from __future__ import annotations

from typing import Final, Literal

JobStatus = Literal["queued", "running", "completed", "failed"]

JOB_STATUS_QUEUED: Final[Literal["queued"]] = "queued"
JOB_STATUS_RUNNING: Final[Literal["running"]] = "running"
JOB_STATUS_COMPLETED: Final[Literal["completed"]] = "completed"
JOB_STATUS_FAILED: Final[Literal["failed"]] = "failed"

JOB_STATUS_ACTIVE: Final[tuple[str, str]] = (JOB_STATUS_QUEUED, JOB_STATUS_RUNNING)
JOB_STATUS_FINISHED: Final[tuple[str, str]] = (JOB_STATUS_COMPLETED, JOB_STATUS_FAILED)
