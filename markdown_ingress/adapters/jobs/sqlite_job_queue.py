"""SQLite-backed job queue adapter."""

from __future__ import annotations

import json
import logging
import queue
import sqlite3
import threading
import uuid
from collections.abc import Callable
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from typing import Any

from markdown_ingress.adapters.jobs.job_queue_cleanup import JobCleanupMixin
from markdown_ingress.adapters.jobs.job_queue_execution import JobQueueExecutionMixin
from markdown_ingress.adapters.jobs.job_queue_lifecycle import JobQueueLifecycleMixin
from markdown_ingress.adapters.jobs.job_queue_models import (
    LEGACY_UNKNOWN_TTL_SECONDS,
    STOP_WORKER,
    JobRecord,
)
from markdown_ingress.adapters.jobs.job_queue_models import (
    utcnow as _utcnow,
)
from markdown_ingress.adapters.jobs.job_queue_security import (
    resolve_and_validate_ip as _resolve_and_validate_ip,
)
from markdown_ingress.adapters.jobs.job_queue_security import (
    validate_int_config as _validate_int_config,
)
from markdown_ingress.adapters.jobs.job_queue_security import (
    validate_job_db_path as _validate_job_db_path,
)
from markdown_ingress.adapters.jobs.job_queue_security import (
    validate_optional_positive_finite_float as _validate_optional_positive_finite_float,
)
from markdown_ingress.adapters.jobs.job_queue_security import (
    validate_webhook_url as _validate_webhook_url,
)
from markdown_ingress.adapters.jobs.job_state_machine import JobStateMachineMixin
from markdown_ingress.adapters.webhooks.http_notifier import HTTPWebhookNotifier
from markdown_ingress.core.interfaces import IWebhookNotifier

_logger = logging.getLogger(__name__)

# Re-export so callers outside this module don't need to import sqlite3 directly.
SQLiteError = sqlite3.Error
_STOP_WORKER = STOP_WORKER


