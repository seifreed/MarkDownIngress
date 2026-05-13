"""Expired-job cleanup for the SQLite job queue."""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import Any

from markdown_ingress.adapters.jobs.job_queue_models import LEGACY_UNKNOWN_TTL_SECONDS, utcnow


class JobCleanupMixin:
    """TTL and legacy-expiry cleanup queries for persisted jobs."""

    def _delete_ttl_expired_jobs(self, conn: Any, now_iso: str) -> None:
        conn.execute(
            """
            DELETE FROM jobs
            WHERE status NOT IN ('queued', 'running')
              AND ttl_seconds IS NOT NULL
              AND (
                  completed_at IS NULL
                  OR julianday(completed_at) IS NULL
                  OR julianday(?) > julianday(completed_at) + (ttl_seconds / 86400.0)
              )
            """,
            (now_iso,),
        )

    def _delete_corrupt_ttl_jobs(self, conn: Any) -> None:
        conn.execute("""
            DELETE FROM jobs
            WHERE status NOT IN ('queued', 'running')
              AND ttl_seconds IS NOT NULL
              AND (
                  typeof(ttl_seconds) != 'integer'
                  OR ttl_seconds <= 0
              )
            """)

    def _delete_legacy_expired_jobs(self, conn: Any, now_iso: str) -> None:
        conn.execute(
            """
            DELETE FROM jobs
            WHERE status NOT IN ('queued', 'running')
              AND ttl_seconds IS NULL
              AND legacy_expires_at IS NOT NULL
              AND (
                  julianday(legacy_expires_at) IS NULL
                  OR julianday(?) > julianday(legacy_expires_at)
              )
            """,
            (now_iso,),
        )

    def _delete_corrupt_legacy_jobs(self, conn: Any) -> None:
        conn.execute("""
            DELETE FROM jobs
            WHERE status NOT IN ('queued', 'running')
              AND ttl_seconds IS NULL
              AND legacy_expires_at IS NULL
              AND (completed_at IS NULL OR julianday(completed_at) IS NULL)
            """)

    def _compute_legacy_ttl_updates(
        self: Any, conn: Any, now_dt: datetime
    ) -> tuple[list[tuple[int, str, str]], list[str]]:
        rows = conn.execute("""
            SELECT job_id, completed_at
            FROM jobs
            WHERE status NOT IN ('queued', 'running')
              AND completed_at IS NOT NULL
              AND ttl_seconds IS NULL
              AND legacy_expires_at IS NULL
            """).fetchall()
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
            conn.executemany(
                "DELETE FROM jobs WHERE job_id = ?",
                ((job_id,) for job_id in expired_ids),
            )
        if updates:
            conn.executemany(
                """
                UPDATE jobs
                SET ttl_seconds = ?,
                    legacy_expires_at = ?
                WHERE job_id = ?
                """,
                updates,
            )

    def cleanup_expired(self: Any) -> None:
        now_iso = utcnow()
        now_dt = datetime.now(UTC)
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._delete_corrupt_ttl_jobs(conn)
            self._delete_ttl_expired_jobs(conn, now_iso)
            self._delete_legacy_expired_jobs(conn, now_iso)
            self._delete_corrupt_legacy_jobs(conn)
            updates, expired_ids = self._compute_legacy_ttl_updates(conn, now_dt)
            self._apply_legacy_ttl_backfill(conn, updates, expired_ids)
            conn.commit()
