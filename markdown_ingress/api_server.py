"""
FastAPI server for MarkDownIngress.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from collections import deque
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import uvicorn
from fastapi import Depends, FastAPI, HTTPException

from markdown_ingress.adapters.jobs.sqlite_job_queue import (
    LEGACY_UNKNOWN_TTL_SECONDS,
    PersistentJobQueue,
    SQLiteError,
    check_external_owner_still_owns,
)
from markdown_ingress.api import generate_security_report, ingest, ingest_many, retry_ingest
from markdown_ingress.api_server_auth import (
    RATE_LIMIT_REQUESTS,
    RATE_LIMIT_WINDOW_SECONDS,
    _check_rate_limit_redis,
)
from markdown_ingress.api_server_dependencies import (
    _rate_limit_client_id,  # noqa: F401 — re-exported for test access via api_server.*
    _require_api_key,
    _require_rate_limit,
)
from markdown_ingress.api_server_env import (
    _detect_multiworker_environment,
    _read_bool_env,
    _read_optional_float_env,
    _read_positive_int_env,
)
from markdown_ingress.api_server_handlers import (
    _is_queue_unavailable_error,
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
from markdown_ingress.api_server_queue import (
    _LEGACY_QUEUE_PRUNE_ERROR_THRESHOLD,
    _close_queue_for_repair,
    _ExternalOwnerJobQueue,
    _is_active_owner_error,
    _is_stale_heartbeat,
    _job_record_within_api_ttl,
    _legacy_unknown_ttl_expires_at,
    _queue_still_has_visible_jobs,
    _read_job_from_queue,
    _TransientLegacyQueueReadError,
)
from markdown_ingress.api_server_support import validate_batch_request_ssrf_async
from markdown_ingress.application.use_cases import CompareExtractorsUseCase
from markdown_ingress.core.orchestrator import get_ingest_stats

# Module-level logger for error handling
_logger = logging.getLogger(__name__)

API_VERSION = "0.8.0"

# ---------------------------------------------------------------------------
# API key configuration (kept here so monkeypatch via api_server.* works)
# ---------------------------------------------------------------------------
_RAW_API_KEY = os.getenv("MDI_API_KEY")
API_KEY_CONFIG_ERROR: bool = _RAW_API_KEY is not None and _RAW_API_KEY.strip() == ""
OPTIONAL_API_KEY: str | None = None if API_KEY_CONFIG_ERROR else _RAW_API_KEY

# ---------------------------------------------------------------------------
# Rate limiting state (kept here so monkeypatch via api_server.* works)
# ---------------------------------------------------------------------------
_request_counts: dict[str, deque[float]] = {}
_rate_limit_lock = threading.Lock()
_rate_limit_cleanup_counter: int = 0  # Counter for periodic cleanup
_RATE_LIMIT_CLEANUP_THRESHOLD: int = 1000  # Cleanup every N requests
_RATE_LIMIT_MAX_CLIENTS: int = 10000  # Max clients before forced cleanup

_RATE_LIMIT_BACKEND: str = os.getenv("MDI_RATE_LIMIT_BACKEND", "memory").strip().lower()

# BUG FIX: Warn about per-worker rate limiting in multi-worker deployments
if _detect_multiworker_environment():
    _logger.warning(
        "Rate limiting is per-worker in multi-worker deployments. "
        "Each worker process maintains separate rate limit state. "
        "Consider using Redis-backed rate limiting for production deployments."
    )


def _check_rate_limit(client_id: str) -> tuple[bool, int]:
    """Check if client is within rate limit.

    Args:
        client_id: Client identifier (typically IP address or API key hash)

    Returns:
        Tuple of (is_allowed, retry_after_seconds)
    """
    if _RATE_LIMIT_BACKEND == "redis":
        return _check_rate_limit_redis(client_id)

    global _rate_limit_cleanup_counter
    now = time.time()
    with _rate_limit_lock:
        # Periodic cleanup of stale client entries to prevent memory leak
        _rate_limit_cleanup_counter += 1
        if _rate_limit_cleanup_counter >= _RATE_LIMIT_CLEANUP_THRESHOLD:
            _rate_limit_cleanup_counter = 0
            stale_clients = [
                cid
                for cid, reqs in _request_counts.items()
                if all(now - t >= RATE_LIMIT_WINDOW_SECONDS for t in reqs)
            ]
            for cid in stale_clients:
                del _request_counts[cid]

        if client_id not in _request_counts:
            # No maxlen: rely solely on explicit window-expiry cleanup to avoid
            # auto-eviction silently dropping timestamps still within the window.
            _request_counts[client_id] = deque()
        requests = _request_counts[client_id]
        while requests and now - requests[0] >= RATE_LIMIT_WINDOW_SECONDS:
            requests.popleft()
        # Hard cap to prevent adversarial unbounded growth (well above normal rate limit)
        rate_limit_hard_cap = RATE_LIMIT_REQUESTS * 10
        while len(requests) > rate_limit_hard_cap:
            requests.popleft()

        if len(requests) >= RATE_LIMIT_REQUESTS:
            # Calculate retry-after
            oldest = requests[0]
            retry_after = max(1, int(RATE_LIMIT_WINDOW_SECONDS - (now - oldest)) + 1)
            return False, retry_after
        requests.append(now)

        # Size-based cleanup after adding the new client to enforce max limit
        if len(_request_counts) > _RATE_LIMIT_MAX_CLIENTS:
            client_ages = [
                (cid, max(reqs) if reqs else float("-inf")) for cid, reqs in _request_counts.items()
            ]
            client_ages.sort(key=lambda x: x[1])  # Least recently active first
            for cid, _ in client_ages[: len(client_ages) - _RATE_LIMIT_MAX_CLIENTS]:
                del _request_counts[cid]

        return True, 0


JOB_TTL_SECONDS = _read_positive_int_env("MDI_API_JOB_TTL_SECONDS", 3600)
JOB_DB_PATH = os.getenv("MDI_API_JOB_DB_PATH", "artifacts/api_jobs/jobs.sqlite3")
JOB_WORKERS = _read_positive_int_env("MDI_API_JOB_WORKERS", 2)
MAX_QUEUED_JOBS = _read_positive_int_env("MDI_API_MAX_QUEUED_JOBS", 100)
JOB_WEBHOOK_MAX_RETRIES = _read_positive_int_env("MDI_API_WEBHOOK_MAX_RETRIES", 2)
_job_webhook_retry_delay = _read_optional_float_env(
    "MDI_API_WEBHOOK_RETRY_DELAY_SECONDS", minimum=0.0
)
JOB_WEBHOOK_RETRY_DELAY_SECONDS = (
    0.25 if _job_webhook_retry_delay is None else _job_webhook_retry_delay
)
JOB_EXECUTION_TIMEOUT_SECONDS = _read_optional_float_env(
    "MDI_API_JOB_TIMEOUT_SECONDS", minimum=0.0, exclusive_minimum=True
)
ALLOW_LOCAL_WEBHOOKS = _read_bool_env("MDI_API_ALLOW_LOCAL_WEBHOOKS", False)

app = FastAPI(
    title="MarkDownIngress API",
    description="Deterministic Web → Markdown Engine for LLM Pipelines",
    version=API_VERSION,
)


# BUG FIX #5: Use functions to get current state instead of caching at module level.
# This prevents stale references during module reload.
def _get_previous_watchdog_thread():
    """Get the previous watchdog thread from globals, avoiding stale references."""
    prev = globals().get("_PREVIOUS_JOB_QUEUE_WATCHDOG_THREAD")
    return prev if prev is not None else globals().get("_JOB_QUEUE_WATCHDOG_THREAD")


def _get_previous_watchdog_stop():
    """Get the previous watchdog stop event from globals, avoiding stale references."""
    prev = globals().get("_PREVIOUS_JOB_QUEUE_WATCHDOG_STOP")
    return prev if prev is not None else globals().get("_JOB_QUEUE_WATCHDOG_STOP")


def _get_previous_repair_thread():
    """Get the previous repair thread from globals, avoiding stale references."""
    prev = globals().get("_PREVIOUS_JOB_QUEUE_REPAIR_THREAD")
    return prev if prev is not None else globals().get("_JOB_QUEUE_REPAIR_THREAD")


def _get_previous_repair_stop():
    """Get the previous repair stop event from globals, avoiding stale references."""
    prev = globals().get("_PREVIOUS_JOB_QUEUE_REPAIR_STOP")
    return prev if prev is not None else globals().get("_JOB_QUEUE_REPAIR_STOP")


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


def _remember_job_queue(queue: PersistentJobQueue | None) -> None:
    """Record a job queue in history for cleanup tracking.

    BUG FIX: Always acquire _JOB_QUEUE_LOCK before modifying _JOB_QUEUE_HISTORY.
    This function may be called from contexts that don't hold the lock,
    so we ensure thread-safety by acquiring it here.
    """
    if queue is None:
        return
    with _JOB_QUEUE_LOCK:
        if getattr(queue, "state", None) == "external_owner":
            return
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
        except (SQLiteError, OSError):
            with _JOB_QUEUE_LOCK:
                state = getattr(expected_queue, "state", None)
                if state == "backend_error":
                    return expected_queue
                if state == "external_owner":
                    return expected_queue
            raise
        if _replace_job_queue_if_current(expected_queue, replacement_queue):
            return replacement_queue
        try:
            replacement_queue.close()
        except Exception as exc:
            _logger.debug("Failed to close superseded replacement queue: %s", exc, exc_info=True)
        with _JOB_QUEUE_LOCK:
            return JOB_QUEUE


def _external_owner_backend_still_owned(queue) -> bool:
    db_path = Path(getattr(queue, "db_path", JOB_DB_PATH))

    def _set_backend_error(_state: str) -> None:
        queue.state = _state

    return check_external_owner_still_owns(db_path, _is_stale_heartbeat, _set_backend_error)


def _start_job_queue_repair_loop() -> None:
    global _JOB_QUEUE_REPAIR_THREAD, _JOB_QUEUE_REPAIR_STOP

    def repair_loop() -> None:
        global JOB_QUEUE, _JOB_QUEUE_REPAIR_THREAD, _JOB_QUEUE_REPAIR_STOP
        if stop_event is None:
            return
        while not stop_event.wait(0.0):
            with _JOB_QUEUE_LOCK:
                queue = JOB_QUEUE
                if queue is None:
                    _JOB_QUEUE_REPAIR_THREAD = None
                    _JOB_QUEUE_REPAIR_STOP = None
                    return
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
                # Queue closed successfully — attempt recovery based on state.
                if state == "external_owner":
                    try:
                        backend_still_owned = _external_owner_backend_still_owned(queue)
                    except RuntimeError:
                        state = getattr(queue, "state", None)
                    else:
                        if backend_still_owned:
                            stop_event.wait(_EXTERNAL_OWNER_REPAIR_RETRY_SECONDS)
                            continue
                # Try building a replacement queue.
                replacement_queue = _build_replacement_queue_or_current(queue)
                if replacement_queue is not queue:
                    with _JOB_QUEUE_LOCK:
                        _JOB_QUEUE_REPAIR_THREAD = None
                        _JOB_QUEUE_REPAIR_STOP = None
                    return
                # Backend error with no replacement possible — give up.
                if state == "backend_error":
                    with _JOB_QUEUE_LOCK:
                        _JOB_QUEUE_REPAIR_THREAD = None
                        _JOB_QUEUE_REPAIR_STOP = None
                    return
            # Wait before retrying based on the current state.
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
        if stop_event is None:
            return
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
    _stop_control_thread(
        "Previous job queue repair thread",
        _get_previous_repair_thread(),
        _get_previous_repair_stop(),
    )
    _stop_control_thread(
        "Job queue repair thread", _JOB_QUEUE_REPAIR_THREAD, _JOB_QUEUE_REPAIR_STOP
    )
    _JOB_QUEUE_REPAIR_STOP = None
    _JOB_QUEUE_REPAIR_THREAD = None
    _stop_control_thread(
        "Previous job queue watchdog thread",
        _get_previous_watchdog_thread(),
        _get_previous_watchdog_stop(),
    )
    _stop_control_thread(
        "Job queue watchdog thread", _JOB_QUEUE_WATCHDOG_THREAD, _JOB_QUEUE_WATCHDOG_STOP
    )
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
_job_queue_init_failed_at: float | None = None
_JOB_QUEUE_RETRY_BACKOFF_SECONDS = 10.0


def _ensure_job_queue_initialized():
    """Initialize job queue lazily to prevent server crash on module import.

    If initialization fails, the error is deferred until first use,
    allowing the server to start and serve endpoints that don't require the job queue.
    """
    global JOB_QUEUE, _job_queue_initialized, _job_queue_init_failed_at
    if _job_queue_initialized:
        return
    if _job_queue_init_failed_at is not None:
        if time.monotonic() - _job_queue_init_failed_at < _JOB_QUEUE_RETRY_BACKOFF_SECONDS:
            return
    with _JOB_QUEUE_INIT_LOCK:
        # Double-check after acquiring lock to prevent race condition
        if _job_queue_initialized:
            return
        if _job_queue_init_failed_at is not None:
            if time.monotonic() - _job_queue_init_failed_at < _JOB_QUEUE_RETRY_BACKOFF_SECONDS:
                return
        if JOB_QUEUE is not None:
            _job_queue_initialized = True
            _job_queue_init_failed_at = None
            _maybe_start_job_queue_repair()
            _start_job_queue_watchdog()
            return
        try:
            JOB_QUEUE = _init_job_queue(globals().get("JOB_QUEUE"))
            _job_queue_initialized = True
            _job_queue_init_failed_at = None
            _maybe_start_job_queue_repair()
            _start_job_queue_watchdog()
        except (OSError, ValueError, RuntimeError, ImportError, sqlite3.Error) as e:
            # Catch specific exceptions. Do NOT set _job_queue_initialized = True
            # on failure so that subsequent calls can retry after backoff.
            _job_queue_init_failed_at = time.monotonic()
            _logger.error("Failed to initialize job queue: %s", e)
            # JOB_QUEUE remains None; endpoints will handle unavailable queue


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
    except (RuntimeError, TypeError):
        # Re-check state under lock after repair failure
        with _JOB_QUEUE_LOCK:
            if getattr(queue_to_repair, "state", None) in _RECOVERABLE_QUEUE_STATES:
                current = JOB_QUEUE
                if current is None:
                    raise RuntimeError("Job queue is unavailable")
                return current
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
            return cast(int, queue_obj.pending_count(cleanup_expired=False))
        except (RuntimeError, SQLiteError):
            return None
        except TypeError:
            try:
                return cast(int, queue_obj.pending_count())
            except (RuntimeError, SQLiteError):
                return None

    def count_unknown_ttl_jobs(queue_obj) -> int:
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
            _logger.warning("Error counting unknown TTL jobs: %s", exc, exc_info=True)
            return 0
        count = 0
        now = datetime.now(UTC)
        for row in rows:
            completed_at = row["completed_at"] if hasattr(row, "keys") else row[0]
            legacy_expires_at = row["legacy_expires_at"] if hasattr(row, "keys") else row[1]
            expires_dt = _legacy_unknown_ttl_expires_at(completed_at, legacy_expires_at)
            if expires_dt is None:
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
    legacy_visible_queues = 0
    legacy_db_paths: list[str] = []
    seen_db_paths = set()
    if current_queue is not None:
        seen_db_paths.add(str(getattr(current_queue, "db_path", JOB_DB_PATH)))
    pending_unknown = current_pending is None
    current_unknown_ttl_jobs = count_unknown_ttl_jobs(current_queue)
    for legacy_queue in history:
        raw_legacy_db_path = getattr(legacy_queue, "db_path", None)
        legacy_db_path = str(raw_legacy_db_path) if raw_legacy_db_path is not None else None
        if legacy_db_path is not None:
            if legacy_db_path in seen_db_paths:
                continue
            seen_db_paths.add(legacy_db_path)
        legacy_value = read_pending(legacy_queue)
        if legacy_value is None:
            pending_unknown = True
            continue
        legacy_pending += legacy_value
        legacy_unknown_ttl_jobs += count_unknown_ttl_jobs(legacy_queue)
        legacy_visible_queues += 1
        if legacy_db_path is not None:
            legacy_db_paths.append(legacy_db_path)

    return {
        "status": "healthy" if current_state == "open" and not pending_unknown else "degraded",
        "current_state": current_state,
        "current_db_path": str(getattr(current_queue, "db_path", JOB_DB_PATH)),
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
        "legacy_unknown_ttl_seconds": LEGACY_UNKNOWN_TTL_SECONDS,
        "repair_in_progress": bool(repair_thread is not None and repair_thread.is_alive()),
    }


def compare_extractors(html: str, model: str = "gpt-4") -> dict[str, dict[str, Any]]:
    """Compatibility wrapper exposing extractor comparison at module scope."""
    return CompareExtractorsUseCase().execute(html, model=model)


@app.post(
    "/api/v1/ingest",
    response_model=IngestResponse,
    dependencies=[Depends(_require_api_key), Depends(_require_rate_limit)],
)
async def ingest_endpoint(request: IngestRequest):
    """Ingest a single URL and return structured markdown plus optional blocks/chunks."""
    return await handle_ingest(request, ingest)


@app.post(
    "/api/v1/ingest/retry",
    response_model=IngestResponse,
    dependencies=[Depends(_require_api_key), Depends(_require_rate_limit)],
)
async def retry_ingest_endpoint(request: RetryIngestRequest):
    """Ingest with automatic retry logic and timeout escalation."""
    return await handle_retry_ingest(request, retry_ingest)


@app.post(
    "/api/v1/ingest/batch",
    response_model=BatchIngestResponse,
    dependencies=[Depends(_require_api_key), Depends(_require_rate_limit)],
)
async def batch_ingest_endpoint(request: BatchIngestRequest):
    """Process a batch synchronously for simple clients."""
    await validate_batch_request_ssrf_async(request)
    return await handle_sync_batch(request, ingest_many)


@app.post(
    "/api/v1/jobs/batch",
    response_model=BatchJobAccepted,
    dependencies=[Depends(_require_api_key), Depends(_require_rate_limit)],
)
async def batch_job_submit(request: BatchIngestRequest):
    """Queue a batch ingestion job and return a polling handle."""
    await validate_batch_request_ssrf_async(request)
    try:
        job_queue = _get_job_queue()
    except (RuntimeError, OSError, ValueError) as exc:
        if _is_queue_unavailable_error(exc):
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        raise
    return await handle_batch_submit(
        request,
        ingest_many,
        job_queue,
        getattr(job_queue, "ttl_seconds", JOB_TTL_SECONDS),
    )


@app.get(
    "/api/v1/jobs/{job_id}",
    response_model=BatchJobResponse,
    dependencies=[Depends(_require_api_key), Depends(_require_rate_limit)],
)
async def batch_job_status(job_id: str):
    """Poll an async batch job."""
    return await handle_batch_status(job_id, _get_job_record)


@app.post(
    "/api/v1/security/report",
    response_model=SecurityReportResponse,
    dependencies=[Depends(_require_api_key), Depends(_require_rate_limit)],
)
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
    # Only expose the basename — full filesystem paths are sensitive.
    jobs_payload["current_db_name"] = Path(snapshot["current_db_path"]).name
    jobs_payload["current_pending"] = snapshot["current_pending"]
    jobs_payload["legacy_pending"] = snapshot["legacy_pending"]
    jobs_payload["pending_visible_total"] = snapshot["pending_visible_total"]
    jobs_payload["legacy_visible_queues"] = snapshot["legacy_visible_queues"]
    jobs_payload["legacy_db_count"] = len(snapshot["legacy_db_paths"])
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
    """Public health check endpoint — does NOT expose internal paths/state."""
    snapshot = _snapshot_job_subsystem(start_repair=False)
    return {
        "status": snapshot["status"],
        "version": API_VERSION,
        "service": "MarkDownIngress API",
    }


@app.get("/api/v1/health/detailed", dependencies=[Depends(_require_api_key)])
async def health_detailed():
    """Authenticated health check with full job-queue observability."""
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
    """Public root endpoint — minimal metadata only."""
    return {"name": "markdown-ingress", "version": API_VERSION, "docs": "/docs"}


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
    host = os.getenv("MDI_HOST") or "127.0.0.1"
    uvicorn.run("markdown_ingress.api_server:app", host=host, port=8000, reload=False)


if __name__ == "__main__":
    main()
