"""Read-only API job queue view for externally owned SQLite backends."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from markdown_ingress.adapters.jobs.sqlite_job_queue import JobRecord, PersistentJobQueue
from markdown_ingress.api_server_job_queue_states import ACTIVE_JOB_STATUSES
from markdown_ingress.api_server_queue_ttl import (
    _completed_row_with_ttl_is_expired,
    _completed_row_without_ttl_is_expired,
)


class _ExternalOwnerJobQueue:
    """Read-only queue view used when another active process owns the job DB."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.state = "external_owner"

    def _raise_backend_read_error(self, exc: sqlite3.Error) -> None:
        message = str(exc).lower()
        if isinstance(exc, sqlite3.OperationalError) and ("locked" in message or "busy" in message):
            raise RuntimeError(
                "Job queue backend is temporarily unavailable because the current owner is busy"
            )
        self.state = "backend_error"
        raise RuntimeError(f"Job queue backend read failed: {exc}")

    def _db_uri(self) -> str:
        return f"{self.db_path.resolve().as_uri()}?mode=ro"

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_uri(), timeout=0.0, uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def submit(self, *_args, **_kwargs):
        if self.state == "backend_error":
            raise RuntimeError("Job queue backend read failed: external owner backend is unhealthy")
        raise RuntimeError(
            "Job queue is unavailable because the DB is owned by another active instance"
        )

    def pending_count(self, *, cleanup_expired: bool = True) -> int:
        del cleanup_expired
        row = None
        try:
            with closing(self._connect()) as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS count FROM jobs WHERE status IN ('queued','running')"
                ).fetchone()
        except sqlite3.Error as exc:
            self._raise_backend_read_error(exc)
        return int(row[0]) if row else 0

    @staticmethod
    def _row_is_expired(row: sqlite3.Row) -> bool:
        if row["status"] in ACTIVE_JOB_STATUSES:
            return False

        now_dt = datetime.now(UTC)
        ttl_seconds = row["ttl_seconds"]
        if ttl_seconds is not None:
            return _completed_row_with_ttl_is_expired(row["completed_at"], ttl_seconds, now_dt)
        return _completed_row_without_ttl_is_expired(
            row["completed_at"],
            row["legacy_expires_at"],
            now_dt,
        )

    def get(self, job_id: str, *, cleanup_expired: bool = True) -> JobRecord | None:
        try:
            with closing(self._connect()) as conn:
                row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        except sqlite3.Error as exc:
            self._raise_backend_read_error(exc)
        if row is None:
            return None
        if cleanup_expired and self._row_is_expired(row):
            return None
        return PersistentJobQueue._record_from_row(row)

    def close(self, *_args, **_kwargs) -> None:
        return None
