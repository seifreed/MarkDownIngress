"""Lifecycle, lease, and shutdown handling for the SQLite job queue."""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from contextlib import closing
from datetime import UTC, datetime
from typing import Any

from markdown_ingress.adapters.jobs.job_queue_models import STOP_WORKER, utcnow

_logger = logging.getLogger(__name__)


class JobQueueLifecycleMixin:
    """Lease ownership, heartbeat, lifecycle state, and shutdown behavior."""

    _heartbeat_thread: threading.Thread | None

    def _get_process_start_time(self: Any) -> float | None:
        """Get the current process start time for PID recycling detection."""
        try:
            start_ticks = JobQueueLifecycleMixin._parse_proc_stat_start_ticks(os.getpid())
            if start_ticks is not None:
                clk_tck = int(os.sysconf("SC_CLK_TCK"))
                if clk_tck > 0:
                    return float(start_ticks / clk_tck)
        except (OSError, ValueError, IndexError) as e:
            _logger.debug("Could not read process start time from /proc: %s", e)
        return None

    @staticmethod
    def _parse_proc_stat_start_ticks(pid: int) -> int | None:
        """Parse /proc/{pid}/stat safely, handling spaces in the process name."""
        proc_path = f"/proc/{pid}/stat"
        if not os.path.exists(proc_path):
            return None
        with open(proc_path, encoding="utf-8") as f:
            stat_line = f.read()
        closing_parenthesis = stat_line.rfind(")")
        if closing_parenthesis == -1:
            return None
        suffix = stat_line[closing_parenthesis + 1 :].strip()
        parts = suffix.split()
        if len(parts) > 19:
            return int(parts[19])
        return None

    @property
    def state(self: Any) -> str:
        """Expose the queue lifecycle state for callers coordinating shutdown/retry."""
        if self._lease_lost:
            return "lease_lost"
        if self._shutdown_complete or self._closed:
            return "closed"
        if self._closing:
            return "closing"
        return "open"

    def _parse_iso(self: Any, value: str) -> datetime:
        """Parse an ISO datetime string and normalize it to UTC."""
        try:
            parsed = datetime.fromisoformat(value)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid ISO datetime format: {value!r}") from exc
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _is_stale_heartbeat(self: Any, heartbeat_at: str) -> bool:
        heartbeat_dt = JobQueueLifecycleMixin._parse_iso(self, heartbeat_at)
        age_seconds = (datetime.now(UTC) - heartbeat_dt).total_seconds()
        return age_seconds > float(self.lease_timeout_seconds)

    def _is_owner_process_alive(
        self: Any, owner_pid: int, owner_start_time: float | None = None
    ) -> bool:
        """Check if a process with the given PID is still the expected owner."""
        if owner_pid <= 0:
            return False
        try:
            os.kill(owner_pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            pass

        if owner_start_time is not None:
            try:
                start_ticks = JobQueueLifecycleMixin._parse_proc_stat_start_ticks(owner_pid)
                if start_ticks is not None:
                    clk_tck = int(os.sysconf("SC_CLK_TCK"))
                    if clk_tck > 0:
                        start_seconds = start_ticks / clk_tck
                        if abs(start_seconds - owner_start_time) > 1.0:
                            return False
            except (OSError, ValueError, IndexError) as e:
                _logger.debug("Could not verify owner process start time: %s", e)
        return True

    def _assert_queue_usable(
        self: Any, *, require_lease: bool = False, allow_closing: bool = False
    ) -> None:
        if self._lease_lost:
            raise RuntimeError(
                "Job queue lease was lost; this instance can no longer accept or execute jobs"
            )
        if self._closed:
            raise RuntimeError("Job queue is closed")
        if self._closing and not allow_closing:
            raise RuntimeError("Job queue is closing")
        if require_lease and not self._still_owns_lease():
            self._lease_lost = True
            raise RuntimeError(
                "Job queue lease was lost; this instance can no longer accept or execute jobs"
            )

    def _still_owns_lease(self: Any) -> bool:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT owner_id FROM queue_leases WHERE lease_name = ?",
                ("default",),
            ).fetchone()
        return row is not None and row["owner_id"] == self.instance_id

    def _acquire_lease(self: Any) -> None:
        """Acquire lease with exponential backoff retry for lock contention."""
        last_error: Exception | None = None
        for attempt in range(self.lease_acquire_max_retries):
            try:
                with closing(self._connect()) as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    row = conn.execute(
                        "SELECT owner_id, heartbeat_at, owner_pid, owner_start_time "
                        "FROM queue_leases WHERE lease_name = ?",
                        ("default",),
                    ).fetchone()
                    now_iso = utcnow()
                    if row is None:
                        conn.execute(
                            "INSERT INTO queue_leases "
                            "(lease_name, owner_id, heartbeat_at, owner_pid, owner_start_time) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (
                                "default",
                                self.instance_id,
                                now_iso,
                                self.owner_pid,
                                self.owner_start_time,
                            ),
                        )
                    else:
                        owner_id = row["owner_id"]
                        heartbeat_at = row["heartbeat_at"]
                        previous_heartbeat_stale = self._is_stale_heartbeat(heartbeat_at)
                        if owner_id != self.instance_id and not previous_heartbeat_stale:
                            conn.rollback()
                            raise RuntimeError(
                                "Job queue DB is already owned by another active instance"
                            )
                        self._recovered_orphaned_jobs = owner_id == self.instance_id
                        conn.execute(
                            "UPDATE queue_leases "
                            "SET owner_id = ?, heartbeat_at = ?, owner_pid = ?, "
                            "owner_start_time = ? WHERE lease_name = ?",
                            (
                                self.instance_id,
                                now_iso,
                                self.owner_pid,
                                self.owner_start_time,
                                "default",
                            ),
                        )
                    conn.commit()
            except sqlite3.OperationalError as e:
                last_error = e
                if attempt < self.lease_acquire_max_retries - 1:
                    delay = min(
                        self.lease_acquire_base_delay * (2**attempt),
                        self.lease_acquire_max_delay,
                    )
                    time.sleep(delay)
                    continue
            else:
                return
        raise RuntimeError(
            f"Failed to acquire lease after {self.lease_acquire_max_retries} attempts: {last_error}"
        ) from last_error

    def _refresh_lease(self: Any) -> None:
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                UPDATE queue_leases
                SET heartbeat_at = ?, owner_pid = ?, owner_start_time = ?
                WHERE lease_name = ? AND owner_id = ?
                """,
                (utcnow(), self.owner_pid, self.owner_start_time, "default", self.instance_id),
            )
            conn.commit()
            updated = cursor.rowcount
        if updated == 0:
            raise RuntimeError("Job queue lease was lost")

    def _start_lease_heartbeat(self: Any) -> None:
        max_heartbeat_retries = 3
        heartbeat_base_delay = 1.0
        heartbeat_max_delay = 10.0

        def heartbeat_loop() -> None:
            consecutive_failures = 0
            skip_interval = False
            while True:
                if not skip_interval:
                    if self._heartbeat_stop.wait(self.heartbeat_interval_seconds):
                        break
                skip_interval = False
                try:
                    self._refresh_lease()
                    consecutive_failures = 0
                except (
                    sqlite3.OperationalError,
                    sqlite3.InterfaceError,
                    sqlite3.NotSupportedError,
                ):
                    consecutive_failures += 1
                    if consecutive_failures >= max_heartbeat_retries:
                        self._mark_lease_lost_and_stop_workers()
                        return
                    delay = min(
                        heartbeat_base_delay * (2 ** (consecutive_failures - 1)),
                        heartbeat_max_delay,
                    )
                    if self._heartbeat_stop.wait(timeout=delay):
                        break
                    skip_interval = True
                except Exception:  # pragma: no cover
                    _logger.critical("Heartbeat loop fatal error, shutting down", exc_info=True)
                    self._mark_lease_lost_and_stop_workers()
                    return

        self._heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

    def _mark_lease_lost_and_stop_workers(self: Any) -> None:
        with self._lock:
            self._lease_lost = True
            self._closed = True
            self._closing = True
            self._request_worker_stop_locked()

    def _request_worker_stop_locked(self: Any) -> None:
        if self._worker_stop_requested:
            return
        worker_count = len(self._workers)
        for _ in range(worker_count):
            self._queue.put(STOP_WORKER)
        self._worker_stop_requested = True

    def _wait_for_existing_close_locked(self: Any) -> bool:
        if not getattr(self, "_close_in_progress", False):
            return False
        while getattr(self, "_close_in_progress", False) and not self._shutdown_complete:
            self._state_changed.wait(timeout=0.1)
        return bool(self._shutdown_complete)

    def _validate_close_preserves_state_locked(
        self: Any,
        preserve_state_on_inline_timeout: bool,
    ) -> None:
        if not preserve_state_on_inline_timeout:
            return
        if self._inline_jobs_running > 0:
            raise RuntimeError("Job queue inline jobs did not stop before lease release")
        if any(worker.is_alive() for worker in self._workers):
            raise RuntimeError("Job queue workers did not stop before lease release")

    def _wait_for_inline_jobs_before_close_locked(
        self: Any,
        inline_wait_timeout: float | None,
    ) -> None:
        deadline = None if inline_wait_timeout is None else time.monotonic() + inline_wait_timeout
        while self._inline_jobs_running > 0:
            if deadline is None:
                self._state_changed.wait(timeout=0.1)
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("Job queue inline jobs did not stop before lease release")
            self._state_changed.wait(timeout=min(0.1, remaining))

    def _begin_close_and_wait_inline_jobs(
        self: Any,
        *,
        inline_wait_timeout: float | None,
        preserve_state_on_inline_timeout: bool,
    ) -> bool:
        with self._lock:
            if self._shutdown_complete:
                return False
            if self._wait_for_existing_close_locked():
                return False
            self._validate_close_preserves_state_locked(preserve_state_on_inline_timeout)
            self._close_in_progress = True
            self._closing = True
            self._request_worker_stop_locked()
            try:
                self._wait_for_inline_jobs_before_close_locked(inline_wait_timeout)
            except BaseException:
                self._close_in_progress = False
                self._state_changed.notify_all()
                raise
            return True

    def _join_workers_for_close(self: Any) -> None:
        for worker in list(self._workers):
            worker.join(timeout=2.0)
        still_alive = [worker for worker in self._workers if worker.is_alive()]
        if still_alive:
            raise RuntimeError("Job queue workers did not stop before lease release")

    def _stop_heartbeat_for_close(self: Any) -> None:
        self._heartbeat_stop.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=2.0)

    def _release_lease_for_close(self: Any) -> None:
        with closing(self._connect()) as conn:
            if not self._lease_lost:
                conn.execute(
                    "DELETE FROM queue_leases WHERE lease_name = ? AND owner_id = ?",
                    ("default", self.instance_id),
                )
            conn.commit()

    def _mark_shutdown_complete(self: Any) -> None:
        with self._lock:
            self._closed = True
            self._shutdown_complete = True
            self._state_changed.notify_all()

    def _release_failed_close_claim(self: Any) -> None:
        with self._lock:
            self._close_in_progress = False
            self._state_changed.notify_all()

    def close(
        self: Any,
        *,
        inline_wait_timeout: float | None = None,
        preserve_state_on_inline_timeout: bool = False,
    ) -> None:
        if self._shutdown_complete:
            return
        claimed_close = False
        try:
            claimed_close = self._begin_close_and_wait_inline_jobs(
                inline_wait_timeout=inline_wait_timeout,
                preserve_state_on_inline_timeout=preserve_state_on_inline_timeout,
            )
            if not claimed_close:
                return
            self._join_workers_for_close()
            self._stop_heartbeat_for_close()
            self._release_lease_for_close()
            self._mark_shutdown_complete()
        finally:
            if claimed_close and not self._shutdown_complete:
                self._release_failed_close_claim()

    def _recover_orphaned_jobs(self: Any) -> None:
        """Fail queued/running jobs from a previous process instance."""
        if self._recovered_orphaned_jobs:
            return
        with closing(self._connect()) as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'failed',
                    completed_at = ?,
                    result_json = NULL,
                    ttl_seconds = COALESCE(ttl_seconds, ?),
                    error = CASE
                        WHEN status = 'running'
                            THEN 'Job interrupted by process restart; persisted task '
                                 || 'payload is not recoverable'
                        ELSE 'Job abandoned after process restart; persisted task '
                             || 'payload is not recoverable'
                    END
                WHERE status IN ('queued', 'running')
                """,
                (utcnow(), self.ttl_seconds),
            )
            conn.commit()
        self._recovered_orphaned_jobs = True
