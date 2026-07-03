"""In-flight request deduplication helpers for ingestion."""

from __future__ import annotations

import atexit
import copy
import logging
import sys
import threading
import time
import weakref
from collections import OrderedDict

from markdown_ingress.core.exception_copy import EXCEPTION_COPY_ERRORS, copy_exception_for_transfer
from markdown_ingress.core.inflight_cleanup import (
    INFLIGHT_MAX_SIZE,
    cleanup_orphaned_entries,
    evict_lru_entries,
    format_request_key_for_log,
)
from markdown_ingress.core.inflight_entry import (
    InFlightEntry,
    notify_entries_inactive,
)
from markdown_ingress.core.inflight_identity import (
    build_request_identity as _build_request_identity,
)
from markdown_ingress.core.inflight_identity import (
    make_request_key as _make_request_key,
)
from markdown_ingress.core.metadata_keys import (
    CACHE_HIT,
    INFLIGHT_DEDUPLICATED,
    INFLIGHT_SHARED_COUNT,
)
from markdown_ingress.models import SafeDocument

# Backward-compatible public API exports.
build_request_identity = _build_request_identity
make_request_key = _make_request_key

logger = logging.getLogger(__name__)

_INFLIGHT_WAIT_TIMEOUT = 600  # seconds — prevents infinite hang if leader crashes

_INFLIGHT_CLEANUP_INTERVAL_SECONDS = 30.0
_INFLIGHT_THREAD_JOIN_TIMEOUT_SECONDS = 5.0

_ALL_INFLIGHT_REGISTRIES: weakref.WeakSet[InFlightRegistry] = weakref.WeakSet()
_ALL_INFLIGHT_REGISTRIES_LOCK = threading.Lock()


