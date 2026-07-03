"""Runtime helpers for API server job-queue orchestration."""

from __future__ import annotations

import logging
import sqlite3
import sys
import threading
import time
from collections.abc import Callable
from typing import Any, cast

from markdown_ingress.adapters.jobs.sqlite_job_queue import (
    LEGACY_UNKNOWN_TTL_SECONDS,
    PersistentJobQueue,
    SQLiteError,
)
from markdown_ingress.api_server_job_history import (
    prune_job_queue_history,
    remember_job_queue,
)
from markdown_ingress.api_server_job_queue_build import build_persistent_job_queue
from markdown_ingress.api_server_job_queue_init import (
    close_previous_job_queue_for_init,
    fallback_queue_for_init_build_error,
)
from markdown_ingress.api_server_job_queue_repair import (
    job_queue_repair_finished,
    job_queue_repair_retry_delay,
)
from markdown_ingress.api_server_job_queue_selection import (
    JobQueueSelection,
    current_queue_after_repair_close_failure,
    current_queue_if_expected_changed,
    queue_if_expected_state,
    select_job_queue_for_use,
)
from markdown_ingress.api_server_job_queue_states import (
    STATE_BACKEND_ERROR,
    STATE_EXTERNAL_OWNER,
)
from markdown_ingress.api_server_queue import (
    _LEGACY_QUEUE_PRUNE_ERROR_THRESHOLD,
    _close_queue_for_repair,
    _ExternalOwnerJobQueue,
    _is_active_owner_error,
    _queue_still_has_visible_jobs,
    _TransientLegacyQueueReadError,
)
from markdown_ingress.api_server_queue import (
    _external_owner_backend_still_owned as _queue_external_owner_backend_still_owned,
)
from markdown_ingress.api_server_snapshot import (
    JobQueueStateSnapshot,
    JobSubsystemSnapshotInputs,
    build_job_subsystem_snapshot,
)
from markdown_ingress.api_server_threads import (
    start_control_thread,
    stop_reloaded_control_thread_pair,
)

_logger = logging.getLogger(__name__)


def _api_server_context() -> Any:
    """Return the API module object used as runtime context."""
    context = sys.modules.get("markdown_ingress.api_server")
    if context is None:
        raise RuntimeError("markdown_ingress.api_server is not initialized")
    return context


