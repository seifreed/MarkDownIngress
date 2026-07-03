"""Job-queue observability snapshot assembly for the API server."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, TypedDict, cast

from markdown_ingress.api_server_queue import _legacy_unknown_ttl_expires_at


class JobSubsystemSnapshot(TypedDict):
    status: str
    current_state: str
    current_db_path: str
    current_ttl_seconds: int | None
    current_max_queued_jobs: int | None
    current_pending: int | None
    legacy_pending: int
    pending_visible_total: int | None
    legacy_visible_queues: int
    legacy_db_paths: list[str]
    pending_unknown: bool
    current_unknown_ttl_jobs: int
    legacy_unknown_ttl_jobs: int
    legacy_unknown_ttl_seconds: int
    repair_in_progress: bool


class RepairThread(Protocol):
    def is_alive(self) -> bool: ...


@dataclass(frozen=True)
class JobSubsystemSnapshotInputs:
    current_queue: object | None
    history: Sequence[object]
    repair_thread: RepairThread | None
    job_db_path: str
    legacy_unknown_ttl_seconds: int
    logger: logging.Logger


def _optional_int_attr(source: object | None, name: str) -> int | None:
    value = getattr(source, name, None)
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _read_pending_count(queue_obj: Any) -> int | None:
    if queue_obj is None:
        return None
    try:
        return cast(int, queue_obj.pending_count(cleanup_expired=False))
    except (RuntimeError, sqlite3.Error):
        return None
    except TypeError:
        try:
            return cast(int, queue_obj.pending_count())
        except (RuntimeError, sqlite3.Error):
            return None


def _count_unknown_ttl_jobs(
    queue_obj: Any,
    logger: logging.Logger,
    now: datetime,
) -> int:
    """Count legacy jobs with unknown TTL that are still visible at ``now``."""
    connect = getattr(queue_obj, "_connect", None)
    if not callable(connect):
        return 0
    try:
        with closing(connect()) as conn:
            rows = conn.execute("""
                SELECT completed_at, legacy_expires_at
                FROM jobs
                WHERE ttl_seconds IS NULL
                """).fetchall()
    except (sqlite3.Error, OSError, ValueError) as exc:
        logger.warning("Error counting unknown TTL jobs: %s", exc, exc_info=True)
        return 0
    count = 0
    for row in rows:
        completed_at = row["completed_at"] if hasattr(row, "keys") else row[0]
        legacy_expires_at = row["legacy_expires_at"] if hasattr(row, "keys") else row[1]
        expires_dt = _legacy_unknown_ttl_expires_at(completed_at, legacy_expires_at)
        if expires_dt is None:
            continue
        if now <= expires_dt:
            count += 1
    return count


def build_job_subsystem_snapshot(
    inputs: JobSubsystemSnapshotInputs,
) -> JobSubsystemSnapshot:
    current_queue = inputs.current_queue
    current_ttl_seconds = _optional_int_attr(current_queue, "ttl_seconds")
    current_max_queued_jobs = _optional_int_attr(current_queue, "max_queued_jobs")
    current_state = str(getattr(current_queue, "state", "uninitialized"))
    current_pending = _read_pending_count(current_queue)
    legacy_pending = 0
    legacy_unknown_ttl_jobs = 0
    legacy_visible_queues = 0
    legacy_db_paths: list[str] = []
    seen_db_paths: set[str] = set()
    if current_queue is not None:
        seen_db_paths.add(str(getattr(current_queue, "db_path", inputs.job_db_path)))
    pending_unknown = current_pending is None
    snapshot_now = datetime.now(UTC)
    current_unknown_ttl_jobs = _count_unknown_ttl_jobs(current_queue, inputs.logger, snapshot_now)
    for legacy_queue in inputs.history:
        raw_legacy_db_path = getattr(legacy_queue, "db_path", None)
        legacy_db_path = str(raw_legacy_db_path) if raw_legacy_db_path is not None else None
        if legacy_db_path is not None:
            if legacy_db_path in seen_db_paths:
                continue
            seen_db_paths.add(legacy_db_path)
        legacy_value = _read_pending_count(legacy_queue)
        if legacy_value is None:
            pending_unknown = True
            continue
        legacy_pending += legacy_value
        legacy_unknown_ttl_jobs += _count_unknown_ttl_jobs(
            legacy_queue, inputs.logger, snapshot_now
        )
        legacy_visible_queues += 1
        if legacy_db_path is not None:
            legacy_db_paths.append(legacy_db_path)

    return {
        "status": "healthy" if current_state == "open" and not pending_unknown else "degraded",
        "current_state": current_state,
        "current_db_path": str(getattr(current_queue, "db_path", inputs.job_db_path)),
        "current_ttl_seconds": current_ttl_seconds,
        "current_max_queued_jobs": current_max_queued_jobs,
        "current_pending": current_pending,
        "legacy_pending": legacy_pending,
        "pending_visible_total": (
            None if pending_unknown or current_pending is None else current_pending + legacy_pending
        ),
        "legacy_visible_queues": legacy_visible_queues,
        "legacy_db_paths": legacy_db_paths,
        "pending_unknown": pending_unknown,
        "current_unknown_ttl_jobs": current_unknown_ttl_jobs,
        "legacy_unknown_ttl_jobs": legacy_unknown_ttl_jobs,
        "legacy_unknown_ttl_seconds": inputs.legacy_unknown_ttl_seconds,
        "repair_in_progress": bool(
            inputs.repair_thread is not None and inputs.repair_thread.is_alive()
        ),
    }
