"""
FastAPI server for MarkDownIngress.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
from markdown_ingress.api_server_job_queue_build import build_persistent_job_queue
from markdown_ingress.api_server_legacy_routes import LegacyRouteHandlers, register_legacy_routes
from markdown_ingress.api_server_queue import (
    _LEGACY_QUEUE_PRUNE_ERROR_THRESHOLD,
    _close_queue_for_repair,
    _ExternalOwnerJobQueue,
    _find_job_record_in_queues,
    _is_active_owner_error,
    _is_stale_heartbeat,
    _queue_still_has_visible_jobs,
    _TransientLegacyQueueReadError,
)
from markdown_ingress.api_server_rate_limit import (
    MemoryRateLimitPolicy,
    MemoryRateLimitState,
    RequestWindow,
    check_memory_rate_limit,
)
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
    JobSubsystemSnapshotInputs,
    build_job_subsystem_snapshot,
)
from markdown_ingress.api_server_support import validate_batch_request_ssrf_async
from markdown_ingress.api_server_threads import (
    start_control_thread,
    stop_reloaded_control_thread_pair,
)
from markdown_ingress.application.use_cases import CompareExtractorsUseCase
from markdown_ingress.core.orchestrator import get_ingest_stats

_logger = logging.getLogger(__name__)

API_VERSION = "1.0.0"

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
_rate_limit_cleanup_counter: int = 0
_RATE_LIMIT_CLEANUP_THRESHOLD: int = 1000
_RATE_LIMIT_MAX_CLIENTS: int = 10000

_RATE_LIMIT_BACKEND: str = os.getenv("MDI_RATE_LIMIT_BACKEND", "memory").strip().lower()

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
    rate_limit_state = MemoryRateLimitState(
        request_counts=_request_counts,
        lock=_rate_limit_lock,
        cleanup_counter=_rate_limit_cleanup_counter,
    )
    allowed, retry_after, _rate_limit_cleanup_counter = check_memory_rate_limit(
        client_id,
        rate_limit_state,
        MemoryRateLimitPolicy(
            cleanup_threshold=_RATE_LIMIT_CLEANUP_THRESHOLD,
            max_clients=_RATE_LIMIT_MAX_CLIENTS,
            rate_limit_requests=RATE_LIMIT_REQUESTS,
            rate_limit_window_seconds=RATE_LIMIT_WINDOW_SECONDS,
        ),
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
_REPAIRABLE_QUEUE_STATES = _RECOVERABLE_QUEUE_STATES | {"closed"}
_EXTERNAL_OWNER_REPAIR_RETRY_SECONDS = 5.0
_BACKEND_ERROR_REPAIR_RETRY_SECONDS = 5.0


@dataclass(frozen=True)
class _JobQueueSelection:
    queue_to_return: Any | None
    queue_to_repair: Any | None
    start_repair: bool = False


def _build_job_queue() -> PersistentJobQueue:
    return build_persistent_job_queue(
        queue_class=PersistentJobQueue,
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
    """Record a job queue in history for cleanup tracking."""
    remember_job_queue(_JOB_QUEUE_HISTORY, _JOB_QUEUE_LOCK, queue)


def _prune_job_queue_history() -> None:
    """Remove job queues from history that no longer have visible jobs."""
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


def _current_queue_if_expected_changed(expected_queue):
    with _JOB_QUEUE_LOCK:
        if JOB_QUEUE is not expected_queue:
            return JOB_QUEUE
    return None


def _queue_if_expected_state(expected_queue, states: set[str]):
    with _JOB_QUEUE_LOCK:
        if getattr(expected_queue, "state", None) in states:
            return expected_queue
    return None


def _replacement_for_runtime_build_error(expected_queue, exc: RuntimeError):
    if _is_active_owner_error(exc):
        return _promote_external_owner_queue(expected_queue)
    if getattr(expected_queue, "state", None) == "backend_error":
        return expected_queue
    return None


def _current_queue_after_superseded_replacement(replacement_queue):
    try:
        replacement_queue.close()
    except Exception as exc:  # noqa: BLE001 - superseded queue cleanup is best effort
        _logger.debug("Failed to close superseded replacement queue: %s", exc, exc_info=True)
    with _JOB_QUEUE_LOCK:
        return JOB_QUEUE


def _build_replacement_queue_or_current(expected_queue):
    with _JOB_QUEUE_BUILD_LOCK:
        current_queue = _current_queue_if_expected_changed(expected_queue)
        if current_queue is not None:
            return current_queue
        try:
            replacement_queue = _build_job_queue()
        except RuntimeError as exc:
            fallback_queue = _replacement_for_runtime_build_error(expected_queue, exc)
            if fallback_queue is not None:
                return fallback_queue
            raise
        except (SQLiteError, OSError):
            fallback_queue = _queue_if_expected_state(
                expected_queue, {"backend_error", "external_owner"}
            )
            if fallback_queue is not None:
                return fallback_queue
            raise
        if _replace_job_queue_if_current(expected_queue, replacement_queue):
            return replacement_queue
        return _current_queue_after_superseded_replacement(replacement_queue)


def _external_owner_backend_still_owned(queue) -> bool:
    db_path = Path(getattr(queue, "db_path", JOB_DB_PATH))

    def _set_backend_error(_state: str) -> None:
        queue.state = _state

    return check_external_owner_still_owns(db_path, _is_stale_heartbeat, _set_backend_error)


def _clear_job_queue_repair_state_locked(
    expected_stop_event: threading.Event | None = None,
) -> None:
    global _JOB_QUEUE_REPAIR_THREAD, _JOB_QUEUE_REPAIR_STOP
    if expected_stop_event is not None and _JOB_QUEUE_REPAIR_STOP is not expected_stop_event:
        return
    _JOB_QUEUE_REPAIR_THREAD = None
    _JOB_QUEUE_REPAIR_STOP = None


def _clear_job_queue_repair_state(
    expected_stop_event: threading.Event | None = None,
) -> None:
    with _JOB_QUEUE_LOCK:
        _clear_job_queue_repair_state_locked(expected_stop_event)


def _current_recoverable_job_queue(
    stop_event: threading.Event,
) -> tuple[Any, str | None] | None:
    with _JOB_QUEUE_LOCK:
        queue = JOB_QUEUE
        if queue is None:
            _clear_job_queue_repair_state_locked(stop_event)
            return None
        state = getattr(queue, "state", None)
        if state not in _REPAIRABLE_QUEUE_STATES:
            _clear_job_queue_repair_state_locked(stop_event)
            return None
        return queue, state


def _wait_for_next_job_queue_repair_attempt(stop_event: threading.Event, state: str | None) -> None:
    if state == "external_owner":
        stop_event.wait(_EXTERNAL_OWNER_REPAIR_RETRY_SECONDS)
    elif state == "backend_error":
        stop_event.wait(_BACKEND_ERROR_REPAIR_RETRY_SECONDS)
    else:
        stop_event.wait(0.25)


def _maybe_wait_for_external_owner_backend(
    queue: Any, state: str | None, stop_event: threading.Event
) -> tuple[str | None, bool]:
    if state != "external_owner":
        return state, False
    try:
        backend_still_owned = _external_owner_backend_still_owned(queue)
    except RuntimeError:
        return getattr(queue, "state", None), False
    if backend_still_owned:
        stop_event.wait(_EXTERNAL_OWNER_REPAIR_RETRY_SECONDS)
        return state, True
    return state, False


def _finish_repair_if_replaced_or_terminal(
    queue: Any,
    state: str | None,
    stop_event: threading.Event,
) -> bool:
    replacement_queue = _build_replacement_queue_or_current(queue)
    if replacement_queue is not queue:
        _clear_job_queue_repair_state(stop_event)
        return True
    if state == "backend_error":
        _clear_job_queue_repair_state(stop_event)
        return True
    return False


def _run_job_queue_repair_attempt(stop_event: threading.Event) -> bool:
    candidate = _current_recoverable_job_queue(stop_event)
    if candidate is None:
        return False
    queue, state = candidate
    try:
        _close_queue_for_repair(queue)
    except RuntimeError as e:
        _logger.debug("Failed to close queue for repair: %s", e)
    else:
        state, retry_later = _maybe_wait_for_external_owner_backend(queue, state, stop_event)
        if retry_later:
            return True
        try:
            repair_finished = _finish_repair_if_replaced_or_terminal(queue, state, stop_event)
        except (RuntimeError, SQLiteError, OSError) as exc:
            _logger.debug("Job queue repair rebuild failed: %s", exc, exc_info=True)
        else:
            if repair_finished:
                return False
    _wait_for_next_job_queue_repair_attempt(stop_event, state)
    return True


def _job_queue_repair_loop(stop_event: threading.Event) -> None:
    while not stop_event.wait(0.0):
        if not _run_job_queue_repair_attempt(stop_event):
            return


def _start_job_queue_repair_loop() -> None:
    def remember_thread(thread: threading.Thread, stop_event: threading.Event) -> None:
        global _JOB_QUEUE_REPAIR_THREAD, _JOB_QUEUE_REPAIR_STOP
        _JOB_QUEUE_REPAIR_STOP = stop_event
        _JOB_QUEUE_REPAIR_THREAD = thread

    with _JOB_QUEUE_LOCK:
        start_control_thread(
            current_thread=_JOB_QUEUE_REPAIR_THREAD,
            target=_job_queue_repair_loop,
            remember=remember_thread,
        )


def _maybe_start_job_queue_repair() -> None:
    with _JOB_QUEUE_LOCK:
        queue = JOB_QUEUE
        state = getattr(queue, "state", None)
        repair_thread = _JOB_QUEUE_REPAIR_THREAD
    if state in _REPAIRABLE_QUEUE_STATES and not (
        repair_thread is not None and repair_thread.is_alive()
    ):
        _start_job_queue_repair_loop()


def _job_queue_watchdog_tick() -> None:
    _maybe_start_job_queue_repair()


def _start_job_queue_watchdog() -> None:
    def run_watchdog(stop_event: threading.Event) -> None:
        while not stop_event.wait(0.5):
            _job_queue_watchdog_tick()

    def remember_thread(thread: threading.Thread, stop_event: threading.Event) -> None:
        global _JOB_QUEUE_WATCHDOG_THREAD, _JOB_QUEUE_WATCHDOG_STOP
        _JOB_QUEUE_WATCHDOG_STOP = stop_event
        _JOB_QUEUE_WATCHDOG_THREAD = thread

    with _JOB_QUEUE_LOCK:
        start_control_thread(
            current_thread=_JOB_QUEUE_WATCHDOG_THREAD,
            target=run_watchdog,
            remember=remember_thread,
        )


def _stop_reloaded_job_queue_control_threads() -> None:
    for name, prefix in (
        ("job queue repair thread", "JOB_QUEUE_REPAIR"),
        ("job queue watchdog thread", "JOB_QUEUE_WATCHDOG"),
    ):
        stop_reloaded_control_thread_pair(
            module_globals=globals(),
            name=name,
            previous_thread_key=f"_PREVIOUS_{prefix}_THREAD",
            current_thread_key=f"_{prefix}_THREAD",
            previous_stop_key=f"_PREVIOUS_{prefix}_STOP",
            current_stop_key=f"_{prefix}_STOP",
        )


def _init_job_queue(previous_queue=None):
    global _JOB_QUEUE_WATCHDOG_STOP, _JOB_QUEUE_WATCHDOG_THREAD
    global _JOB_QUEUE_REPAIR_STOP, _JOB_QUEUE_REPAIR_THREAD

    _stop_reloaded_job_queue_control_threads()
    _JOB_QUEUE_REPAIR_STOP = None
    _JOB_QUEUE_REPAIR_THREAD = None
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


# Lazy initialization defers backend errors until an endpoint needs the queue.
JOB_QUEUE: PersistentJobQueue | None = None
_job_queue_initialized = False
_job_queue_init_failed_at: float | None = None
_JOB_QUEUE_RETRY_BACKOFF_SECONDS = 10.0


def _job_queue_init_backoff_active() -> bool:
    return (
        _job_queue_init_failed_at is not None
        and time.monotonic() - _job_queue_init_failed_at < _JOB_QUEUE_RETRY_BACKOFF_SECONDS
    )


def _ensure_job_queue_initialized():
    """Initialize job queue lazily to prevent server crash on module import.

    If initialization fails, the error is deferred until first use,
    allowing the server to start and serve endpoints that don't require the job queue.
    """
    global JOB_QUEUE, _job_queue_initialized, _job_queue_init_failed_at
    if _job_queue_initialized:
        return
    if _job_queue_init_backoff_active():
        return
    with _JOB_QUEUE_INIT_LOCK:
        if _job_queue_initialized:
            return
        if _job_queue_init_backoff_active():
            return
        if JOB_QUEUE is not None:
            _job_queue_initialized = True
            _job_queue_init_failed_at = None
            _start_job_queue_watchdog()
            return
        try:
            JOB_QUEUE = _init_job_queue(globals().get("JOB_QUEUE"))
            _job_queue_initialized = True
            _job_queue_init_failed_at = None
            _start_job_queue_watchdog()
        except (OSError, ValueError, RuntimeError, ImportError, sqlite3.Error):
            _job_queue_init_failed_at = time.monotonic()
            _logger.exception("Failed to initialize job queue")


def _select_job_queue_for_use() -> _JobQueueSelection:
    with _JOB_QUEUE_LOCK:
        queue = JOB_QUEUE
        if queue is None:
            raise RuntimeError("Job queue is unavailable")
        state = getattr(queue, "state", None)
        if state not in _REPAIRABLE_QUEUE_STATES:
            return _JobQueueSelection(queue_to_return=queue, queue_to_repair=None)
        if state == "external_owner":
            return _JobQueueSelection(
                queue_to_return=queue,
                queue_to_repair=None,
                start_repair=True,
            )
        return _JobQueueSelection(queue_to_return=None, queue_to_repair=queue)


def _current_queue_after_repair_close_failure(queue_to_repair: Any) -> Any | None:
    with _JOB_QUEUE_LOCK:
        if getattr(queue_to_repair, "state", None) not in _REPAIRABLE_QUEUE_STATES:
            return None
        current = JOB_QUEUE
        if current is None:
            raise RuntimeError("Job queue is unavailable")
        return current


def _get_job_queue():
    _ensure_job_queue_initialized()
    selection = _select_job_queue_for_use()
    if selection.queue_to_return is not None:
        if selection.start_repair:
            _maybe_start_job_queue_repair()
        return selection.queue_to_return
    queue_to_repair = selection.queue_to_repair
    if queue_to_repair is None:
        raise RuntimeError("Job queue is unavailable")

    try:
        _close_queue_for_repair(queue_to_repair)
    except (RuntimeError, TypeError) as exc:
        try:
            current = _current_queue_after_repair_close_failure(queue_to_repair)
        except RuntimeError as unavailable:
            raise unavailable from exc
        if current is not None:
            _maybe_start_job_queue_repair()
            return current
        raise
    return _build_replacement_queue_or_current(queue_to_repair)


def _get_job_record(job_id: str):
    queue = _get_job_queue()
    with _JOB_QUEUE_LOCK:
        _prune_job_queue_history()
        history = list(_JOB_QUEUE_HISTORY)
    return _find_job_record_in_queues(job_id, queue, history)


def _snapshot_job_subsystem(*, start_repair: bool = True) -> _JobSubsystemSnapshot:
    if start_repair:
        _maybe_start_job_queue_repair()

    with _JOB_QUEUE_LOCK:
        _prune_job_queue_history()
        current_queue = JOB_QUEUE
        history = list(_JOB_QUEUE_HISTORY)
        repair_thread = _JOB_QUEUE_REPAIR_THREAD
    if current_queue is None and not history:
        _ensure_job_queue_initialized()
        with _JOB_QUEUE_LOCK:
            current_queue = JOB_QUEUE
            history = list(_JOB_QUEUE_HISTORY)
            repair_thread = _JOB_QUEUE_REPAIR_THREAD
    return build_job_subsystem_snapshot(
        JobSubsystemSnapshotInputs(
            current_queue=current_queue,
            history=history,
            repair_thread=repair_thread,
            job_db_path=JOB_DB_PATH,
            legacy_unknown_ttl_seconds=LEGACY_UNKNOWN_TTL_SECONDS,
            logger=_logger,
        )
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


register_legacy_routes(
    app,
    [Depends(_require_api_key), Depends(_require_rate_limit)],
    LegacyRouteHandlers(
        ingest_endpoint=ingest_endpoint,
        retry_ingest_endpoint=retry_ingest_endpoint,
        batch_ingest_endpoint=batch_ingest_endpoint,
        security_report_endpoint=security_report_endpoint,
        extractor_comparison_endpoint=extractor_comparison_endpoint,
        health_endpoint=health,
    ),
)


def main():
    """Run the server."""
    import uvicorn

    host = os.getenv("MDI_HOST") or "127.0.0.1"
    port = _read_positive_int_env("MDI_PORT", 8000)
    uvicorn.run("markdown_ingress.api_server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
