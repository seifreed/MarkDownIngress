"""Expired-job cleanup for the SQLite job queue."""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import Any

from markdown_ingress.adapters.jobs.job_queue_models import LEGACY_UNKNOWN_TTL_SECONDS, utcnow
from markdown_ingress.adapters.jobs.job_queue_sql import (
    SQL_BEGIN_IMMEDIATE,
    SQL_JOBS_DELETE_BY_ID,
    SQL_JOBS_DELETE_CORRUPT_LEGACY,
    SQL_JOBS_DELETE_CORRUPT_TTL,
    SQL_JOBS_DELETE_LEGACY_EXPIRED,
    SQL_JOBS_DELETE_TTL_EXPIRED,
    SQL_JOBS_SELECT_COMPLETED_WITHOUT_TTL,
    SQL_JOBS_UPDATE_LEGACY_TTL_WITH_TTL,
)
from markdown_ingress.adapters.jobs.job_queue_states import (
    JOB_STATUS_ACTIVE,
    JOB_STATUS_FINISHED,
)


class JobCleanupMixin:
    """TTL and legacy-expiry cleanup queries for persisted jobs."""

    def _delete_ttl_expired_jobs(self, conn: Any, now_iso: str) -> None:
        conn.execute(
            SQL_JOBS_DELETE_TTL_EXPIRED,
            (*JOB_STATUS_ACTIVE, now_iso),
        )

    def _delete_corrupt_ttl_jobs(self, conn: Any) -> None:
        conn.execute(
            SQL_JOBS_DELETE_CORRUPT_TTL,
            (*JOB_STATUS_ACTIVE,),
        )

    def _delete_legacy_expired_jobs(self, conn: Any, now_iso: str) -> None:
        conn.execute(
            SQL_JOBS_DELETE_LEGACY_EXPIRED,
            (*JOB_STATUS_ACTIVE, now_iso),
        )

    def _delete_corrupt_legacy_jobs(self, conn: Any) -> None:
        conn.execute(
            SQL_JOBS_DELETE_CORRUPT_LEGACY,
            (*JOB_STATUS_ACTIVE,),
        )

    def _compute_legacy_ttl_updates(
        self: Any, conn: Any, now_dt: datetime
    ) -> tuple[list[tuple[int, str, str]], list[str]]:
        rows = conn.execute(
            SQL_JOBS_SELECT_COMPLETED_WITHOUT_TTL,
            JOB_STATUS_FINISHED,
        ).fetchall()
        updates: list[tuple[int, str, str]] = []
        expired_ids: list[str] = []
        for row in rows:
            try:
                completed_dt = self._parse_iso(row["completed_at"])
            except ValueError:
                continue
            expires_at = (completed_dt + timedelta(seconds=LEGACY_UNKNOWN_TTL_SECONDS)).isoformat()
            if completed_dt + timedelta(seconds=LEGACY_UNKNOWN_TTL_SECONDS) <= now_dt:
                expired_ids.append(row["job_id"])
            else:
                updates.append((LEGACY_UNKNOWN_TTL_SECONDS, expires_at, row["job_id"]))
        return updates, expired_ids

    def _apply_legacy_ttl_backfill(
        self, conn: Any, updates: list[tuple[int, str, str]], expired_ids: list[str]
    ) -> None:
        if expired_ids:
            conn.executemany(SQL_JOBS_DELETE_BY_ID, ((job_id,) for job_id in expired_ids))
        if updates:
            conn.executemany(SQL_JOBS_UPDATE_LEGACY_TTL_WITH_TTL, updates)

    def cleanup_expired(self: Any) -> None:
        now_iso = utcnow()
        now_dt = datetime.now(UTC)
        with closing(self._connect()) as conn:
            conn.execute(SQL_BEGIN_IMMEDIATE)
            self._delete_corrupt_ttl_jobs(conn)
            self._delete_ttl_expired_jobs(conn, now_iso)
            self._delete_legacy_expired_jobs(conn, now_iso)
            self._delete_corrupt_legacy_jobs(conn)
            updates, expired_ids = self._compute_legacy_ttl_updates(conn, now_dt)
            self._apply_legacy_ttl_backfill(conn, updates, expired_ids)
            conn.commit()
