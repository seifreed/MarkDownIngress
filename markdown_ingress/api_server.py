"""
FastAPI server for MarkDownIngress.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI

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
    _rate_limit_client_id as _rate_limit_client_id,
)
from markdown_ingress.api_server_dependencies import (
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
    handle_batch_status,
    handle_batch_submit,
    handle_extractor_comparison,
    handle_ingest,
    handle_retry_ingest,
    handle_security_report,
    handle_sync_batch,
)
from markdown_ingress.api_server_job_history import (
    prune_job_queue_history,
    remember_job_queue,
)
from markdown_ingress.api_server_legacy_routes import register_legacy_routes
from markdown_ingress.api_server_queue import (
    _LEGACY_QUEUE_PRUNE_ERROR_THRESHOLD,
    _close_queue_for_repair,
    _ExternalOwnerJobQueue,
    _is_active_owner_error,
    _is_stale_heartbeat,
    _job_record_within_api_ttl,
    _queue_still_has_visible_jobs,
    _read_job_from_queue,
    _TransientLegacyQueueReadError,
)
from markdown_ingress.api_server_rate_limit import RequestWindow, check_memory_rate_limit
from markdown_ingress.api_server_responses import (
    build_detailed_health_payload,
    build_health_payload,
    build_root_payload,
    build_stats_payload,
)
from markdown_ingress.api_server_routes import ApiRouteProviders, register_api_routes
from markdown_ingress.api_server_snapshot import (
    JobSubsystemSnapshot as _JobSubsystemSnapshot,
)
from markdown_ingress.api_server_snapshot import (
    build_job_subsystem_snapshot,
)
from markdown_ingress.api_server_support import validate_batch_request_ssrf_async
from markdown_ingress.api_server_threads import (
    previous_or_current_reference,
    stop_control_thread,
)
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
_request_counts: dict[str, RequestWindow] = {}


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
    allowed, retry_after, _rate_limit_cleanup_counter = check_memory_rate_limit(
        client_id,
        request_counts=_request_counts,
        lock=_rate_limit_lock,
        cleanup_counter=_rate_limit_cleanup_counter,
        cleanup_threshold=_RATE_LIMIT_CLEANUP_THRESHOLD,
        max_clients=_RATE_LIMIT_MAX_CLIENTS,
        rate_limit_requests=RATE_LIMIT_REQUESTS,
        rate_limit_window_seconds=RATE_LIMIT_WINDOW_SECONDS,
    )
    return allowed, retry_after


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
    return previous_or_current_reference(
        globals(),
        "_PREVIOUS_JOB_QUEUE_WATCHDOG_THREAD",
        "_JOB_QUEUE_WATCHDOG_THREAD",
    )


def _get_previous_watchdog_stop():
    """Get the previous watchdog stop event from globals, avoiding stale references."""
    return previous_or_current_reference(
        globals(),
        "_PREVIOUS_JOB_QUEUE_WATCHDOG_STOP",
        "_JOB_QUEUE_WATCHDOG_STOP",
    )


def _get_previous_repair_thread():
    """Get the previous repair thread from globals, avoiding stale references."""
    return previous_or_current_reference(
        globals(),
        "_PREVIOUS_JOB_QUEUE_REPAIR_THREAD",
        "_JOB_QUEUE_REPAIR_THREAD",
    )


def _get_previous_repair_stop():
    """Get the previous repair stop event from globals, avoiding stale references."""
    return previous_or_current_reference(
        globals(),
        "_PREVIOUS_JOB_QUEUE_REPAIR_STOP",
        "_JOB_QUEUE_REPAIR_STOP",
    )


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
    remember_job_queue(_JOB_QUEUE_HISTORY, _JOB_QUEUE_LOCK, queue)


def _prune_job_queue_history() -> None:
    """Remove job queues from history that no longer have visible jobs.

    BUG FIX #3: All access to _JOB_QUEUE_HISTORY is protected by _JOB_QUEUE_LOCK.
    This includes both reading (iteration) and writing (slice assignment).
    """
    prune_job_queue_history(
        _JOB_QUEUE_HISTORY,
        _JOB_QUEUE_LOCK,
        queue_still_has_visible_jobs=_queue_still_has_visible_jobs,
        transient_read_error=_TransientLegacyQueueReadError,
        prune_error_threshold=_LEGACY_QUEUE_PRUNE_ERROR_THRESHOLD,
    )


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
        global _JOB_QUEUE_REPAIR_THREAD, _JOB_QUEUE_REPAIR_STOP
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
    global _JOB_QUEUE_WATCHDOG_STOP, _JOB_QUEUE_WATCHDOG_THREAD
    global _JOB_QUEUE_REPAIR_STOP, _JOB_QUEUE_REPAIR_THREAD

    # BUG FIX #5: Use getter functions instead of cached module-level variables.
    # This prevents issues with stale references during module reload.
    stop_control_thread(
        "Previous job queue repair thread",
        _get_previous_repair_thread(),
        _get_previous_repair_stop(),
    )
    stop_control_thread("Job queue repair thread", _JOB_QUEUE_REPAIR_THREAD, _JOB_QUEUE_REPAIR_STOP)
    _JOB_QUEUE_REPAIR_STOP = None
    _JOB_QUEUE_REPAIR_THREAD = None
    stop_control_thread(
        "Previous job queue watchdog thread",
        _get_previous_watchdog_thread(),
        _get_previous_watchdog_stop(),
    )
    stop_control_thread(
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
    except (RuntimeError, TypeError) as exc:
        # Re-check state under lock after repair failure
        with _JOB_QUEUE_LOCK:
            if getattr(queue_to_repair, "state", None) in _RECOVERABLE_QUEUE_STATES:
                current = JOB_QUEUE
                if current is None:
                    raise RuntimeError("Job queue is unavailable") from exc
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


def _snapshot_job_subsystem(*, start_repair: bool = True) -> _JobSubsystemSnapshot:
    if start_repair:
        _maybe_start_job_queue_repair()

    with _JOB_QUEUE_LOCK:
        _prune_job_queue_history()
        current_queue = JOB_QUEUE
        history = list(_JOB_QUEUE_HISTORY)
        repair_thread = _JOB_QUEUE_REPAIR_THREAD
    return build_job_subsystem_snapshot(
        current_queue=current_queue,
        history=history,
        repair_thread=repair_thread,
        job_db_path=JOB_DB_PATH,
        legacy_unknown_ttl_seconds=LEGACY_UNKNOWN_TTL_SECONDS,
        logger=_logger,
    )


def compare_extractors(html: str, model: str = "gpt-4") -> dict[str, dict[str, Any]]:
    """Compatibility wrapper exposing extractor comparison at module scope."""
    return CompareExtractorsUseCase().execute(html, model=model)


_api_routes = register_api_routes(
    app,
    require_api_key=_require_api_key,
    require_rate_limit=_require_rate_limit,
    providers=ApiRouteProviders(
        api_version=lambda: API_VERSION,
        ingest=lambda: ingest,
        retry_ingest=lambda: retry_ingest,
        ingest_many=lambda: ingest_many,
        generate_security_report=lambda: generate_security_report,
        get_ingest_stats=lambda: get_ingest_stats,
        get_job_queue=lambda: _get_job_queue,
        get_job_record=lambda: _get_job_record,
        get_job_ttl_seconds=lambda: JOB_TTL_SECONDS,
        snapshot_job_subsystem=lambda: _snapshot_job_subsystem,
        compare_extractors=lambda: compare_extractors,
        validate_batch_request_ssrf_async=lambda: validate_batch_request_ssrf_async,
        handle_ingest=lambda: handle_ingest,
        handle_retry_ingest=lambda: handle_retry_ingest,
        handle_sync_batch=lambda: handle_sync_batch,
        handle_batch_submit=lambda: handle_batch_submit,
        handle_batch_status=lambda: handle_batch_status,
        handle_security_report=lambda: handle_security_report,
        handle_extractor_comparison=lambda: handle_extractor_comparison,
        build_stats_payload=lambda: build_stats_payload,
        build_health_payload=lambda: build_health_payload,
        build_detailed_health_payload=lambda: build_detailed_health_payload,
        build_root_payload=lambda: build_root_payload,
    ),
)
ingest_endpoint = _api_routes.ingest_endpoint
retry_ingest_endpoint = _api_routes.retry_ingest_endpoint
batch_ingest_endpoint = _api_routes.batch_ingest_endpoint
batch_job_submit = _api_routes.batch_job_submit
batch_job_status = _api_routes.batch_job_status
security_report_endpoint = _api_routes.security_report_endpoint
extractor_comparison_endpoint = _api_routes.extractor_comparison_endpoint
stats_endpoint = _api_routes.stats_endpoint
health = _api_routes.health
health_detailed = _api_routes.health_detailed
root = _api_routes.root


# Compatibility aliases for existing clients/tests.
register_legacy_routes(
    app,
    dependencies=[Depends(_require_api_key), Depends(_require_rate_limit)],
    ingest_endpoint=ingest_endpoint,
    retry_ingest_endpoint=retry_ingest_endpoint,
    batch_ingest_endpoint=batch_ingest_endpoint,
    security_report_endpoint=security_report_endpoint,
    extractor_comparison_endpoint=extractor_comparison_endpoint,
    health_endpoint=health,
)


def main():
    """Run the server."""
    host = os.getenv("MDI_HOST") or "127.0.0.1"
    uvicorn.run("markdown_ingress.api_server:app", host=host, port=8000, reload=False)


if __name__ == "__main__":
    main()
