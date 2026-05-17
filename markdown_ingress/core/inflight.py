"""In-flight request deduplication helpers for ingestion."""

from __future__ import annotations

import atexit
import copy
import logging
import threading
import time
import weakref
from collections import OrderedDict

from markdown_ingress.core.exception_copy import copy_exception_for_transfer
from markdown_ingress.core.inflight_entry import (
    InFlightEntry,
    notify_entries_inactive,
)
from markdown_ingress.core.inflight_identity import (
    build_request_identity as build_request_identity,
)
from markdown_ingress.core.inflight_identity import (
    make_request_key as make_request_key,
)
from markdown_ingress.models import SafeDocument

logger = logging.getLogger(__name__)

# Maximum number of in-flight requests before LRU eviction kicks in.
_INFLIGHT_MAX_SIZE = 1000

# TTL (time-to-live) for in-flight entries in seconds.
_INFLIGHT_TTL_SECONDS = 900  # 15 minutes

_INFLIGHT_WAIT_TIMEOUT = 600  # seconds — prevents infinite hang if leader crashes

_INFLIGHT_COMPLETING_GRACE_MULTIPLIER = 2
_INFLIGHT_CLEANUP_INTERVAL_SECONDS = 30.0
_INFLIGHT_THREAD_JOIN_TIMEOUT_SECONDS = 5.0
_REQUEST_KEY_LOG_TRUNCATE_LENGTH = 16

_ALL_INFLIGHT_REGISTRIES: weakref.WeakSet[InFlightRegistry] = weakref.WeakSet()
_ALL_INFLIGHT_REGISTRIES_LOCK = threading.Lock()


class InFlightRegistry:
    """Isolated in-flight registry owned by one runtime/orchestrator.

    BUG FIX: Implements periodic cleanup to prevent unbounded growth from orphaned entries.
    Entries that exceed TTL without completion are automatically cleaned up.
    """

    def __init__(self) -> None:
        self._requests: OrderedDict[str, InFlightEntry] = OrderedDict()
        self._lock = threading.Lock()
        self._cleanup_control_lock = threading.Lock()
        self._last_orphan_cleanup_at: float = 0.0
        # BUG FIX: Background cleanup thread to remove orphaned entries
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

        BUG FIX: Prevents unbounded growth by periodically removing orphaned entries
        even when acquire() is not called frequently.
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
        thread.join(timeout=_INFLIGHT_THREAD_JOIN_TIMEOUT_SECONDS)
        with self._cleanup_control_lock:
            if self._cleanup_thread is thread and not thread.is_alive():
                self._cleanup_thread = None

    def _cleanup_orphaned_entries_locked(self) -> list[InFlightEntry]:
        """Pop orphaned entries from the registry. Caller must notify them after releasing _lock."""
        now = time.monotonic()
        keys_to_remove = []

        completing_grace = _INFLIGHT_TTL_SECONDS * _INFLIGHT_COMPLETING_GRACE_MULTIPLIER
        for key, entry in self._requests.items():
            age = now - entry.created_at
            if age > _INFLIGHT_TTL_SECONDS and not entry.completing:
                keys_to_remove.append(key)
            elif age > completing_grace and entry.completing and not entry.done:
                keys_to_remove.append(key)

        orphaned: list[InFlightEntry] = []
        for key in keys_to_remove:
            entry = self._requests.pop(key)
            logger.warning(
                "Cleaned up orphaned in-flight entry (key=%s, age=%.1fs, followers=%d)",
                key[:_REQUEST_KEY_LOG_TRUNCATE_LENGTH] + "...",
                now - entry.created_at,
                entry.followers,
            )
            orphaned.append(entry)

        return orphaned

    def _evict_lru_entries_locked(self) -> list[InFlightEntry]:
        """Evict done/inactive entries with no followers."""
        evicted: list[InFlightEntry] = []
        while len(self._requests) >= _INFLIGHT_MAX_SIZE:
            evictable_key = None
            evictable_entry = None
            for key, entry in self._requests.items():
                # Bug fix: never evict entries that still have followers waiting
                if (entry.done or not entry.leader_active) and entry.followers == 0:
                    evictable_key = key
                    evictable_entry = entry
                    break

            if evictable_key is None or evictable_entry is None:
                logger.warning(
                    "In-flight registry at max size (%d) with no evictable entries "
                    "(all have followers)",
                    _INFLIGHT_MAX_SIZE,
                )
                break

            key, entry = evictable_key, evictable_entry
            self._requests.pop(key)
            logger.warning(
                "Evicted in-flight entry due to max size (key=%s, followers=%d, age=%.1fs)",
                key[:_REQUEST_KEY_LOG_TRUNCATE_LENGTH] + "...",
                entry.followers,
                time.monotonic() - entry.created_at,
            )
            evicted.append(entry)
        return evicted

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
                        existing.followers += 1
                        self._requests.move_to_end(request_key)
                        result = existing
                        skip_new_leader = True
                    else:
                        # No document (error path) — drop and let caller retry
                        self._requests.pop(request_key)
                        # Fall through to new leader path
                else:
                    # Follower: increment under _lock (condition not needed for the write)
                    existing.followers += 1
                    self._requests.move_to_end(request_key)
                    result = existing
                    skip_new_leader = True

            if not skip_new_leader:
                to_notify.extend(self._evict_lru_entries_locked())
                if len(self._requests) >= _INFLIGHT_MAX_SIZE:
                    logger.warning(
                        "In-flight registry saturated; skipping dedup registration for key=%s",
                        request_key[:_REQUEST_KEY_LOG_TRUNCATE_LENGTH] + "...",
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
                            request_key[:_REQUEST_KEY_LOG_TRUNCATE_LENGTH] + "...",
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
                request_key[:_REQUEST_KEY_LOG_TRUNCATE_LENGTH] + "...",
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
                    entry.document.metadata["inflight_shared_count"] = shared_count
                if error is not None:
                    entry.error = copy_exception_for_transfer(error)
            except Exception as exc:  # noqa: BLE001 - release must notify waiters on copy failure
                entry.error = exc
            finally:
                entry.completing = True
                entry.done = True
                entry.condition.notify_all()

        # LOCK ORDERING FIX: Remove from registry AFTER releasing
        # entry.condition.  The previous code acquired _lock inside the
        # entry.condition block (condition -> _lock), while acquire() uses
        # (_lock -> condition), creating an ABBA deadlock.  By deferring
        # the removal to here, _lock and entry.condition are never held
        # simultaneously during the long release path.
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
    cached_copy.metadata["cache_hit"] = True
    cached_copy.metadata["inflight_deduplicated"] = False
    cached_copy.metadata["inflight_shared_count"] = 0
    return cached_copy
