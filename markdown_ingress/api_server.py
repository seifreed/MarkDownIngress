"""
FastAPI server for MarkDownIngress.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request

from markdown_ingress.adapters.jobs.sqlite_job_queue import (
    LEGACY_UNKNOWN_TTL_SECONDS,
    JobRecord,
    PersistentJobQueue,
)
from markdown_ingress.api import generate_security_report, ingest, ingest_many, retry_ingest
from markdown_ingress.api_server_handlers import (
    build_root_payload,
    handle_batch_status,
    handle_batch_submit,
    handle_extractor_comparison,
    handle_ingest,
    handle_retry_ingest,
    handle_security_report,
    handle_sync_batch,
)
from markdown_ingress.api_server_models import (
    BatchIngestRequest,
    BatchIngestResponse,
    BatchJobAccepted,
    BatchJobResponse,
    ExtractorComparisonResponse,
    HTMLCompareRequest,
    IngestRequest,
    IngestResponse,
    RetryIngestRequest,
    SecurityReportResponse,
)
from markdown_ingress.application.use_cases import CompareExtractorsUseCase
from markdown_ingress.core.orchestrator import get_ingest_stats

# Module-level logger for error handling
_logger = logging.getLogger(__name__)

API_VERSION = "0.8.0"


def _read_positive_int_env(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        _logger.warning("Invalid integer for %s=%r. Using default %d.", name, raw, default)
        return default
    if value < minimum:
        _logger.warning("Invalid value for %s=%r. Minimum is %d. Using default %d.", name, raw, minimum, default)
        return default
    return value


def _read_bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    _logger.warning("Invalid boolean for %s=%r. Using default %s.", name, raw, default)
    return default


def _read_optional_float_env(name: str, *, minimum: float = 0.0, exclusive_minimum: bool = False) -> float | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        value = float(raw)
    except ValueError:
        _logger.warning("Invalid float for %s=%r. Disabling optional setting.", name, raw)
        return None
    is_invalid = value < minimum or (exclusive_minimum and value == minimum)
    if is_invalid:
        comparator = ">" if exclusive_minimum else ">="
        _logger.warning("Invalid value for %s=%r. Expected %s %s. Disabling optional setting.", name, raw, comparator, minimum)
        return None
    return value


_RAW_API_KEY = os.getenv("MDI_API_KEY")
API_KEY_CONFIG_ERROR = _RAW_API_KEY is not None and _RAW_API_KEY.strip() == ""
OPTIONAL_API_KEY = None if API_KEY_CONFIG_ERROR else _RAW_API_KEY
JOB_TTL_SECONDS = _read_positive_int_env("MDI_API_JOB_TTL_SECONDS", 3600)
JOB_DB_PATH = os.getenv("MDI_API_JOB_DB_PATH", "artifacts/api_jobs/jobs.sqlite3")
JOB_WORKERS = _read_positive_int_env("MDI_API_JOB_WORKERS", 2)
MAX_QUEUED_JOBS = _read_positive_int_env("MDI_API_MAX_QUEUED_JOBS", 100)
JOB_WEBHOOK_MAX_RETRIES = _read_positive_int_env("MDI_API_WEBHOOK_MAX_RETRIES", 2)
_job_webhook_retry_delay = _read_optional_float_env("MDI_API_WEBHOOK_RETRY_DELAY_SECONDS", minimum=0.0)
JOB_WEBHOOK_RETRY_DELAY_SECONDS = 0.25 if _job_webhook_retry_delay is None else _job_webhook_retry_delay
JOB_EXECUTION_TIMEOUT_SECONDS = _read_optional_float_env("MDI_API_JOB_TIMEOUT_SECONDS", minimum=0.0, exclusive_minimum=True)
ALLOW_LOCAL_WEBHOOKS = _read_bool_env("MDI_API_ALLOW_LOCAL_WEBHOOKS", False)

# Rate limiting configuration
RATE_LIMIT_REQUESTS = _read_positive_int_env("MDI_API_RATE_LIMIT_REQUESTS", 100)
RATE_LIMIT_WINDOW_SECONDS = _read_positive_int_env("MDI_API_RATE_LIMIT_WINDOW", 60)
_request_counts: dict[str, list[float]] = {}
_rate_limit_lock = threading.Lock()
_rate_limit_cleanup_counter = 0  # Counter for periodic cleanup
_RATE_LIMIT_CLEANUP_THRESHOLD = 1000  # Cleanup every N requests
_RATE_LIMIT_MAX_CLIENTS = 10000  # BUG FIX: Max clients before forced cleanup (memory leak prevention)


def _check_rate_limit(client_id: str) -> tuple[bool, int]:
    """Check if client is within rate limit.

    Args:
        client_id: Client identifier (typically IP address or API key hash)

    Returns:
        Tuple of (is_allowed, retry_after_seconds)
    """
    import time
    global _rate_limit_cleanup_counter
    now = time.time()
    with _rate_limit_lock:
        if client_id not in _request_counts:
            _request_counts[client_id] = []
        requests = _request_counts[client_id]
        # Remove old requests outside the window
        _request_counts[client_id] = [t for t in requests if now - t < RATE_LIMIT_WINDOW_SECONDS]
        if len(_request_counts[client_id]) >= RATE_LIMIT_REQUESTS:
            # Calculate retry-after
            oldest = min(_request_counts[client_id])
            retry_after = int(RATE_LIMIT_WINDOW_SECONDS - (now - oldest)) + 1
            return False, retry_after
        _request_counts[client_id].append(now)

        # Periodic cleanup of empty client entries to prevent memory leak
        _rate_limit_cleanup_counter += 1
        if _rate_limit_cleanup_counter >= _RATE_LIMIT_CLEANUP_THRESHOLD:
            _rate_limit_cleanup_counter = 0
            # Remove clients with all timestamps outside the window (stale clients)
            stale_clients = [
                cid for cid, reqs in _request_counts.items()
                if all(now - t >= RATE_LIMIT_WINDOW_SECONDS for t in reqs)
            ]
            for cid in stale_clients:
                del _request_counts[cid]

        # BUG FIX: Size-based cleanup to prevent unbounded memory growth
        # When client count exceeds max, remove oldest half
        if len(_request_counts) > _RATE_LIMIT_MAX_CLIENTS:
            # Sort clients by oldest request timestamp and remove oldest half
            client_ages = [
                (cid, min(reqs) if reqs else float('inf'))
                for cid, reqs in _request_counts.items()
            ]
            client_ages.sort(key=lambda x: x[1])  # Oldest first
            # Remove oldest half of clients
            for cid, _ in client_ages[:len(client_ages) // 2]:
                del _request_counts[cid]

        return True, 0

app = FastAPI(
    title="MarkDownIngress API",
    description="Deterministic Web → Markdown Engine for LLM Pipelines",
    version=API_VERSION,
)
# BUG FIX #5: Use functions to get current state instead of caching at module level.
# This prevents stale references during module reload.
def _get_previous_watchdog_thread():
    """Get the previous watchdog thread from globals, avoiding stale references."""
    return globals().get("_PREVIOUS_JOB_QUEUE_WATCHDOG_THREAD") or globals().get("_JOB_QUEUE_WATCHDOG_THREAD")


def _get_previous_watchdog_stop():
    """Get the previous watchdog stop event from globals, avoiding stale references."""
    return globals().get("_PREVIOUS_JOB_QUEUE_WATCHDOG_STOP") or globals().get("_JOB_QUEUE_WATCHDOG_STOP")


def _get_previous_repair_thread():
    """Get the previous repair thread from globals, avoiding stale references."""
    return globals().get("_PREVIOUS_JOB_QUEUE_REPAIR_THREAD") or globals().get("_JOB_QUEUE_REPAIR_THREAD")


def _get_previous_repair_stop():
    """Get the previous repair stop event from globals, avoiding stale references."""
    return globals().get("_PREVIOUS_JOB_QUEUE_REPAIR_STOP") or globals().get("_JOB_QUEUE_REPAIR_STOP")
_JOB_QUEUE_LOCK = threading.RLock()
_JOB_QUEUE_INIT_LOCK = threading.Lock()  # Protects lazy job queue initialization
_JOB_QUEUE_BUILD_LOCK = threading.Lock()
_PREVIOUS_JOB_QUEUE_REPAIR_THREAD: threading.Thread | None = None
_PREVIOUS_JOB_QUEUE_REPAIR_STOP: threading.Event | None = None
_PREVIOUS_JOB_QUEUE_WATCHDOG_THREAD: threading.Thread | None = None
_PREVIOUS_JOB_QUEUE_WATCHDOG_STOP: threading.Event | None = None
_JOB_QUEUE_REPAIR_THREAD: threading.Thread | None = None
_JOB_QUEUE_REPAIR_STOP: threading.Event | None = None
_JOB_QUEUE_WATCHDOG_THREAD: threading.Thread | None = None
_JOB_QUEUE_WATCHDOG_STOP: threading.Event | None = None
_JOB_QUEUE_HISTORY: list[PersistentJobQueue] = []
_RECOVERABLE_QUEUE_STATES = {"closing", "lease_lost", "external_owner", "backend_error"}
_EXTERNAL_OWNER_REPAIR_RETRY_SECONDS = 5.0
_BACKEND_ERROR_REPAIR_RETRY_SECONDS = 5.0
_QUEUE_LEASE_TIMEOUT_SECONDS = 30.0
_LEGACY_QUEUE_PRUNE_ERROR_THRESHOLD = 3


class _ExternalOwnerJobQueue:
    """Read-only queue view used when another active process owns the job DB."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.state = "external_owner"

    def _raise_backend_read_error(self, exc: sqlite3.Error) -> None:
        message = str(exc).lower()
        if isinstance(exc, sqlite3.OperationalError) and ("locked" in message or "busy" in message):
            raise RuntimeError("Job queue backend is temporarily unavailable because the current owner is busy")
        self.state = "backend_error"
        raise RuntimeError(f"Job queue backend read failed: {exc}")

    def _db_uri(self) -> str:
        return f"{self.db_path.resolve().as_uri()}?mode=ro"

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_uri(), timeout=0.0, uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def submit(self, *args, **kwargs):
        if self.state == "backend_error":
            raise RuntimeError("Job queue backend read failed: external owner backend is unhealthy")
        raise RuntimeError("Job queue is unavailable because the DB is owned by another active instance")

    def pending_count(self, *, cleanup_expired: bool = True) -> int:
        row = None
        try:
            with closing(sqlite3.connect(self._db_uri(), timeout=0.0, uri=True)) as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS count FROM jobs WHERE status IN ('queued','running')"
                ).fetchone()
        except sqlite3.Error as exc:
            self._raise_backend_read_error(exc)
        return int(row[0]) if row else 0

    def get(self, job_id: str, *, cleanup_expired: bool = True) -> JobRecord | None:
        try:
            with closing(self._connect()) as conn:
                row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        except sqlite3.Error as exc:
            self._raise_backend_read_error(exc)
        if row is None:
            return None
        return JobRecord(
            job_id=row["job_id"],
            status=row["status"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            error=row["error"],
            webhook_url=row["webhook_url"],
            ttl_seconds=row["ttl_seconds"],
            legacy_expires_at=row["legacy_expires_at"],
        )

    def close(self, *args, **kwargs) -> None:
        return None


class _TransientLegacyQueueReadError(RuntimeError):
    """Signal that a legacy queue could not be inspected due to a transient read problem."""


def _build_job_queue() -> PersistentJobQueue:
    return PersistentJobQueue(
        db_path=JOB_DB_PATH,
        worker_count=JOB_WORKERS,
        ttl_seconds=JOB_TTL_SECONDS,
        max_queued_jobs=MAX_QUEUED_JOBS,
        webhook_max_retries=JOB_WEBHOOK_MAX_RETRIES,
        webhook_retry_delay_seconds=JOB_WEBHOOK_RETRY_DELAY_SECONDS,
        allow_local_webhooks=ALLOW_LOCAL_WEBHOOKS,
        job_timeout_seconds=JOB_EXECUTION_TIMEOUT_SECONDS,
    )


def _is_active_owner_error(exc: RuntimeError) -> bool:
    return str(exc) == "Job queue DB is already owned by another active instance"


def _remember_job_queue(queue: PersistentJobQueue | None) -> None:
    """Record a job queue in history for cleanup tracking.

    BUG FIX: Always acquire _JOB_QUEUE_LOCK before modifying _JOB_QUEUE_HISTORY.
    This function may be called from contexts that don't hold the lock,
    so we ensure thread-safety by acquiring it here.
    """
    if queue is None:
        return
    if getattr(queue, "state", None) == "external_owner":
        return
    with _JOB_QUEUE_LOCK:
        if any(existing is queue for existing in _JOB_QUEUE_HISTORY):
            return
        queue_db_path = getattr(queue, "db_path", None)
        if queue_db_path is not None:
            queue_db_path = str(queue_db_path)
            _JOB_QUEUE_HISTORY[:] = [
                existing
                for existing in _JOB_QUEUE_HISTORY
                if str(getattr(existing, "db_path", object())) != queue_db_path
            ]
        _JOB_QUEUE_HISTORY.append(queue)


def _queue_still_has_visible_jobs(queue) -> bool:
    connect = getattr(queue, "_connect", None)
    if not callable(connect):
        return True
    try:
        with closing(connect()) as conn:
            rows = conn.execute(
                """
                SELECT status, completed_at, ttl_seconds, legacy_expires_at
                FROM jobs
                """
            ).fetchall()
    except sqlite3.Error as exc:
        raise _TransientLegacyQueueReadError(str(exc)) from exc
    except Exception:
        raise _TransientLegacyQueueReadError("legacy queue inspection failed")
    now = datetime.now(UTC)
    for row in rows:
        status = row["status"] if isinstance(row, sqlite3.Row) else row[0]
        completed_at = row["completed_at"] if isinstance(row, sqlite3.Row) else row[1]
        ttl_seconds = row["ttl_seconds"] if isinstance(row, sqlite3.Row) else row[2]
        legacy_expires_at = row["legacy_expires_at"] if isinstance(row, sqlite3.Row) else row[3]
        if status in {"queued", "running"}:
            return True
        if not completed_at:
            continue
        if ttl_seconds is None:
            try:
                datetime.fromisoformat(completed_at)
            except ValueError:
                continue
            if not legacy_expires_at:
                continue
            try:
                expires_dt = datetime.fromisoformat(legacy_expires_at)
            except ValueError:
                continue
            if now <= expires_dt:
                return True
            continue
        try:
            completed_dt = datetime.fromisoformat(completed_at)
        except ValueError:
            continue  # skip corrupt row, don't abort entire queue
        if (now - completed_dt).total_seconds() <= int(ttl_seconds):
            return True
    return False


def _prune_job_queue_history() -> None:
    """Remove job queues from history that no longer have visible jobs.

    BUG FIX #3: All access to _JOB_QUEUE_HISTORY is protected by _JOB_QUEUE_LOCK.
    This includes both reading (iteration) and writing (slice assignment).
    """
    with _JOB_QUEUE_LOCK:
        kept = []
        for queue in _JOB_QUEUE_HISTORY:
            try:
                if _queue_still_has_visible_jobs(queue):
                    setattr(queue, "_history_read_failures", 0)
                    kept.append(queue)
            except _TransientLegacyQueueReadError:
                failures = int(getattr(queue, "_history_read_failures", 0)) + 1
                setattr(queue, "_history_read_failures", failures)
                if failures < _LEGACY_QUEUE_PRUNE_ERROR_THRESHOLD:
                    kept.append(queue)
        _JOB_QUEUE_HISTORY[:] = kept


def _replace_job_queue_if_current(expected_queue, replacement_queue) -> bool:
    global JOB_QUEUE
    with _JOB_QUEUE_LOCK:
        if JOB_QUEUE is not expected_queue:
            return False
        _remember_job_queue(JOB_QUEUE)
        JOB_QUEUE = replacement_queue
        return True


def _close_queue_for_repair(queue: PersistentJobQueue) -> None:
    try:
        queue.close(inline_wait_timeout=0.0, preserve_state_on_inline_timeout=True)
    except TypeError:
        queue.close()


def _read_job_from_queue(queue, job_id: str):
    try:
        return queue.get(job_id, cleanup_expired=False)
    except TypeError:
        return queue.get(job_id)


def _job_record_within_api_ttl(job) -> bool:
    completed_at = getattr(job, "completed_at", None)
    if completed_at is None:
        return True
    try:
        completed_dt = datetime.fromisoformat(completed_at)
    except ValueError:
        return False
    ttl_seconds = getattr(job, "ttl_seconds", None)
    if ttl_seconds is None:
        legacy_expires_at = getattr(job, "legacy_expires_at", None)
        if not legacy_expires_at:
            return False
        try:
            return datetime.now(UTC) <= datetime.fromisoformat(legacy_expires_at)
        except ValueError:
            return False
    age_seconds = (datetime.now(UTC) - completed_dt).total_seconds()
    return age_seconds <= ttl_seconds


def _promote_external_owner_queue(expected_queue):
    if getattr(expected_queue, "state", None) == "external_owner":
        return expected_queue
    replacement_queue = _ExternalOwnerJobQueue(getattr(expected_queue, "db_path", JOB_DB_PATH))
    if _replace_job_queue_if_current(expected_queue, replacement_queue):
        return replacement_queue
    with _JOB_QUEUE_LOCK:
        return JOB_QUEUE


def _build_replacement_queue_or_current(expected_queue):
    with _JOB_QUEUE_BUILD_LOCK:
        with _JOB_QUEUE_LOCK:
            if JOB_QUEUE is not expected_queue:
                return JOB_QUEUE
        try:
            replacement_queue = _build_job_queue()
        except RuntimeError as exc:
            if _is_active_owner_error(exc):
                return _promote_external_owner_queue(expected_queue)
            if getattr(expected_queue, "state", None) == "backend_error":
                return expected_queue
            raise
        # BUG FIX #2: Catch only specific exceptions, not all exceptions.
        # Previously caught all Exception including MemoryError, KeyboardInterrupt, etc.
        # Only catch database/file errors that indicate transient backend issues.
        except (sqlite3.Error, OSError):
            if getattr(expected_queue, "state", None) in {"backend_error", "external_owner"}:
                expected_queue.state = "backend_error"
                return expected_queue
            raise
        if _replace_job_queue_if_current(expected_queue, replacement_queue):
            return replacement_queue
        replacement_queue.close()
        with _JOB_QUEUE_LOCK:
            return JOB_QUEUE


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
    try:
        heartbeat_dt = datetime.fromisoformat(heartbeat_at)
    except ValueError:
        return True
    age_seconds = (datetime.now(UTC) - heartbeat_dt).total_seconds()
    return age_seconds > _QUEUE_LEASE_TIMEOUT_SECONDS


def _external_owner_backend_still_owned(queue) -> bool:
    db_path = Path(getattr(queue, "db_path", JOB_DB_PATH))
    db_uri = f"{db_path.resolve().as_uri()}?mode=ro"
    try:
        with closing(sqlite3.connect(db_uri, timeout=0.0, uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT owner_id, heartbeat_at, owner_pid FROM queue_leases WHERE lease_name = ?",
                ("default",),
            ).fetchone()
    except sqlite3.Error as exc:
        message = str(exc).lower()
        if isinstance(exc, sqlite3.OperationalError) and ("locked" in message or "busy" in message):
            return True
        queue.state = "backend_error"
        raise RuntimeError(f"Job queue backend read failed during repair: {exc}")
    if row is None:
        return False
    owner_pid = int(row["owner_pid"])
    heartbeat_at = row["heartbeat_at"]
    return _is_owner_process_alive(owner_pid) and not _is_stale_heartbeat(heartbeat_at)


def _start_job_queue_repair_loop() -> None:
    global _JOB_QUEUE_REPAIR_THREAD, _JOB_QUEUE_REPAIR_STOP

    def repair_loop() -> None:
        global JOB_QUEUE, _JOB_QUEUE_REPAIR_THREAD, _JOB_QUEUE_REPAIR_STOP
        assert stop_event is not None
        while not stop_event.wait(0.0):
            with _JOB_QUEUE_LOCK:
                queue = JOB_QUEUE
                state = getattr(queue, "state", None)
                if state not in _RECOVERABLE_QUEUE_STATES:
                    _JOB_QUEUE_REPAIR_THREAD = None
                    _JOB_QUEUE_REPAIR_STOP = None
                    return
            try:
                _close_queue_for_repair(queue)
            except RuntimeError as e:
                _logger.debug("Failed to close queue for repair: %s", e)
            else:
                if state == "external_owner":
                    try:
                        backend_still_owned = _external_owner_backend_still_owned(queue)
                    except RuntimeError:
                        state = getattr(queue, "state", None)
                    else:
                        if backend_still_owned:
                            stop_event.wait(_EXTERNAL_OWNER_REPAIR_RETRY_SECONDS)
                            continue
                if state == "backend_error":
                    stop_event.wait(_BACKEND_ERROR_REPAIR_RETRY_SECONDS)
                else:
                    if state == "external_owner":
                        replacement_queue = _build_replacement_queue_or_current(queue)
                        if replacement_queue is not queue:
                            _JOB_QUEUE_REPAIR_THREAD = None
                            _JOB_QUEUE_REPAIR_STOP = None
                            return
                        stop_event.wait(_EXTERNAL_OWNER_REPAIR_RETRY_SECONDS)
                        continue
                replacement_queue = _build_replacement_queue_or_current(queue)
                if replacement_queue is not queue:
                    _JOB_QUEUE_REPAIR_THREAD = None
                    _JOB_QUEUE_REPAIR_STOP = None
                    return
                if state == "backend_error" and replacement_queue is queue:
                    _JOB_QUEUE_REPAIR_THREAD = None
                    _JOB_QUEUE_REPAIR_STOP = None
                    return
            if state == "external_owner":
                stop_event.wait(_EXTERNAL_OWNER_REPAIR_RETRY_SECONDS)
            elif state == "backend_error":
                stop_event.wait(_BACKEND_ERROR_REPAIR_RETRY_SECONDS)
            else:
                stop_event.wait(0.25)

    # Assign stop_event BEFORE defining repair_loop so the closure captures
    # a fully initialised Event — eliminates the race where the thread starts
    # before the variable is bound.
    stop_event = threading.Event()

    with _JOB_QUEUE_LOCK:
        if _JOB_QUEUE_REPAIR_THREAD is not None and _JOB_QUEUE_REPAIR_THREAD.is_alive():
            return
        _JOB_QUEUE_REPAIR_STOP = stop_event
        _JOB_QUEUE_REPAIR_THREAD = threading.Thread(target=repair_loop, daemon=True)
        _JOB_QUEUE_REPAIR_THREAD.start()


def _maybe_start_job_queue_repair() -> None:
    with _JOB_QUEUE_LOCK:
        queue = JOB_QUEUE
        state = getattr(queue, "state", None)
        repair_thread = _JOB_QUEUE_REPAIR_THREAD
    if state in _RECOVERABLE_QUEUE_STATES and not (
        repair_thread is not None and repair_thread.is_alive()
    ):
        _start_job_queue_repair_loop()


def _job_queue_watchdog_tick() -> None:
    _maybe_start_job_queue_repair()


def _start_job_queue_watchdog() -> None:
    global _JOB_QUEUE_WATCHDOG_THREAD, _JOB_QUEUE_WATCHDOG_STOP

    def watchdog_loop() -> None:
        assert stop_event is not None
        while not stop_event.wait(0.5):
            _job_queue_watchdog_tick()

    stop_event = threading.Event()

    with _JOB_QUEUE_LOCK:
        if _JOB_QUEUE_WATCHDOG_THREAD is not None and _JOB_QUEUE_WATCHDOG_THREAD.is_alive():
            return
        _JOB_QUEUE_WATCHDOG_STOP = stop_event
        _JOB_QUEUE_WATCHDOG_THREAD = threading.Thread(target=watchdog_loop, daemon=True)
        _JOB_QUEUE_WATCHDOG_THREAD.start()


def _init_job_queue(previous_queue=None):
    global _JOB_QUEUE_WATCHDOG_STOP, _JOB_QUEUE_WATCHDOG_THREAD, _JOB_QUEUE_REPAIR_STOP, _JOB_QUEUE_REPAIR_THREAD

    def _stop_control_thread(
        name: str,
        thread: threading.Thread | None,
        stop_event: threading.Event | None,
    ) -> None:
        if stop_event is not None:
            stop_event.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
            if thread.is_alive():
                raise RuntimeError(f"{name} did not stop before reload")

    # BUG FIX #5: Use getter functions instead of cached module-level variables.
    # This prevents issues with stale references during module reload.
    _stop_control_thread("Previous job queue repair thread", _get_previous_repair_thread(), _get_previous_repair_stop())
    _stop_control_thread("Job queue repair thread", _JOB_QUEUE_REPAIR_THREAD, _JOB_QUEUE_REPAIR_STOP)
    _JOB_QUEUE_REPAIR_STOP = None
    _JOB_QUEUE_REPAIR_THREAD = None
    _stop_control_thread("Previous job queue watchdog thread", _get_previous_watchdog_thread(), _get_previous_watchdog_stop())
    _stop_control_thread("Job queue watchdog thread", _JOB_QUEUE_WATCHDOG_THREAD, _JOB_QUEUE_WATCHDOG_STOP)
    _JOB_QUEUE_WATCHDOG_STOP = None
    _JOB_QUEUE_WATCHDOG_THREAD = None
    if previous_queue is not None:
        close = getattr(previous_queue, "close", None)
        if callable(close):
            try:
                close()
            except RuntimeError:
                if getattr(previous_queue, "state", None) in _RECOVERABLE_QUEUE_STATES:
                    return previous_queue
                raise
        _remember_job_queue(previous_queue)
    try:
        return _build_job_queue()
    except RuntimeError as exc:
        if _is_active_owner_error(exc):
            if previous_queue is not None:
                return _promote_external_owner_queue(previous_queue)
            return _ExternalOwnerJobQueue(JOB_DB_PATH)
        raise


# Lazy initialization to prevent server crash on startup if job queue fails
JOB_QUEUE: PersistentJobQueue | None = None
_job_queue_initialized = False


def _ensure_job_queue_initialized():
    """Initialize job queue lazily to prevent server crash on module import.

    If initialization fails, the error is deferred until first use,
    allowing the server to start and serve endpoints that don't require the job queue.
    """
    global JOB_QUEUE, _job_queue_initialized
    if _job_queue_initialized and JOB_QUEUE is not None:
        return
    with _JOB_QUEUE_INIT_LOCK:
        # Double-check after acquiring lock to prevent race condition
        if _job_queue_initialized and JOB_QUEUE is not None:
            return
        if JOB_QUEUE is not None:
            _job_queue_initialized = True
            return
        try:
            JOB_QUEUE = _init_job_queue(globals().get("JOB_QUEUE"))
            _job_queue_initialized = True
            _maybe_start_job_queue_repair()
            _start_job_queue_watchdog()
        except (OSError, ValueError, RuntimeError, ImportError) as e:
            # BUG FIX: Catch specific exceptions instead of broad Exception
            # OSError: file/database errors
            # ValueError: configuration errors
            # RuntimeError: initialization failures
            # ImportError: missing dependencies
            _logger.error("Failed to initialize job queue: %s", e)
            # JOB_QUEUE remains None; endpoints will handle unavailable queue


# Legacy initialization for backwards compatibility - now happens lazily
# JOB_QUEUE = _init_job_queue(globals().get("JOB_QUEUE"))
# _maybe_start_job_queue_repair()
# _start_job_queue_watchdog()


def _get_job_queue():
    global JOB_QUEUE
    # Ensure job queue is initialized before use (lazy initialization)
    _ensure_job_queue_initialized()
    _maybe_start_job_queue_repair()
    with _JOB_QUEUE_LOCK:
        queue = JOB_QUEUE
        if queue is None:
            raise RuntimeError("Job queue is unavailable")
        if getattr(queue, "state", None) not in _RECOVERABLE_QUEUE_STATES:
            return queue
        if getattr(queue, "state", None) == "external_owner":
            return queue
        # BUG FIX #1: Capture the queue reference before releasing lock.
        # This prevents TOCTOU race where JOB_QUEUE changes between
        # releasing the lock and attempting repair/rebuild.
        queue_to_repair = queue
    try:
        _close_queue_for_repair(queue_to_repair)
    except RuntimeError:
        # Re-check state under lock after repair failure
        with _JOB_QUEUE_LOCK:
            if getattr(queue_to_repair, "state", None) in _RECOVERABLE_QUEUE_STATES:
                # JOB_QUEUE may have changed, return current value
                return JOB_QUEUE
        raise
    # Pass the queue we attempted to repair, rebuild will handle TOCTOU internally
    return _build_replacement_queue_or_current(queue_to_repair)


def _get_job_record(job_id: str):
    queue = _get_job_queue()
    unavailable_error: RuntimeError | None = None
    try:
        job = _read_job_from_queue(queue, job_id)
    except RuntimeError as exc:
        unavailable_error = exc
    else:
        if job is not None and _job_record_within_api_ttl(job):
            return job
    with _JOB_QUEUE_LOCK:
        _prune_job_queue_history()
        history = list(_JOB_QUEUE_HISTORY)
    for legacy_queue in reversed(history):
        try:
            job = _read_job_from_queue(legacy_queue, job_id)
        except RuntimeError:
            continue
        if job is not None and _job_record_within_api_ttl(job):
            return job
    if unavailable_error is not None:
        raise unavailable_error
    return None


def _snapshot_job_subsystem(*, start_repair: bool = True) -> dict[str, object]:
    if start_repair:
        _maybe_start_job_queue_repair()

    def read_pending(queue_obj) -> int | None:
        if queue_obj is None:
            return None
        try:
            return queue_obj.pending_count(cleanup_expired=False)
        except RuntimeError:
            return None
        except TypeError:
            return queue_obj.pending_count()

    def count_unknown_ttl_jobs(queue_obj) -> int:
        connect = getattr(queue_obj, "_connect", None)
        if not callable(connect):
            return 0
        try:
            with closing(connect()) as conn:
                rows = conn.execute(
                    """
                    SELECT completed_at, legacy_expires_at
                    FROM jobs
                    WHERE completed_at IS NOT NULL AND ttl_seconds IS NULL
                    """
                ).fetchall()
        except Exception as exc:
            _logger.debug("Error counting unknown TTL jobs: %s", exc)
            return 0
        count = 0
        now = datetime.now(UTC)
        for row in rows:
            completed_at = row["completed_at"] if isinstance(row, sqlite3.Row) else row[0]
            legacy_expires_at = row["legacy_expires_at"] if isinstance(row, sqlite3.Row) else row[1]
            try:
                datetime.fromisoformat(completed_at)
            except (TypeError, ValueError):
                continue
            if not legacy_expires_at:
                continue
            try:
                expires_dt = datetime.fromisoformat(legacy_expires_at)
            except ValueError:
                continue
            if now <= expires_dt:
                count += 1
        return count

    with _JOB_QUEUE_LOCK:
        _prune_job_queue_history()
        current_queue = JOB_QUEUE
        history = list(_JOB_QUEUE_HISTORY)
        repair_thread = _JOB_QUEUE_REPAIR_THREAD
        current_ttl_seconds = getattr(current_queue, "ttl_seconds", None)
        current_max_queued_jobs = getattr(current_queue, "max_queued_jobs", None)

    current_state = getattr(current_queue, "state", "uninitialized")
    current_pending = read_pending(current_queue)
    legacy_pending = 0
    legacy_unknown_ttl_jobs = 0
    legacy_db_paths: list[str] = []
    seen_db_paths = {str(getattr(current_queue, "db_path", JOB_DB_PATH))}
    pending_unknown = current_pending is None
    current_unknown_ttl_jobs = count_unknown_ttl_jobs(current_queue)
    for legacy_queue in history:
        legacy_db_path = str(legacy_queue.db_path)
        if legacy_db_path in seen_db_paths:
            continue
        seen_db_paths.add(legacy_db_path)
        legacy_value = read_pending(legacy_queue)
        if legacy_value is None:
            pending_unknown = True
            continue
        legacy_pending += legacy_value
        legacy_unknown_ttl_jobs += count_unknown_ttl_jobs(legacy_queue)
        legacy_db_paths.append(legacy_db_path)

    return {
        "status": "healthy" if current_state == "open" and not pending_unknown else "degraded",
        "current_state": current_state,
        "current_db_path": str(getattr(current_queue, "db_path", JOB_DB_PATH)),
        "current_ttl_seconds": current_ttl_seconds,
        "current_max_queued_jobs": current_max_queued_jobs,
        "current_pending": current_pending,
        "legacy_pending": legacy_pending,
        "pending_visible_total": None if pending_unknown else current_pending + legacy_pending,
        "legacy_visible_queues": len(legacy_db_paths),
        "legacy_db_paths": legacy_db_paths,
        "pending_unknown": pending_unknown,
        "current_unknown_ttl_jobs": current_unknown_ttl_jobs,
        "legacy_unknown_ttl_jobs": legacy_unknown_ttl_jobs,
        "legacy_unknown_ttl_seconds": LEGACY_UNKNOWN_TTL_SECONDS,
        "repair_in_progress": bool(repair_thread is not None and repair_thread.is_alive()),
    }


def _require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Enforce API key auth when configured.

    Uses secrets.compare_digest for constant-time comparison to prevent timing attacks.
    """
    import secrets
    if API_KEY_CONFIG_ERROR:
        raise HTTPException(status_code=500, detail="Server API key configuration is invalid")
    if OPTIONAL_API_KEY is None:
        return
    if x_api_key is None or not secrets.compare_digest(x_api_key, OPTIONAL_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def _rate_limit_client_id(request: Request, x_api_key: str | None) -> str:
    import hashlib
    if OPTIONAL_API_KEY is not None and x_api_key is not None:
        return hashlib.sha256(x_api_key.encode()).hexdigest()[:16]
    if request.client is not None and request.client.host:
        return f"ip:{request.client.host}"
    return "anonymous:unknown"


def _require_rate_limit(request: Request, x_api_key: str | None = Header(default=None)) -> None:
    """Enforce rate limiting for batch endpoints.

    Uses API key (if available) or falls back to the client IP for anonymous clients.
    Raises HTTP 429 if rate limit is exceeded.
    """
    client_id = _rate_limit_client_id(request, x_api_key)
    allowed, retry_after = _check_rate_limit(client_id)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Retry after {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)},
        )

def compare_extractors(html: str, model: str = "gpt-4") -> dict[str, dict[str, Any]]:
    """Compatibility wrapper exposing extractor comparison at module scope."""
    return CompareExtractorsUseCase().execute(html, model=model)


@app.post("/api/v1/ingest", response_model=IngestResponse, dependencies=[Depends(_require_api_key), Depends(_require_rate_limit)])
async def ingest_endpoint(request: IngestRequest):
    """Ingest a single URL and return structured markdown plus optional blocks/chunks."""
    return await handle_ingest(request, ingest)


@app.post("/api/v1/ingest/retry", response_model=IngestResponse, dependencies=[Depends(_require_api_key), Depends(_require_rate_limit)])
async def retry_ingest_endpoint(request: RetryIngestRequest):
    """Ingest with automatic retry logic and timeout escalation."""
    return await handle_retry_ingest(request, retry_ingest)


@app.post("/api/v1/ingest/batch", response_model=BatchIngestResponse, dependencies=[Depends(_require_api_key), Depends(_require_rate_limit)])
async def batch_ingest_endpoint(request: BatchIngestRequest):
    """Process a batch synchronously for simple clients."""
    return await handle_sync_batch(request, ingest_many)


@app.post("/api/v1/jobs/batch", response_model=BatchJobAccepted, dependencies=[Depends(_require_api_key), Depends(_require_rate_limit)])
async def batch_job_submit(request: BatchIngestRequest):
    """Queue a batch ingestion job and return a polling handle."""
    job_queue = _get_job_queue()
    return await handle_batch_submit(
        request,
        ingest_many,
        job_queue,
        getattr(job_queue, "ttl_seconds", JOB_TTL_SECONDS),
    )


@app.get("/api/v1/jobs/{job_id}", response_model=BatchJobResponse, dependencies=[Depends(_require_api_key), Depends(_require_rate_limit)])
async def batch_job_status(job_id: str):
    """Poll an async batch job."""
    return await handle_batch_status(job_id, _get_job_record)


@app.post("/api/v1/security/report", response_model=SecurityReportResponse, dependencies=[Depends(_require_api_key), Depends(_require_rate_limit)])
async def security_report_endpoint(request: IngestRequest):
    """Generate a detailed security report."""
    return await handle_security_report(request, generate_security_report)


@app.post(
    "/api/v1/evaluate/extractors",
    response_model=ExtractorComparisonResponse,
    dependencies=[Depends(_require_api_key)],
)
async def extractor_comparison_endpoint(request: HTMLCompareRequest):
    """Compare supported extractors against a raw HTML payload."""
    return await handle_extractor_comparison(request, compare_extractors)


@app.get("/api/v1/stats", dependencies=[Depends(_require_api_key)])
async def stats_endpoint():
    """Expose process-level observability stats."""
    snapshot = _snapshot_job_subsystem(start_repair=False)
    payload = {
        "version": API_VERSION,
        "stats": get_ingest_stats(),
        "job_queue": {
            "pending": snapshot["pending_visible_total"],
            "ttl_seconds": snapshot["current_ttl_seconds"],
            "ttl_applies_to": "completed_jobs_with_persisted_ttl_or_legacy_compatibility_ttl",
            "max_queued_jobs": snapshot["current_max_queued_jobs"],
        },
    }
    jobs_payload = payload["job_queue"]
    jobs_payload["current_state"] = snapshot["current_state"]
    jobs_payload["current_db_path"] = snapshot["current_db_path"]
    jobs_payload["current_pending"] = snapshot["current_pending"]
    jobs_payload["legacy_pending"] = snapshot["legacy_pending"]
    jobs_payload["pending_visible_total"] = snapshot["pending_visible_total"]
    jobs_payload["legacy_visible_queues"] = snapshot["legacy_visible_queues"]
    jobs_payload["legacy_db_paths"] = snapshot["legacy_db_paths"]
    jobs_payload["repair_in_progress"] = snapshot["repair_in_progress"]
    jobs_payload["current_unknown_ttl_jobs"] = snapshot["current_unknown_ttl_jobs"]
    jobs_payload["legacy_unknown_ttl_jobs"] = snapshot["legacy_unknown_ttl_jobs"]
    jobs_payload["legacy_unknown_ttl_seconds"] = snapshot["legacy_unknown_ttl_seconds"]
    jobs_payload["unknown_ttl_jobs_total"] = (
        snapshot["current_unknown_ttl_jobs"] + snapshot["legacy_unknown_ttl_jobs"]
    )
    jobs_payload["pending"] = snapshot["pending_visible_total"]
    return payload


@app.get("/api/v1/health")
async def health():
    """Health check endpoint."""
    snapshot = _snapshot_job_subsystem(start_repair=False)
    return {
        "status": snapshot["status"],
        "version": API_VERSION,
        "service": "MarkDownIngress API",
        "job_queue": {
            "state": snapshot["current_state"],
            "current_db_path": snapshot["current_db_path"],
            "current_pending": snapshot["current_pending"],
            "legacy_pending": snapshot["legacy_pending"],
            "pending_visible_total": snapshot["pending_visible_total"],
            "legacy_visible_queues": snapshot["legacy_visible_queues"],
            "current_unknown_ttl_jobs": snapshot["current_unknown_ttl_jobs"],
            "legacy_unknown_ttl_jobs": snapshot["legacy_unknown_ttl_jobs"],
            "legacy_unknown_ttl_seconds": snapshot["legacy_unknown_ttl_seconds"],
            "repair_in_progress": snapshot["repair_in_progress"],
        },
    }


@app.get("/")
async def root():
    """Root endpoint."""
    return build_root_payload(API_VERSION)


# Compatibility aliases for existing clients/tests.
_LEGACY_API_DEPENDENCIES = [Depends(_require_api_key), Depends(_require_rate_limit)]

app.add_api_route(
    "/ingest",
    ingest_endpoint,
    methods=["POST"],
    response_model=IngestResponse,
    dependencies=_LEGACY_API_DEPENDENCIES,
)
app.add_api_route(
    "/ingest/retry",
    retry_ingest_endpoint,
    methods=["POST"],
    response_model=IngestResponse,
    dependencies=_LEGACY_API_DEPENDENCIES,
)
app.add_api_route(
    "/ingest/batch",
    batch_ingest_endpoint,
    methods=["POST"],
    response_model=BatchIngestResponse,
    dependencies=_LEGACY_API_DEPENDENCIES,
)
app.add_api_route(
    "/security/report",
    security_report_endpoint,
    methods=["POST"],
    response_model=SecurityReportResponse,
    dependencies=_LEGACY_API_DEPENDENCIES,
)
app.add_api_route(
    "/evaluate/extractors",
    extractor_comparison_endpoint,
    methods=["POST"],
    response_model=ExtractorComparisonResponse,
    dependencies=_LEGACY_API_DEPENDENCIES,
)
app.add_api_route("/health", health, methods=["GET"])


def main():
    """Run the server."""
    uvicorn.run("markdown_ingress.api_server:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
