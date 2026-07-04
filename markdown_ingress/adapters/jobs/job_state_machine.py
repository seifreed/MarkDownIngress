"""Atomic job state transitions for the SQLite job queue."""

from __future__ import annotations

import json
from contextlib import closing
from typing import Any

from markdown_ingress.adapters.jobs.job_queue_models import JobAlreadyRunningError, utcnow
from markdown_ingress.adapters.jobs.job_queue_sql import (
    SQL_JOBS_SELECT_STATUS,
    SQL_JOBS_UPDATE_COMPLETE_PRESERVE_RESULT,
    SQL_JOBS_UPDATE_FAIL_STANDARD,
    SQL_JOBS_UPDATE_RUNNING,
    SQL_JOBS_UPDATE_RUNNING_TO_COMPLETED,
    SQL_JOBS_UPDATE_WEBHOOK_FAILED,
)
from markdown_ingress.adapters.jobs.job_queue_states import (
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
)


class JobStateMachineMixin:
    """Persisted job status transitions backed by atomic SQLite updates."""

    @staticmethod
    def _job_status(conn: Any, job_id: str) -> str | None:
        row = conn.execute(
            SQL_JOBS_SELECT_STATUS,
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
                SQL_JOBS_UPDATE_RUNNING,
                (JOB_STATUS_RUNNING, utcnow(), job_id, JOB_STATUS_QUEUED),
            )
            conn.commit()
            if cursor.rowcount == 0:
                status = self._job_status(conn, job_id)
                if status is None:
                    raise RuntimeError(f"Job {job_id} not found")
                if status == JOB_STATUS_RUNNING:
                    raise JobAlreadyRunningError(
                        f"Job {job_id} is already running and cannot be executed twice"
                    )
                raise self._invalid_transition(job_id, status, JOB_STATUS_QUEUED)

    def _mark_completed(self: Any, job_id: str, result: dict[str, Any]) -> None:
        """Transition job from 'running' to 'completed'."""
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                SQL_JOBS_UPDATE_RUNNING_TO_COMPLETED,
                (JOB_STATUS_COMPLETED, utcnow(), json.dumps(result), job_id, JOB_STATUS_RUNNING),
            )
            conn.commit()
            if cursor.rowcount == 0:
                status = self._job_status(conn, job_id)
                if status is None:
                    raise RuntimeError(f"Job {job_id} not found")
                if status == JOB_STATUS_COMPLETED:
                    return
                raise self._invalid_transition(job_id, status, JOB_STATUS_RUNNING)

    def _mark_failed(self: Any, job_id: str, error: str) -> None:
        """Transition job from 'queued' or 'running' to 'failed'."""
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                SQL_JOBS_UPDATE_FAIL_STANDARD,
                (
                    JOB_STATUS_FAILED,
                    utcnow(),
                    error,
                    job_id,
                    JOB_STATUS_QUEUED,
                    JOB_STATUS_RUNNING,
                ),
            )
            conn.commit()
            if cursor.rowcount == 0:
                status = self._job_status(conn, job_id)
                if status is None:
                    return
                if status == JOB_STATUS_FAILED:
                    return
                raise RuntimeError(
                    f"Invalid state transition: job {job_id} is '{status}', "
                    "cannot transition to 'failed'"
                )

    def _mark_webhook_failed(self: Any, job_id: str, error: str) -> None:
        """Transition completed job to failed while preserving result_json."""
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                SQL_JOBS_UPDATE_WEBHOOK_FAILED,
                (JOB_STATUS_FAILED, error, job_id, JOB_STATUS_COMPLETED),
            )
            conn.commit()
            if cursor.rowcount == 0:
                status = self._job_status(conn, job_id)
                if status is None:
                    return
                if status == JOB_STATUS_FAILED:
                    return
                raise self._invalid_transition(job_id, status, JOB_STATUS_COMPLETED)

    def _mark_completed_preserve_result(
        self: Any, job_id: str, result: dict[str, Any], error: str
    ) -> None:
        """Preserve result while marking a running job failed after lease loss."""
        result_json = json.dumps(result) if result is not None else None
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                SQL_JOBS_UPDATE_COMPLETE_PRESERVE_RESULT,
                (
                    JOB_STATUS_FAILED,
                    utcnow(),
                    result_json,
                    error,
                    job_id,
                    JOB_STATUS_RUNNING,
                ),
            )
            conn.commit()
            if cursor.rowcount == 0:
                return