class PersistentJobQueue(
    JobQueueLifecycleMixin,
    JobCleanupMixin,
    JobQueueExecutionMixin,
    JobStateMachineMixin,
):
    """SQLite-backed worker queue for long-running API jobs."""

    # Queue lifecycle constructor keeps operational limits explicit for callers.
    def __init__(  # noqa: PLR0913
        self,
        db_path: str,
        worker_count: int = 2,
        ttl_seconds: int = 3600,
        max_queued_jobs: int = 100,
        webhook_max_retries: int = 2,
        webhook_retry_delay_seconds: float = 0.25,
        notifier: IWebhookNotifier | None = None,
        allow_local_webhooks: bool = False,
        job_timeout_seconds: float | None = None,
    ):
        self.db_path = _validate_job_db_path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.worker_count = _validate_int_config("worker_count", worker_count, minimum=1)
        self.ttl_seconds = _validate_int_config("ttl_seconds", ttl_seconds, minimum=60)
        self.max_queued_jobs = _validate_int_config("max_queued_jobs", max_queued_jobs, minimum=1)
        self.instance_id = str(uuid.uuid4())
        self.lease_timeout_seconds = 30.0
        self.heartbeat_interval_seconds = 5.0
        self.lease_acquire_max_retries = 5
        self.lease_acquire_base_delay = 0.1
        self.lease_acquire_max_delay = 5.0
        self.notifier = notifier or HTTPWebhookNotifier(
            max_retries=webhook_max_retries,
            retry_delay_seconds=webhook_retry_delay_seconds,
            allow_local_webhooks=allow_local_webhooks,
        )
        self.allow_local_webhooks = allow_local_webhooks
        # Apply allow_local_webhooks to an injected notifier too so the
        # defense-in-depth SSRF check respects the queue's policy.
        if hasattr(self.notifier, "allow_local_webhooks"):
            self.notifier.allow_local_webhooks = allow_local_webhooks
        self.job_timeout_seconds = _validate_optional_positive_finite_float(
            "job_timeout_seconds", job_timeout_seconds
        )
        self._queue: queue.Queue[tuple[str, Callable[[], dict[str, Any]]] | object] = queue.Queue()
        self._lock = threading.RLock()
        self._state_changed = threading.Condition(self._lock)
        self._workers: list[threading.Thread] = []
        self._workers_started = False
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._closed = False
        self._closing = False
        self._lease_lost = False
        self._shutdown_complete = False
        self._recovered_orphaned_jobs = False
        self._worker_stop_requested = False
        self._inline_jobs_running = 0
        self._init_db()
        self._acquire_lease()
        self._recover_orphaned_jobs()
        self._start_lease_heartbeat()

    def _connect(self) -> sqlite3.Connection:
        # Re-validate path at open time to close the TOCTOU window between validation
        # in __init__ and the actual sqlite3.connect() call.
        validated = _validate_job_db_path(self.db_path)
        if validated != self.db_path:
            raise ValueError(
                "Job DB path changed between validation and open: "
                f"{self.db_path!r} -> {validated!r}"
            )
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _resolve_webhook_hostname_ip(hostname: str) -> str:
        return _resolve_and_validate_ip(hostname)

    def _init_db(self) -> None:
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    result_json TEXT,
                    error TEXT,
                    webhook_url TEXT,
                    ttl_seconds INTEGER,
                    legacy_expires_at TEXT
                )
                """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS queue_leases (
                    lease_name TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL
                )
                """)
            job_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
            }
            if "ttl_seconds" not in job_columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN ttl_seconds INTEGER")
            if "legacy_expires_at" not in job_columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN legacy_expires_at TEXT")
            legacy_rows = conn.execute("""
                SELECT job_id, completed_at
                FROM jobs
                WHERE completed_at IS NOT NULL
                  AND ttl_seconds IS NULL
                  AND legacy_expires_at IS NULL
                """).fetchall()
            if legacy_rows:
                updates = []
                for row in legacy_rows:
                    completed_at = row["completed_at"]
                    legacy_expires_at = None
                    if completed_at:
                        try:
                            completed_dt = self._parse_iso(completed_at)
                            legacy_expires_at = (
                                completed_dt + timedelta(seconds=LEGACY_UNKNOWN_TTL_SECONDS)
                            ).isoformat()
                        except ValueError:
                            legacy_expires_at = None
                    updates.append((legacy_expires_at, row["job_id"]))
                conn.executemany(
                    "UPDATE jobs SET legacy_expires_at = ? WHERE job_id = ?",
                    updates,
                )
            conn.commit()

    def _ensure_workers(self) -> None:
        with self._lock:
            if self._workers_started:
                return
            for _ in range(self.worker_count):
                worker = threading.Thread(target=self._worker_loop, daemon=True)
                worker.start()
                self._workers.append(worker)
            self._workers_started = True

    def submit(
        self,
        task: Callable[[], dict[str, Any]],
        webhook_url: str | None = None,
        start_immediately: bool = False,
    ) -> JobRecord:
        self._assert_queue_usable(require_lease=True)
        # Validate webhook_url for SSRF protection
        _validate_webhook_url(webhook_url, allow_local=self.allow_local_webhooks)
        job_id = str(uuid.uuid4())
        record = JobRecord(
            job_id=job_id,
            status="queued",
            created_at=_utcnow(),
            webhook_url=webhook_url,
            ttl_seconds=self.ttl_seconds,
        )
        run_inline = False
        job_inserted = False
        with self._lock:
            self._assert_queue_usable(require_lease=True)
            self.cleanup_expired()
            with closing(self._connect()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                lease_row = conn.execute(
                    "SELECT owner_id FROM queue_leases WHERE lease_name = ?",
                    ("default",),
                ).fetchone()
                if lease_row is None or lease_row["owner_id"] != self.instance_id:
                    conn.rollback()
                    self._lease_lost = True
                    raise RuntimeError(
                        "Job queue lease was lost; this instance can no longer accept "
                        "or execute jobs"
                    )
                row = conn.execute(
                    "SELECT COUNT(*) AS count FROM jobs WHERE status IN ('queued','running')"
                ).fetchone()
                pending = int(row["count"]) if row else 0
                if pending >= self.max_queued_jobs:
                    conn.rollback()
                    raise RuntimeError("Job queue is full")
                conn.execute(
                    """
                    INSERT INTO jobs (job_id, status, created_at, webhook_url, ttl_seconds)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        record.job_id,
                        record.status,
                        record.created_at,
                        record.webhook_url,
                        record.ttl_seconds,
                    ),
                )
                conn.commit()
            job_inserted = True
            try:
                self._assert_queue_usable(require_lease=True)
                if start_immediately:
                    self._inline_jobs_running += 1
                    run_inline = True
                else:
                    self._ensure_workers()
                    self._assert_queue_usable(require_lease=True, allow_closing=True)
                    self._queue.put((job_id, task))
            except Exception:
                if job_inserted:
                    self._delete_queued_job(job_id)
                raise
        if run_inline:
            try:
                self._execute_job(job_id, task)
            finally:
                with self._lock:
                    self._inline_jobs_running -= 1
                    self._state_changed.notify_all()
        return record

    def pending_count(self, *, cleanup_expired: bool = True) -> int:
        if cleanup_expired:
            self.cleanup_expired()
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM jobs WHERE status IN ('queued','running')"
            ).fetchone()
        return int(row["count"]) if row else 0

    @staticmethod
    def _safe_json_loads(raw: str | None) -> dict[str, Any] | None:
        if raw is None:
            return None
        try:
            result = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            _logger.warning("Corrupt result_json in job record, returning None")
            return None
        if result is None:
            return None
        if not isinstance(result, dict):
            _logger.warning(
                "Job result_json decoded to %s, expected object; returning None",
                type(result).__name__,
            )
            return None
        return result

    def get(self, job_id: str, *, cleanup_expired: bool = True) -> JobRecord | None:
        if cleanup_expired:
            self.cleanup_expired()
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        return JobRecord(
            job_id=row["job_id"],
            status=row["status"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            result=self._safe_json_loads(row["result_json"]),
            error=row["error"],
            webhook_url=row["webhook_url"],
            ttl_seconds=row["ttl_seconds"],
            legacy_expires_at=row["legacy_expires_at"],
        )


def check_external_owner_still_owns(
    db_path: Path,
    is_stale_fn: Callable[[str], bool],
    on_backend_error: Callable[[str], None] | None = None,
) -> bool:
    """Read the queue lease table to determine if an external owner's lease is still valid.

    Accepts ``is_stale_fn`` so the caller's heartbeat-staleness logic is injected
    rather than duplicated, keeping this module free of presentation-layer imports.
    Returns True if the lease is still held (not stale), False otherwise.
    Raises RuntimeError on unrecoverable backend errors.
    """
    db_uri = f"{db_path.resolve().as_uri()}?mode=ro"
    try:
        with closing(sqlite3.connect(db_uri, timeout=0.0, uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT owner_id, heartbeat_at FROM queue_leases WHERE lease_name = ?",
                ("default",),
            ).fetchone()
    except sqlite3.Error as exc:
        message = str(exc).lower()
        if isinstance(exc, sqlite3.OperationalError) and ("locked" in message or "busy" in message):
            return True
        if on_backend_error is not None:
            on_backend_error("backend_error")
        raise RuntimeError(f"Job queue backend read failed during repair: {exc}") from exc
    if row is None:
        return False
    heartbeat_at = row["heartbeat_at"]
    return not is_stale_fn(heartbeat_at)
