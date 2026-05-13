"""Shared job queue records, errors, and time helpers."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

LEGACY_UNKNOWN_TTL_SECONDS = 3600


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class JobRecord:
    job_id: str
    status: str
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    webhook_url: str | None = None
    ttl_seconds: int | None = None
    legacy_expires_at: str | None = None


class JobAlreadyRunningError(RuntimeError):
    """Raised when a queued task is dispatched for a job that is already running."""
