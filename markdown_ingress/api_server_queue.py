"""
Job-queue helper classes and pure utilities for api_server.py.

Contains classes and stateless helper functions that have no dependency on
api_server's mutable module-level globals, extracted to keep api_server.py
focused on routing, middleware, and stateful queue management.

Symbols that depend on mutable globals (JOB_QUEUE, _JOB_QUEUE_HISTORY, etc.)
remain in api_server.py so that monkeypatch.setattr(api_server, ...) works
correctly in tests.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from markdown_ingress.adapters.jobs.sqlite_job_queue import (
    LEGACY_UNKNOWN_TTL_SECONDS,
    JobRecord,
    PersistentJobQueue,
)
from markdown_ingress.api_server_env import _parse_iso_datetime_utc

# ---------------------------------------------------------------------------
# Timeout / threshold constants
# ---------------------------------------------------------------------------

_QUEUE_LEASE_TIMEOUT_SECONDS = 30.0
_LEGACY_QUEUE_PRUNE_ERROR_THRESHOLD = 3
_ACTIVE_JOB_STATUSES = {"queued", "running"}

# ---------------------------------------------------------------------------
# Exception types
# ---------------------------------------------------------------------------


class _TransientLegacyQueueReadError(RuntimeError):
    """Signal that a legacy queue could not be inspected due to a transient read problem."""


# ---------------------------------------------------------------------------
# Pure TTL helper
# ---------------------------------------------------------------------------


def _legacy_unknown_ttl_expires_at(
    completed_at: str | None, legacy_expires_at: str | None
) -> datetime | None:
    """Return the effective expiry for a legacy completed job.

    When an explicit ``legacy_expires_at`` exists, it is authoritative.
    Older rows may lack both ``ttl_seconds`` and ``legacy_expires_at``. In that
    case the queue layer preserves them with ``LEGACY_UNKNOWN_TTL_SECONDS``
    from completion time, so the API needs to use the same derived window when
    deciding whether the row is still visible.
    """
    if legacy_expires_at:
        expires_dt = _parse_iso_datetime_utc(legacy_expires_at)
        if expires_dt is not None:
            return expires_dt
        return None
    if completed_at is None:
        return None
    completed_dt = _parse_iso_datetime_utc(completed_at)
    if completed_dt is None:
        return None
    return completed_dt + timedelta(seconds=LEGACY_UNKNOWN_TTL_SECONDS)


def _coerce_positive_ttl_seconds(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        ttl_value = value
    elif isinstance(value, str) and value.strip().isdigit():
        ttl_value = int(value)
    else:
        return None
    if ttl_value <= 0:
        return None
    return ttl_value


def _parse_optional_datetime_utc(value: str | None) -> datetime | None:
    if value is None:
        return None
    return _parse_iso_datetime_utc(value)


def _optional_text(value: object) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _completed_row_with_ttl_is_expired(
    completed_at: str | None,
    ttl_seconds: object,
    now: datetime,
) -> bool:
    ttl_value = _coerce_positive_ttl_seconds(ttl_seconds)
    if ttl_value is None:
        return True
    completed_dt = _parse_optional_datetime_utc(completed_at)
    if completed_dt is None:
        return True
    return completed_dt + timedelta(seconds=ttl_value) <= now


def _completed_row_without_ttl_is_expired(
    completed_at: str | None,
    legacy_expires_at: str | None,
    now: datetime,
) -> bool:
    legacy_expires_dt = _parse_optional_datetime_utc(legacy_expires_at)
    if legacy_expires_dt is not None:
        return legacy_expires_dt <= now
    expires_dt = _legacy_unknown_ttl_expires_at(completed_at, None)
    if expires_dt is None:
        return True
    return expires_dt <= now


def _job_visibility_fields(row: object) -> tuple[object, object, object, object]:
    if isinstance(row, sqlite3.Row):
        return row["status"], row["completed_at"], row["ttl_seconds"], row["legacy_expires_at"]
    row_values = cast(tuple[object, object, object, object], row)
    return row_values[0], row_values[1], row_values[2], row_values[3]


def _completed_job_is_visible(
    completed_at: object,
    ttl_seconds: object,
    legacy_expires_at: object,
    now: datetime,
) -> bool:
    if ttl_seconds is None:
        expires_dt = _legacy_unknown_ttl_expires_at(
            _optional_text(completed_at),
            _optional_text(legacy_expires_at),
        )
        return expires_dt is not None and now <= expires_dt

    if not completed_at:
        return False
    completed_dt = _parse_optional_datetime_utc(_optional_text(completed_at))
    if completed_dt is None:
        return False
    ttl_value = _coerce_positive_ttl_seconds(ttl_seconds)
    if ttl_value is None:
        return False
    return (now - completed_dt).total_seconds() <= ttl_value


def _job_row_is_visible(row: object, now: datetime) -> bool:
    status, completed_at, ttl_seconds, legacy_expires_at = _job_visibility_fields(row)
    if status in _ACTIVE_JOB_STATUSES:
        return True
    return _completed_job_is_visible(completed_at, ttl_seconds, legacy_expires_at, now)


# ---------------------------------------------------------------------------
# _ExternalOwnerJobQueue
# ---------------------------------------------------------------------------


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
            with closing(sqlite3.connect(self._db_uri(), timeout=0.0, uri=True)) as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS count FROM jobs WHERE status IN ('queued','running')"
                ).fetchone()
        except sqlite3.Error as exc:
            self._raise_backend_read_error(exc)
        return int(row[0]) if row else 0

    @staticmethod
    def _row_is_expired(row: sqlite3.Row) -> bool:
        if row["status"] in _ACTIVE_JOB_STATUSES:
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
        return JobRecord(
            job_id=row["job_id"],
            status=row["status"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            result=PersistentJobQueue._safe_json_loads(row["result_json"]),
            error=row["error"],
            webhook_url=row["webhook_url"],
            ttl_seconds=row["ttl_seconds"],
            legacy_expires_at=row["legacy_expires_at"],
        )

    def close(self, *_args, **_kwargs) -> None:
        return None


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------


def _is_active_owner_error(exc: RuntimeError) -> bool:
    return str(exc) == "Job queue DB is already owned by another active instance"


def _is_owner_process_alive(owner_pid: int) -> bool:
    if owner_pid <= 0:
        return False
    try:
        os.kill(owner_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _is_stale_heartbeat(heartbeat_at: str) -> bool:
    heartbeat_dt = _parse_iso_datetime_utc(heartbeat_at)
    if heartbeat_dt is None:
        return True
    age_seconds = (datetime.now(UTC) - heartbeat_dt).total_seconds()
    return age_seconds > _QUEUE_LEASE_TIMEOUT_SECONDS


def _close_queue_for_repair(queue: object) -> None:
    close = getattr(queue, "close", None)
    if not callable(close):
        if getattr(queue, "state", None) == "external_owner":
            return
        raise RuntimeError("Job queue cannot be closed for repair")
    try:
        close(inline_wait_timeout=0.0, preserve_state_on_inline_timeout=True)
    except TypeError:
        close()


def _read_job_from_queue(queue, job_id: str):
    try:
        return queue.get(job_id, cleanup_expired=False)
    except TypeError:
        try:
            return queue.get(job_id)
        except sqlite3.Error as exc:
            raise RuntimeError(f"Job queue backend read failed: {exc}") from exc
    except sqlite3.Error as exc:
        raise RuntimeError(f"Job queue backend read failed: {exc}") from exc


def _job_record_within_api_ttl(job) -> bool:
    status = getattr(job, "status", None)
    completed_at = getattr(job, "completed_at", None)
    if status in {"queued", "running"}:
        return True
    ttl_seconds = cast(object | None, getattr(job, "ttl_seconds", None))
    if ttl_seconds is None:
        return _legacy_job_record_within_api_ttl(job, completed_at)
    return _completed_job_record_within_api_ttl(completed_at, ttl_seconds)


def _legacy_job_record_within_api_ttl(job, completed_at) -> bool:
    expires_dt = _legacy_unknown_ttl_expires_at(
        completed_at,
        getattr(job, "legacy_expires_at", None),
    )
    return expires_dt is not None and datetime.now(UTC) <= expires_dt


def _completed_job_record_within_api_ttl(completed_at, ttl_seconds: object) -> bool:
    if completed_at is None:
        return False
    completed_dt = _parse_iso_datetime_utc(completed_at)
    ttl_value = _coerce_positive_ttl_seconds(ttl_seconds)
    if completed_dt is None or ttl_value is None:
        return False
    age_seconds = (datetime.now(UTC) - completed_dt).total_seconds()
    return age_seconds <= ttl_value


def _queue_still_has_visible_jobs(queue) -> bool:
    connect = getattr(queue, "_connect", None)
    if not callable(connect):
        return True
    try:
        with closing(connect()) as conn:
            rows = conn.execute("""
                SELECT status, completed_at, ttl_seconds, legacy_expires_at
                FROM jobs
                """).fetchall()
    except sqlite3.Error as exc:
        raise _TransientLegacyQueueReadError(str(exc)) from exc
    except (AttributeError, TypeError, KeyError) as exc:
        raise _TransientLegacyQueueReadError(f"legacy queue inspection failed: {exc}") from exc
    now = datetime.now(UTC)
    return any(_job_row_is_visible(row, now) for row in rows)
