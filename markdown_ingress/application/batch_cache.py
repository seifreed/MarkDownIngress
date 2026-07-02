"""Cache helpers for batch ingestion."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from markdown_ingress.application.cache_resolution import _purge_corrupt_cache_entry
from markdown_ingress.application.screenshot_policy import screenshot_requires_fresh_capture
from markdown_ingress.core.ingest_stats import bump_ingest_stat
from markdown_ingress.core.metadata_keys import REQUESTED_MODE

if TYPE_CHECKING:
    from markdown_ingress.application.batch_state import _PreparedBatchRequest
    from markdown_ingress.models import SafeDocument

_logger = logging.getLogger(__name__)


def read_batch_cached_document(
    prepared: _PreparedBatchRequest,
    clone_cached_document: Callable[[object], SafeDocument],
) -> SafeDocument | None:
    if prepared.cache_backend is None or prepared.cache_key is None:
        return None
    try:
        cached = prepared.cache_backend.get(prepared.cache_key)
    except Exception as exc:  # noqa: BLE001 - cache backends fail open as misses
        _logger.warning(
            "Batch cache lookup failed for %s; continuing without cache: %s",
            prepared.cache_key,
            exc,
            exc_info=True,
        )
        cached = None
    if cached is None:
        bump_ingest_stat("cache_misses")
        return None
    try:
        cached_copy = clone_cached_document(cached)
        bump_ingest_stat("cache_hits")
        cached_copy.metadata[REQUESTED_MODE] = prepared.requested_mode
        return cached_copy
    except Exception as exc:  # noqa: BLE001 - corrupt cached values are purged
        _logger.warning(
            "Failed to clone cached batch document for %s, cache entry may be corrupt: %s",
            prepared.cache_key,
            exc,
            exc_info=True,
        )
        _purge_corrupt_cache_entry(prepared.cache_backend, prepared.cache_key)
        bump_ingest_stat("cache_misses")
        return None


def write_batch_cache(prepared: _PreparedBatchRequest, document: SafeDocument) -> None:
    if screenshot_requires_fresh_capture(prepared.resolved_config):
        return
    if prepared.cache_backend is None or prepared.cache_key is None:
        return
    try:
        prepared.cache_backend.set(
            prepared.cache_key,
            document,
            ttl=prepared.resolved_config.cache_ttl,
        )
    except Exception as exc:  # noqa: BLE001 - cache writes are optional side effects
        _logger.warning(
            "Batch cache write failed for %s; continuing without cache: %s",
            prepared.cache_key,
            exc,
            exc_info=True,
        )
