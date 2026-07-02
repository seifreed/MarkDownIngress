"""Atomic job state transitions for the SQLite job queue."""

from __future__ import annotations

import json
from contextlib import closing
from typing import Any

from markdown_ingress.adapters.jobs.job_queue_models import JobAlreadyRunningError, utcnow


class JobStateMachineMixin:
    """Persisted job status transitions backed by atomic SQLite updates."""

    @staticmethod
    def _job_status(conn: Any, job_id: str) -> str | None:
        row = conn.execute(
            "SELECT status FROM jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        return None if row is None else row["status"]

    @staticmethod
    def _invalid_transition(job_id: str, status: str, expected: str) -> RuntimeError:
        return RuntimeError(
            f"Invalid state transition: job {job_id} is '{status}', expected '{expected}'"
        )

    def _mark_running(self: Any, job_id: str) -> None:
        """Transition job from 'queued' to 'running'."""
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                "UPDATE jobs SET status = ?, started_at = ? WHERE job_id = ? AND status = ?",
                ("running", utcnow(), job_id, "queued"),
            )
            conn.commit()
            if cursor.rowcount == 0:
                status = self._job_status(conn, job_id)
                if status is None:
                    raise RuntimeError(f"Job {job_id} not found")
                if status == "running":
                    raise JobAlreadyRunningError(
                        f"Job {job_id} is already running and cannot be executed twice"
                    )
                raise self._invalid_transition(job_id, status, "queued")

    def _mark_completed(self: Any, job_id: str, result: dict[str, Any]) -> None:
        """Transition job from 'running' to 'completed'."""
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status = ?, completed_at = ?, result_json = ?, error = NULL
                WHERE job_id = ? AND status = ?
                """,
                ("completed", utcnow(), json.dumps(result), job_id, "running"),
            )
            conn.commit()
            if cursor.rowcount == 0:
                status = self._job_status(conn, job_id)
                if status is None:
                    raise RuntimeError(f"Job {job_id} not found")
                if status == "completed":
                    return
                raise self._invalid_transition(job_id, status, "running")

    def _mark_failed(self: Any, job_id: str, error: str) -> None:
        """Transition job from 'queued' or 'running' to 'failed'."""
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status = ?, completed_at = ?, result_json = NULL, error = ?
                WHERE job_id = ? AND status IN ('queued', 'running')
                """,
                ("failed", utcnow(), error, job_id),
            )
            conn.commit()
            if cursor.rowcount == 0:
                status = self._job_status(conn, job_id)
                if status is None:
                    return
                if status == "failed":
                    return
                raise RuntimeError(
                    f"Invalid state transition: job {job_id} is '{status}', "
                    "cannot transition to 'failed'"
                )

    def _mark_webhook_failed(self: Any, job_id: str, error: str) -> None:
        """Transition completed job to failed while preserving result_json."""
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status = ?, error = ?
                WHERE job_id = ? AND status = 'completed'
                """,
                ("failed", error, job_id),
            )
            conn.commit()
            if cursor.rowcount == 0:
                status = self._job_status(conn, job_id)
                if status is None:
                    return
                if status == "failed":
                    return
                raise self._invalid_transition(job_id, status, "completed")

    def _mark_completed_preserve_result(
        self: Any, job_id: str, result: dict[str, Any], error: str
    ) -> None:
        """Preserve result while marking a running job failed after lease loss."""
        result_json = json.dumps(result) if result is not None else None
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status = ?, completed_at = ?, result_json = ?, error = ?
                WHERE job_id = ? AND status = 'running'
                """,
                ("failed", utcnow(), result_json, error, job_id),
            )
            conn.commit()
            if cursor.rowcount == 0:
                return
