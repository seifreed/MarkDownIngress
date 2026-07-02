"""Retention policy helpers for in-flight request registries."""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from collections.abc import Callable

from markdown_ingress.core.inflight_entry import InFlightEntry

# Maximum number of in-flight requests before LRU eviction kicks in.
INFLIGHT_MAX_SIZE = 1000

# TTL (time-to-live) for in-flight entries in seconds.
INFLIGHT_TTL_SECONDS = 900  # 15 minutes

INFLIGHT_COMPLETING_GRACE_MULTIPLIER = 2
REQUEST_KEY_LOG_TRUNCATE_LENGTH = 16


def format_request_key_for_log(request_key: str) -> str:
    return request_key[:REQUEST_KEY_LOG_TRUNCATE_LENGTH] + "..."


def cleanup_orphaned_entries(
    requests: OrderedDict[str, InFlightEntry],
    logger: logging.Logger,
    *,
    now: float | None = None,
) -> list[InFlightEntry]:
    """Pop orphaned entries from a locked registry."""
    cleanup_time = time.monotonic() if now is None else now
    keys_to_remove = []

    completing_grace = INFLIGHT_TTL_SECONDS * INFLIGHT_COMPLETING_GRACE_MULTIPLIER
    for key, entry in requests.items():
        age = cleanup_time - entry.created_at
        if age > INFLIGHT_TTL_SECONDS and not entry.completing:
            keys_to_remove.append(key)
        elif age > completing_grace and entry.completing and not entry.done:
            keys_to_remove.append(key)

    orphaned: list[InFlightEntry] = []
    for key in keys_to_remove:
        entry = requests.pop(key)
        logger.warning(
            "Cleaned up orphaned in-flight entry (key=%s, age=%.1fs, followers=%d)",
            format_request_key_for_log(key),
            cleanup_time - entry.created_at,
            entry.followers,
        )
        orphaned.append(entry)

    return orphaned


def evict_lru_entries(
    requests: OrderedDict[str, InFlightEntry],
    logger: logging.Logger,
    *,
    now_fn: Callable[[], float] = time.monotonic,
) -> list[InFlightEntry]:
    """Evict done or inactive entries with no followers from a locked registry."""
    evicted: list[InFlightEntry] = []
    while len(requests) >= INFLIGHT_MAX_SIZE:
        evictable_key = None
        evictable_entry = None
        for key, entry in requests.items():
            # Never evict entries that still have followers waiting.
            if (entry.done or not entry.leader_active) and entry.followers == 0:
                evictable_key = key
                evictable_entry = entry
                break

        if evictable_key is None or evictable_entry is None:
            logger.warning(
                "In-flight registry at max size (%d) with no evictable entries "
                "(all have followers)",
                INFLIGHT_MAX_SIZE,
            )
            break

        key, entry = evictable_key, evictable_entry
        requests.pop(key)
        logger.warning(
            "Evicted in-flight entry due to max size (key=%s, followers=%d, age=%.1fs)",
            format_request_key_for_log(key),
            entry.followers,
            now_fn() - entry.created_at,
        )
        evicted.append(entry)
    return evicted