def _call_with_context(
    context: Any,
    name: str,
    fallback: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    target = getattr(context, name, fallback)
    if not callable(target):
        target = fallback
    return target(*args, **kwargs)


def _build_job_queue() -> PersistentJobQueue:
    context = _api_server_context()
    queue_class = cast(
        type[PersistentJobQueue],
        getattr(context, "PersistentJobQueue", PersistentJobQueue),
    )
    return build_persistent_job_queue(
        queue_class=queue_class,
        db_path=context.JOB_DB_PATH,
        worker_count=context.JOB_WORKERS,
        ttl_seconds=context.JOB_TTL_SECONDS,
        max_queued_jobs=context.MAX_QUEUED_JOBS,
        webhook_max_retries=context.JOB_WEBHOOK_MAX_RETRIES,
        webhook_retry_delay_seconds=context.JOB_WEBHOOK_RETRY_DELAY_SECONDS,
        allow_local_webhooks=context.ALLOW_LOCAL_WEBHOOKS,
        job_timeout_seconds=context.JOB_EXECUTION_TIMEOUT_SECONDS,
    )


def _remember_job_queue(queue: PersistentJobQueue | None) -> None:
    """Record a job queue in history for cleanup tracking."""
    context = _api_server_context()
    remember_job_queue(context._JOB_QUEUE_HISTORY, context._JOB_QUEUE_LOCK, queue)


def _prune_job_queue_history() -> None:
    """Remove job queues from history that no longer have visible jobs."""
    context = _api_server_context()
    prune_job_queue_history(
        context._JOB_QUEUE_HISTORY,
        context._JOB_QUEUE_LOCK,
        queue_still_has_visible_jobs=_queue_still_has_visible_jobs,
        transient_read_error=_TransientLegacyQueueReadError,
        prune_error_threshold=_LEGACY_QUEUE_PRUNE_ERROR_THRESHOLD,
    )


def _replace_job_queue_if_current(expected_queue, replacement_queue) -> bool:
    context = _api_server_context()
    with context._JOB_QUEUE_LOCK:
        if context.JOB_QUEUE is not expected_queue:
            return False
        _remember_job_queue(context.JOB_QUEUE)
        context.JOB_QUEUE = replacement_queue
        return True


def _promote_external_owner_queue(expected_queue):
    context = _api_server_context()
    if getattr(expected_queue, "state", None) == STATE_EXTERNAL_OWNER:
        return expected_queue
    replacement_queue = _call_with_context(
        context,
        "_ExternalOwnerJobQueue",
        _ExternalOwnerJobQueue,
        getattr(expected_queue, "db_path", context.JOB_DB_PATH),
    )
    if _replace_job_queue_if_current(expected_queue, replacement_queue):
        return replacement_queue
    with context._JOB_QUEUE_LOCK:
        return context.JOB_QUEUE


def _current_queue_if_expected_changed(expected_queue):
    context = _api_server_context()
    with context._JOB_QUEUE_LOCK:
        return current_queue_if_expected_changed(expected_queue, context.JOB_QUEUE)


def _queue_if_expected_state(expected_queue, states: set[str]):
    context = _api_server_context()
    with context._JOB_QUEUE_LOCK:
        return queue_if_expected_state(expected_queue, states)


def _replacement_for_runtime_build_error(expected_queue, exc: RuntimeError):
    if _is_active_owner_error(exc):
        return _promote_external_owner_queue(expected_queue)
    if getattr(expected_queue, "state", None) == STATE_BACKEND_ERROR:
        return expected_queue
    return None


def _current_queue_after_superseded_replacement(replacement_queue):
    context = _api_server_context()
    try:
        replacement_queue.close()
    except (RuntimeError, SQLiteError, OSError, ValueError) as exc:
        _logger.debug("Failed to close superseded replacement queue: %s", exc, exc_info=True)
    with context._JOB_QUEUE_LOCK:
        return context.JOB_QUEUE


def _build_replacement_queue_or_current(expected_queue):
    context = _api_server_context()
    with context._JOB_QUEUE_BUILD_LOCK:
        current_queue = _call_with_context(
            context,
            "_current_queue_if_expected_changed",
            _current_queue_if_expected_changed,
            expected_queue,
        )
        if current_queue is not None:
            return current_queue
        try:
            replacement_queue = _call_with_context(
                context,
                "_build_job_queue",
                _build_job_queue,
            )
        except RuntimeError as exc:
            fallback_queue = _call_with_context(
                context,
                "_replacement_for_runtime_build_error",
                _replacement_for_runtime_build_error,
                expected_queue,
                exc,
            )
            if fallback_queue is not None:
                return fallback_queue
            raise
        except (SQLiteError, OSError):
            fallback_queue = _call_with_context(
                context,
                "_queue_if_expected_state",
                _queue_if_expected_state,
                expected_queue,
                {STATE_BACKEND_ERROR, STATE_EXTERNAL_OWNER},
            )
            if fallback_queue is not None:
                return fallback_queue
            raise
        if _call_with_context(
            context,
            "_replace_job_queue_if_current",
            _replace_job_queue_if_current,
            expected_queue,
            replacement_queue,
        ):
            return replacement_queue
        return _call_with_context(
            context,
            "_current_queue_after_superseded_replacement",
            _current_queue_after_superseded_replacement,
            replacement_queue,
        )


def _external_owner_backend_still_owned(queue) -> bool:
    context = _api_server_context()
    return _queue_external_owner_backend_still_owned(queue, context.JOB_DB_PATH)


def _clear_job_queue_repair_state_locked(
    expected_stop_event: threading.Event | None = None,
) -> None:
    context = _api_server_context()
    if (
        expected_stop_event is not None
        and context._JOB_QUEUE_REPAIR_STOP is not expected_stop_event
    ):
        return
    context._JOB_QUEUE_REPAIR_THREAD = None
    context._JOB_QUEUE_REPAIR_STOP = None


def _clear_job_queue_repair_state(
    expected_stop_event: threading.Event | None = None,
) -> None:
    context = _api_server_context()
    with context._JOB_QUEUE_LOCK:
        _clear_job_queue_repair_state_locked(expected_stop_event)


def _current_recoverable_job_queue(
    stop_event: threading.Event,
) -> tuple[Any, str | None] | None:
    context = _api_server_context()
    with context._JOB_QUEUE_LOCK:
        queue = context.JOB_QUEUE
        if queue is None:
            _clear_job_queue_repair_state_locked(stop_event)
            return None
        state = getattr(queue, "state", None)
        if state not in context._REPAIRABLE_QUEUE_STATES:
            _clear_job_queue_repair_state_locked(stop_event)
            return None
        return queue, state


def _wait_for_next_job_queue_repair_attempt(stop_event: threading.Event, state: str | None) -> None:
    context = _api_server_context()
    stop_event.wait(
        job_queue_repair_retry_delay(
            state,
            external_owner_seconds=context._EXTERNAL_OWNER_REPAIR_RETRY_SECONDS,
            backend_error_seconds=context._BACKEND_ERROR_REPAIR_RETRY_SECONDS,
        )
    )


def _maybe_wait_for_external_owner_backend(
    queue: Any,
    state: str | None,
    stop_event: threading.Event,
) -> tuple[str | None, bool]:
    context = _api_server_context()
    if state != STATE_EXTERNAL_OWNER:
        return state, False
    try:
        backend_still_owned = _external_owner_backend_still_owned(queue)
    except RuntimeError:
        return getattr(queue, "state", None), False
    if backend_still_owned:
        stop_event.wait(context._EXTERNAL_OWNER_REPAIR_RETRY_SECONDS)
        return state, True
    return state, False


def _finish_repair_if_replaced_or_terminal(
    queue: Any,
    state: str | None,
    stop_event: threading.Event,
) -> bool:
    replacement_queue = _call_with_context(
        _api_server_context(),
        "_build_replacement_queue_or_current",
        _build_replacement_queue_or_current,
        queue,
    )
    if job_queue_repair_finished(queue, replacement_queue, state):
        _clear_job_queue_repair_state(stop_event)
        return True
    return False


def _run_job_queue_repair_attempt(stop_event: threading.Event) -> bool:
    context = _api_server_context()
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
            repair_finished = _call_with_context(
                context,
                "_finish_repair_if_replaced_or_terminal",
                _finish_repair_if_replaced_or_terminal,
                queue,
                state,
                stop_event,
            )
        except (RuntimeError, SQLiteError, OSError) as exc:
            _logger.debug("Job queue repair rebuild failed: %s", exc, exc_info=True)
        else:
            if repair_finished:
                return False
    _call_with_context(
        context,
        "_wait_for_next_job_queue_repair_attempt",
        _wait_for_next_job_queue_repair_attempt,
        stop_event,
        state,
    )
    return True


def _job_queue_repair_loop(stop_event: threading.Event) -> None:
    while not stop_event.wait(0.0):
        if not _run_job_queue_repair_attempt(stop_event):
            return


def _start_job_queue_repair_loop() -> None:
    context = _api_server_context()

    def remember_thread(thread: threading.Thread, stop_event: threading.Event) -> None:
        context._JOB_QUEUE_REPAIR_STOP = stop_event
        context._JOB_QUEUE_REPAIR_THREAD = thread

    with context._JOB_QUEUE_LOCK:
        start_control_thread(
            current_thread=context._JOB_QUEUE_REPAIR_THREAD,
            target=_job_queue_repair_loop,
            remember=remember_thread,
        )


def _maybe_start_job_queue_repair() -> None:
    context = _api_server_context()
    with context._JOB_QUEUE_LOCK:
        queue = context.JOB_QUEUE
        state = getattr(queue, "state", None)
        repair_thread = context._JOB_QUEUE_REPAIR_THREAD
    if state in context._REPAIRABLE_QUEUE_STATES and not (
        repair_thread is not None and repair_thread.is_alive()
    ):
        _call_with_context(context, "_start_job_queue_repair_loop", _start_job_queue_repair_loop)


def _job_queue_watchdog_tick() -> None:
    _call_with_context(
        _api_server_context(),
        "_maybe_start_job_queue_repair",
        _maybe_start_job_queue_repair,
    )


def _start_job_queue_watchdog() -> None:
    context = _api_server_context()

    def run_watchdog(stop_event: threading.Event) -> None:
        while not stop_event.wait(0.5):
            _call_with_context(
                context,
                "_job_queue_watchdog_tick",
                _job_queue_watchdog_tick,
            )

    def remember_thread(thread: threading.Thread, stop_event: threading.Event) -> None:
        context._JOB_QUEUE_WATCHDOG_STOP = stop_event
        context._JOB_QUEUE_WATCHDOG_THREAD = thread

    with context._JOB_QUEUE_LOCK:
        start_control_thread(
            current_thread=context._JOB_QUEUE_WATCHDOG_THREAD,
            target=run_watchdog,
            remember=remember_thread,
        )


def _stop_reloaded_job_queue_control_threads() -> None:
    context = _api_server_context()
    for name, prefix in (
        ("job queue repair thread", "JOB_QUEUE_REPAIR"),
        ("job queue watchdog thread", "JOB_QUEUE_WATCHDOG"),
    ):
        stop_reloaded_control_thread_pair(
            module_globals=context.__dict__,
            name=name,
            previous_thread_key=f"_PREVIOUS_{prefix}_THREAD",
            current_thread_key=f"_{prefix}_THREAD",
            previous_stop_key=f"_PREVIOUS_{prefix}_STOP",
            current_stop_key=f"_{prefix}_STOP",
        )


def _reset_job_queue_control_thread_refs() -> None:
    context = _api_server_context()
    context._JOB_QUEUE_REPAIR_STOP = None
    context._JOB_QUEUE_REPAIR_THREAD = None
    context._JOB_QUEUE_WATCHDOG_STOP = None
    context._JOB_QUEUE_WATCHDOG_THREAD = None


def _fallback_queue_for_init_build_error(previous_queue: Any | None, exc: RuntimeError):
    context = _api_server_context()
    return fallback_queue_for_init_build_error(
        previous_queue,
        exc,
        is_active_owner_error=_is_active_owner_error,
        promote_external_owner_queue=_promote_external_owner_queue,
        external_owner_queue=lambda: context._ExternalOwnerJobQueue(context.JOB_DB_PATH),
    )


def _init_job_queue(previous_queue=None):
    context = _api_server_context()
    _stop_reloaded_job_queue_control_threads()
    _reset_job_queue_control_thread_refs()
    reused_queue = close_previous_job_queue_for_init(
        previous_queue,
        recoverable_states=context._RECOVERABLE_QUEUE_STATES,
        remember_job_queue=_remember_job_queue,
    )
    if reused_queue is not None:
        return reused_queue
    try:
        return _call_with_context(context, "_build_job_queue", _build_job_queue)
    except RuntimeError as exc:
        fallback_queue = _fallback_queue_for_init_build_error(previous_queue, exc)
        if fallback_queue is not None:
            return fallback_queue
        raise


def _job_queue_init_backoff_active() -> bool:
    context = _api_server_context()
    return (
        context._job_queue_init_failed_at is not None
        and time.monotonic() - context._job_queue_init_failed_at
        < context._JOB_QUEUE_RETRY_BACKOFF_SECONDS
    )


def _ensure_job_queue_initialized():
    """Initialize job queue lazily to prevent server crash on module import.

    If initialization fails, the error is deferred until first use,
    allowing the server to start and serve endpoints that don't require the job queue.
    """
    context = _api_server_context()
    if context._job_queue_initialized:
        return
    if _job_queue_init_backoff_active():
        return
    with context._JOB_QUEUE_INIT_LOCK:
        if context._job_queue_initialized:
            return
        if _job_queue_init_backoff_active():
            return
        if context.JOB_QUEUE is not None:
            context._job_queue_initialized = True
            context._job_queue_init_failed_at = None
            _call_with_context(context, "_start_job_queue_watchdog", _start_job_queue_watchdog)
            return
        try:
            context.JOB_QUEUE = _call_with_context(
                context,
                "_init_job_queue",
                _init_job_queue,
                getattr(context, "JOB_QUEUE", None),
            )
            context._job_queue_initialized = True
            context._job_queue_init_failed_at = None
            _call_with_context(context, "_start_job_queue_watchdog", _start_job_queue_watchdog)
        except (OSError, ValueError, RuntimeError, ImportError, sqlite3.Error):
            context._job_queue_init_failed_at = time.monotonic()
            _logger.exception("Failed to initialize job queue")


def _select_job_queue_for_use() -> JobQueueSelection:
    context = _api_server_context()
    with context._JOB_QUEUE_LOCK:
        return select_job_queue_for_use(context.JOB_QUEUE, context._REPAIRABLE_QUEUE_STATES)


def _current_queue_after_repair_close_failure(queue_to_repair: Any) -> Any | None:
    context = _api_server_context()
    with context._JOB_QUEUE_LOCK:
        return current_queue_after_repair_close_failure(
            queue_to_repair,
            context.JOB_QUEUE,
            context._REPAIRABLE_QUEUE_STATES,
        )


def _get_job_queue():
    context = _api_server_context()
    _call_with_context(context, "_ensure_job_queue_initialized", _ensure_job_queue_initialized)
    selection = _call_with_context(context, "_select_job_queue_for_use", _select_job_queue_for_use)
    if selection.queue_to_return is not None:
        if selection.start_repair:
            _call_with_context(
                context,
                "_maybe_start_job_queue_repair",
                _maybe_start_job_queue_repair,
            )
        return selection.queue_to_return
    queue_to_repair = selection.queue_to_repair
    if queue_to_repair is None:
        raise RuntimeError("Job queue is unavailable")

    try:
        _call_with_context(
            context,
            "_close_queue_for_repair",
            _close_queue_for_repair,
            queue_to_repair,
        )
    except (RuntimeError, TypeError) as exc:
        try:
            current = _call_with_context(
                context,
                "_current_queue_after_repair_close_failure",
                _current_queue_after_repair_close_failure,
                queue_to_repair,
            )
        except RuntimeError as unavailable:
            raise unavailable from exc
        if current is not None:
            _call_with_context(
                context,
                "_maybe_start_job_queue_repair",
                _maybe_start_job_queue_repair,
            )
            return current
        raise
    return _call_with_context(
        context,
        "_build_replacement_queue_or_current",
        _build_replacement_queue_or_current,
        queue_to_repair,
    )


def _snapshot_job_queue_state():
    context = _api_server_context()
    with context._JOB_QUEUE_LOCK:
        _prune_job_queue_history()
        return JobQueueStateSnapshot(
            current_queue=context.JOB_QUEUE,
            history=list(context._JOB_QUEUE_HISTORY),
            repair_thread=context._JOB_QUEUE_REPAIR_THREAD,
        )


def _snapshot_job_subsystem(*, start_repair: bool = True):
    context = _api_server_context()
    if start_repair:
        _call_with_context(
            context,
            "_maybe_start_job_queue_repair",
            _maybe_start_job_queue_repair,
        )

    snapshot = _call_with_context(context, "_snapshot_job_queue_state", _snapshot_job_queue_state)
    current_queue = snapshot.current_queue
    history = snapshot.history
    repair_thread = snapshot.repair_thread
    if current_queue is None and not history:
        _call_with_context(
            context,
            "_ensure_job_queue_initialized",
            _ensure_job_queue_initialized,
        )
        snapshot = _call_with_context(
            context, "_snapshot_job_queue_state", _snapshot_job_queue_state
        )
        current_queue = snapshot.current_queue
        history = snapshot.history
        repair_thread = snapshot.repair_thread
    return build_job_subsystem_snapshot(
        JobSubsystemSnapshotInputs(
            current_queue=current_queue,
            history=history,
            repair_thread=repair_thread,
            job_db_path=context.JOB_DB_PATH,
            legacy_unknown_ttl_seconds=LEGACY_UNKNOWN_TTL_SECONDS,
            logger=_logger,
        )
    )