class InFlightRegistry:
    """Isolated in-flight registry owned by one runtime/orchestrator."""

    def __init__(self) -> None:
        self._requests: OrderedDict[str, InFlightEntry] = OrderedDict()
        self._lock = threading.Lock()
        self._cleanup_control_lock = threading.Lock()
        self._last_orphan_cleanup_at: float = 0.0
        self._cleanup_thread: threading.Thread | None = None
        self._cleanup_stop = threading.Event()
        with _ALL_INFLIGHT_REGISTRIES_LOCK:
            _ALL_INFLIGHT_REGISTRIES.add(self)

    def __del__(self) -> None:
        self.stop_periodic_cleanup()

    def start_periodic_cleanup(self, interval_seconds: float = 300.0) -> None:
        """Start background thread for periodic cleanup of orphaned entries.

        Args:
            interval_seconds: Time between cleanup runs (default 5 minutes)

        Entries that exceed TTL without completion are removed even when
        acquire() is not called frequently.
        """
        with self._cleanup_control_lock:
            if self._cleanup_thread is not None and self._cleanup_thread.is_alive():
                return  # Already running
            if self._cleanup_stop.is_set():
                self._cleanup_stop = threading.Event()

            weak_self = weakref.ref(self)
            stop_event = self._cleanup_stop

            def cleanup_loop() -> None:
                while not stop_event.wait(interval_seconds):
                    registry = weak_self()
                    if registry is None:
                        break
                    to_notify: list[InFlightEntry] = []
                    with registry._lock:
                        to_notify = registry._cleanup_orphaned_entries_locked()
                        to_notify.extend(registry._evict_lru_entries_locked())
                    notify_entries_inactive(to_notify)

            self._cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True)
            self._cleanup_thread.start()

    def stop_periodic_cleanup(self) -> None:
        """Stop the background cleanup thread."""
        with self._cleanup_control_lock:
            thread = self._cleanup_thread
            if thread is None:
                return
            self._cleanup_stop.set()
        try:
            thread.join(timeout=_INFLIGHT_THREAD_JOIN_TIMEOUT_SECONDS)
        except RuntimeError:
            # PythonFinalizationError (a RuntimeError subclass, Python 3.13+) is
            # raised when join() runs during interpreter shutdown — e.g. when a
            # non-default registry is finalized via __del__ while the interpreter
            # is tearing down. The cleanup thread is a daemon and is terminated
            # automatically, so there is nothing to wait for. Re-raise anything
            # that is not a shutdown-time failure.
            if not sys.is_finalizing():
                raise
            return
        with self._cleanup_control_lock:
            if self._cleanup_thread is thread and not thread.is_alive():
                self._cleanup_thread = None

    def has_active_cleanup_thread(self) -> bool:
        """Return whether the periodic cleanup thread is alive."""
        thread = self._cleanup_thread
        return thread is not None and thread.is_alive()

    def wait_for_cleanup_thread_stop(self, timeout: float = 5.0) -> bool:
        """Stop and wait briefly for the periodic cleanup thread to terminate."""
        self.stop_periodic_cleanup()
        thread = self._cleanup_thread
        if thread is None:
            return True
        thread.join(timeout=timeout)
        return not thread.is_alive()

    def _cleanup_orphaned_entries_locked(self) -> list[InFlightEntry]:
        """Pop orphaned entries from the registry. Caller must notify them after releasing _lock."""
        return cleanup_orphaned_entries(self._requests, logger)

    def _evict_lru_entries_locked(self) -> list[InFlightEntry]:
        """Evict done/inactive entries with no followers."""
        return evict_lru_entries(self._requests, logger)

    def active_count(self) -> int:
        to_notify: list[InFlightEntry] = []
        with self._lock:
            now = time.monotonic()
            if now - self._last_orphan_cleanup_at >= _INFLIGHT_CLEANUP_INTERVAL_SECONDS:
                to_notify = self._cleanup_orphaned_entries_locked()
                self._last_orphan_cleanup_at = now
            count = sum(1 for e in self._requests.values() if not e.done)
        notify_entries_inactive(to_notify)
        return count

    def acquire(self, request_key: str) -> InFlightEntry | None:
        to_notify: list[InFlightEntry] = []
        result: InFlightEntry | None = None
        skip_new_leader = False

        with self._lock:
            to_notify.extend(self._cleanup_orphaned_entries_locked())
            existing = self._requests.get(request_key)
            if existing is not None:
                if not existing.done and not existing.leader_active:
                    # The previous leader has already been declared inactive.
                    # Drop the stale entry so a new leader can take over.
                    self._requests.pop(request_key)
                    to_notify.append(existing)
                    # Fall through to new leader path
                elif existing.done:
                    if existing.document is not None:
                        # Bump followers under entry.condition — the lock that
                        # guards followers in await_result (decrement) and
                        # release (read). _lock is already held, so this keeps the
                        # established _lock -> condition ordering and prevents a
                        # lost update against a concurrent decrement.
                        with existing.condition:
                            existing.followers += 1
                        self._requests.move_to_end(request_key)
                        result = existing
                        skip_new_leader = True
                    else:
                        # No document (error path) — drop and let caller retry
                        self._requests.pop(request_key)
                        # Fall through to new leader path
                else:
                    # Follower: bump under entry.condition (see note above) so the
                    # increment is mutually exclusive with the await_result
                    # decrement; _lock is held, preserving _lock -> condition.
                    with existing.condition:
                        existing.followers += 1
                    self._requests.move_to_end(request_key)
                    result = existing
                    skip_new_leader = True

            if not skip_new_leader:
                to_notify.extend(self._evict_lru_entries_locked())
                if len(self._requests) >= INFLIGHT_MAX_SIZE:
                    logger.warning(
                        "In-flight registry saturated; skipping dedup registration for key=%s",
                        format_request_key_for_log(request_key),
                    )
                else:
                    self._requests[request_key] = InFlightEntry(request_key=request_key)

        notify_entries_inactive(to_notify)

        return result

    def await_result(self, entry: InFlightEntry, request_key: str) -> SafeDocument:
        with entry.condition:
            try:
                while not entry.done:
                    if not entry.leader_active:
                        raise RuntimeError(
                            "In-flight ingestion failed - leader was marked inactive"
                        )

                    if not entry.condition.wait(timeout=_INFLIGHT_WAIT_TIMEOUT):
                        if entry.done:
                            break
                        entry.leader_active = False
                        logger.error(
                            "Marked in-flight entry as inactive after timeout "
                            "(key=%s, followers=%d)",
                            format_request_key_for_log(request_key),
                            entry.followers,
                        )
                        entry.condition.notify_all()
                        raise RuntimeError(
                            "In-flight ingestion timed out waiting for leader — "
                            "the leader may have crashed without calling release_inflight()"
                        )

                if entry.error is not None:
                    raise entry.error
                if entry.document is None:
                    raise RuntimeError("In-flight ingestion finished without result")
                return copy.deepcopy(entry.document)
            finally:
                entry.followers -= 1

    def _handle_double_release_locked(
        self,
        entry: InFlightEntry,
        request_key: str,
    ) -> tuple[bool, int]:
        """Called under self._lock. Acquires entry.condition to detect double-release.

        Returns (should_bail, followers). If should_bail is True, caller must return followers.
        Side effect: sets entry.completing = True when NOT bailing.
        """
        with entry.condition:
            if not entry.done:
                entry.completing = True
                return False, 0
            logger.warning(
                "Double-release detected for request_key=%s, returning followers=%d",
                format_request_key_for_log(request_key),
                entry.followers,
            )
            followers = entry.followers
            return True, followers

    def release(
        self,
        request_key: str,
        *,
        document: SafeDocument | None = None,
        error: Exception | None = None,
    ) -> int:
        to_notify: list[InFlightEntry] = []
        entry: InFlightEntry | None = None
        double_release_followers: int | None = None
        with self._lock:
            to_notify.extend(self._cleanup_orphaned_entries_locked())
            entry = self._requests.get(request_key)
            if entry is not None:
                should_bail, followers = self._handle_double_release_locked(entry, request_key)
                if should_bail:
                    double_release_followers = followers
            # Note: shared_count will be read inside entry.condition to avoid race.

        notify_entries_inactive(to_notify)

        if double_release_followers is not None:
            return double_release_followers
        if entry is None:
            return 0

        with entry.condition:
            shared_count = entry.followers
            try:
                entry.document = copy.deepcopy(document) if document is not None else None
                if entry.document is not None:
                    entry.document.metadata[INFLIGHT_SHARED_COUNT] = shared_count
                if error is not None:
                    entry.error = copy_exception_for_transfer(error)
            except EXCEPTION_COPY_ERRORS as exc:
                entry.error = exc
            finally:
                entry.completing = True
                entry.done = True
                entry.condition.notify_all()

        # Preserve _lock -> entry.condition ordering used by acquire(); do not
        # take _lock while entry.condition is held.
        with self._lock:
            current = self._requests.get(request_key)
            if current is entry:
                self._requests.pop(request_key)

        return shared_count


