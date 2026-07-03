"""Worker execution and webhook delivery for the SQLite job queue."""

from __future__ import annotations

import json
import logging
import os
import socket
import sqlite3
import threading
from collections.abc import Callable
from contextlib import closing
from typing import Any, cast
from urllib.parse import urlparse

from markdown_ingress.adapters.jobs.job_queue_models import (
    STOP_WORKER,
    JobAlreadyRunningError,
    utcnow,
)
from markdown_ingress.adapters.jobs.job_queue_security import validate_positive_finite_float

_logger = logging.getLogger(__name__)
JOB_QUEUE_STATE_ERRORS: tuple[type[Exception], ...] = (
    OSError,
    RuntimeError,
    sqlite3.Error,
    TypeError,
    ValueError,
)


class JobQueueExecutionMixin:
    """Worker dispatch, task execution, timeout, and webhook delivery."""

    def _worker_loop(self: Any) -> None:
        while True:
            item = self._queue.get()
            job_id = None
            try:
                if item is STOP_WORKER:
                    return
                job_id, task = cast(tuple[str, Callable[[], dict[str, Any]]], item)
                self._assert_queue_usable(require_lease=True, allow_closing=True)
                self._execute_job(job_id, task)
            except JobAlreadyRunningError as exc:
                _logger.info("Skipping duplicate execution for job %s: %s", job_id, exc)
            except Exception as exc:  # noqa: BLE001 - worker boundary marks jobs failed
                _logger.warning("Worker loop exception for job %s: %s", job_id, exc)
                if job_id is not None:
                    self._mark_worker_job_failed(job_id, exc)
            finally:
                self._queue.task_done()

    def _mark_worker_job_failed(self: Any, job_id: str, exc: Exception) -> None:
        try:
            job = self.get(job_id, cleanup_expired=False)
            if job is not None and job.status not in {"failed", "completed"}:
                self._mark_failed(job_id, str(exc))
        except JOB_QUEUE_STATE_ERRORS as mark_exc:
            _logger.warning(
                "Could not mark job %s as failed: %s",
                job_id,
                mark_exc,
                exc_info=True,
            )
            self._force_mark_running_job_failed(job_id, str(exc)[:500])

    def _force_mark_running_job_failed(self: Any, job_id: str, error: str) -> None:
        try:
            with closing(self._connect()) as conn:
                conn.execute(
                    "UPDATE jobs SET status='failed', error=?, "
                    "completed_at=? WHERE job_id=? AND status='running'",
                    (error, utcnow(), job_id),
                )
                conn.commit()
        except Exception as fallback_exc:
            _logger.error(
                "Fallback mark-failed also failed for job %s: %s",
                job_id,
                fallback_exc,
                exc_info=True,
            )

    def _delete_queued_job(self: Any, job_id: str) -> None:
        """Remove a queued job that was persisted but never successfully accepted."""
        with closing(self._connect()) as conn:
            conn.execute(
                "DELETE FROM jobs WHERE job_id = ? AND status = 'queued'",
                (job_id,),
            )
            conn.commit()

    def _execute_job(self: Any, job_id: str, task: Callable[[], dict[str, Any]]) -> None:
        self._assert_queue_usable(require_lease=True, allow_closing=True)
        self._mark_running(job_id)
        try:
            result = self._run_task_for_job(task)
        except Exception as exc:
            self._mark_job_failed_preserving_original_error(job_id, exc)
            raise
        self._persist_result_or_fail_on_lease_loss(job_id, result)
        job = self.get(job_id, cleanup_expired=False)
        if job and job.webhook_url:
            self._deliver_job_webhook(job_id, job)

    def _run_task_for_job(self: Any, task: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        if self.job_timeout_seconds is not None:
            result = self._execute_with_timeout(task, self.job_timeout_seconds)
        else:
            result = task()
        if result is None:
            raise RuntimeError("Task returned None result")
        if not isinstance(result, dict):
            raise RuntimeError("Task returned non-dict result")
        json.dumps(result)
        return result

    def _mark_job_failed_preserving_original_error(self: Any, job_id: str, exc: Exception) -> None:
        try:
            self._mark_failed(job_id, str(exc))
        except JOB_QUEUE_STATE_ERRORS:
            _logger.warning(
                "Failed to mark job %s as failed (original error: %s)",
                job_id,
                exc,
            )

    def _persist_result_or_fail_on_lease_loss(
        self: Any, job_id: str, result: dict[str, Any]
    ) -> None:
        if not self._still_owns_lease():
            self._lease_lost = True
            self._closed = True
            try:
                self._mark_completed_preserve_result(
                    job_id, result, "Job queue lease was lost before result persistence"
                )
            except JOB_QUEUE_STATE_ERRORS as e:
                _logger.debug("Failed to preserve job result on lease loss: %s", e)
                self._mark_failed(job_id, "Job queue lease was lost before result persistence")
            raise RuntimeError("Job queue lease was lost before result persistence")
        self._mark_completed(job_id, result)

    def _deliver_job_webhook(self: Any, job_id: str, job: Any) -> None:
        if not self._still_owns_lease():
            self._lease_lost = True
            self._closed = True
            raise RuntimeError("Job queue lease was lost before webhook delivery")

        validated_ip = self._validated_webhook_ip(job_id, job.webhook_url)
        try:
            self.notifier.notify(
                job.webhook_url,
                {
                    "job_id": job.job_id,
                    "status": job.status,
                    "completed_at": job.completed_at,
                    "result": job.result,
                    "error": job.error,
                },
                validated_ip=validated_ip,
            )
        except Exception as exc:
            self._mark_webhook_failed(job_id, f"Webhook delivery failed: {exc}")
            raise

    def _validated_webhook_ip(self: Any, job_id: str, webhook_url: str) -> str | None:
        if self.allow_local_webhooks:
            return None
        try:
            parsed = urlparse(webhook_url)
            hostname = parsed.hostname
        except Exception as e:
            _logger.warning("Failed to parse webhook URL: %s", str(e)[:100])
            self._mark_webhook_failed(job_id, f"Invalid webhook URL: {e}")
            raise RuntimeError(f"Invalid webhook URL: {e}") from e
        if hostname is None:
            _logger.warning("Webhook URL has no hostname: %s", webhook_url[:100])
            self._mark_webhook_failed(job_id, "Webhook URL has no hostname")
            raise RuntimeError("Webhook URL has no hostname")
        try:
            resolver = cast(Callable[[str], str], self._resolve_webhook_hostname_ip)
            return resolver(hostname)
        except socket.gaierror as exc:
            self._mark_webhook_failed(job_id, "Webhook URL DNS resolution failed (transient)")
            raise RuntimeError("Webhook URL DNS resolution failed (transient)") from exc
        except ValueError as exc:
            self._mark_webhook_failed(job_id, f"Webhook URL blocked by SSRF policy: {exc}")
            raise RuntimeError(f"Webhook URL blocked by SSRF policy: {exc}") from exc

    def _execute_with_timeout(
        self: Any, task: Callable[[], dict[str, Any]], timeout_seconds: float
    ) -> dict[str, Any]:
        """Execute a task and return control to the caller when the timeout expires."""
        timeout_seconds = validate_positive_finite_float("timeout_seconds", timeout_seconds)
        if os.name != "posix":
            return JobQueueExecutionMixin._execute_with_timeout_thread_fallback(
                self, task, timeout_seconds
            )
        return JobQueueExecutionMixin._execute_with_timeout_process(self, task, timeout_seconds)

    def _execute_with_timeout_process(
        self: Any,
        task: Callable[[], dict[str, Any]],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        return JobQueueExecutionMixin._execute_with_timeout_thread_fallback(
            self, task, timeout_seconds
        )

    def _execute_with_timeout_thread_fallback(
        self: Any,
        task: Callable[[], dict[str, Any]],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """Execute a task in a daemon thread with a caller-visible timeout."""
        result_container: list[dict[str, Any] | None] = [None]
        exception_container: list[BaseException | None] = [None]

        def run_task() -> None:
            try:
                result_container[0] = task()
            except BaseException as e:  # noqa: BLE001 - thread bridge preserves caller errors
                exception_container[0] = e

        thread = threading.Thread(target=run_task, daemon=True)
        thread.start()
        thread.join(timeout=timeout_seconds)

        if thread.is_alive():
            _logger.warning(
                "Job execution timed out after %s seconds; "
                "background thread may continue running until the task returns.",
                timeout_seconds,
            )
            raise RuntimeError(f"Job execution timed out after {timeout_seconds} seconds")

        if exception_container[0] is not None:
            raise exception_container[0]

        if result_container[0] is None:
            raise RuntimeError("Task returned None result")

        return result_container[0]
