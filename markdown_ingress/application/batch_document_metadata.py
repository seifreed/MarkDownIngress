"""Document metadata helpers for batch ingestion."""

from __future__ import annotations

from typing import TYPE_CHECKING

from markdown_ingress.core.metadata_keys import (
    CACHE_HIT,
    INFLIGHT_DEDUPLICATED,
    INFLIGHT_SHARED_COUNT,
    REQUESTED_MODE,
)

if TYPE_CHECKING:
    from markdown_ingress.models import SafeDocument


def mark_batch_document(
    document: SafeDocument,
    *,
    requested_mode: str,
    inflight_deduplicated: bool,
    shared_count: int,
) -> None:
    document.metadata[REQUESTED_MODE] = requested_mode
    document.metadata[INFLIGHT_DEDUPLICATED] = inflight_deduplicated
    document.metadata[INFLIGHT_SHARED_COUNT] = shared_count
    document.metadata.setdefault(CACHE_HIT, False)