def inflight_active_count(registry: InFlightRegistry | None = None) -> int:
    """Return the current number of leader requests still in progress."""
    if registry is not None:
        return registry.active_count()
    with _ALL_INFLIGHT_REGISTRIES_LOCK:
        registries = list(_ALL_INFLIGHT_REGISTRIES)
    return sum(registry.active_count() for registry in registries)


_DEFAULT_INFLIGHT_REGISTRY = InFlightRegistry()
_DEFAULT_INFLIGHT_REGISTRY.start_periodic_cleanup()
atexit.register(_DEFAULT_INFLIGHT_REGISTRY.stop_periodic_cleanup)


def acquire_inflight(
    request_key: str,
    *,
    registry: InFlightRegistry | None = None,
) -> InFlightEntry | None:
    return (registry or _DEFAULT_INFLIGHT_REGISTRY).acquire(request_key)


def await_inflight(
    entry: InFlightEntry,
    request_key: str,
    *,
    registry: InFlightRegistry | None = None,
) -> SafeDocument:
    return (registry or _DEFAULT_INFLIGHT_REGISTRY).await_result(entry, request_key)


def release_inflight(
    request_key: str,
    *,
    document: SafeDocument | None = None,
    error: Exception | None = None,
    registry: InFlightRegistry | None = None,
) -> int:
    return (registry or _DEFAULT_INFLIGHT_REGISTRY).release(
        request_key,
        document=document,
        error=error,
    )


def clone_cached_document(document: SafeDocument) -> SafeDocument:
    """Return an isolated cached document copy with cache metadata reset."""
    cached_copy = copy.deepcopy(document)
    cached_copy.metadata[CACHE_HIT] = True
    cached_copy.metadata[INFLIGHT_DEDUPLICATED] = False
    cached_copy.metadata[INFLIGHT_SHARED_COUNT] = 0
    return cached_copy
