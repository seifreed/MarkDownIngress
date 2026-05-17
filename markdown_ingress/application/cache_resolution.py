"""Cache and in-flight resolution helpers for ingestion use cases."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import cast

from markdown_ingress.config_models import DomainPolicy, IngestConfig
from markdown_ingress.core.cache import Cache
from markdown_ingress.core.inflight import build_request_identity
from markdown_ingress.core.ingest_stats import (
    bump_ingest_stat,
    record_mode_result,
)
from markdown_ingress.core.interfaces import IIngestOrchestrator
from markdown_ingress.core.metadata_keys import (
    CACHE_HIT,
    INFLIGHT_DEDUPLICATED,
    REQUESTED_MODE,
)
from markdown_ingress.models import SafeDocument

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CacheResolutionRequest:
    url: str
    resolved_config: IngestConfig
    matched_domain_policy: DomainPolicy | None
    cache_backend: Cache | None
    request_key: str
    requested_mode: str


def _purge_corrupt_cache_entry(cache_backend: Cache, cache_key: str) -> None:
    """Best-effort removal of a corrupt cache value before recomputing."""
    try:
        cache_backend.delete(cache_key)
    except Exception as exc:
        _logger.warning(
            "Failed to delete corrupt cache entry for %s; continuing as cache miss: %s",
            cache_key,
            exc,
            exc_info=True,
        )


class _CacheResolutionHelper:
    """Handles cache lookup and in-flight deduplication for ingestion requests."""

    def __init__(self, orchestrator: IIngestOrchestrator) -> None:
        self._orchestrator = orchestrator

    @staticmethod
    def make_cache_key(
        url: str,
        resolved_config: IngestConfig,
        request_identity: dict[str, object],
        cache_backend: Cache | None,
    ) -> str | None:
        if cache_backend is None:
            return None
        return Cache.make_key(
            url=url,
            mode=resolved_config.mode,
            strict=resolved_config.strict,
            extra=request_identity,
        )

    def try_cache_hit(
        self,
        cache_backend: Cache | None,
        cache_key: str | None,
        requested_mode: str,
    ) -> SafeDocument | None:
        if cache_backend is None or cache_key is None:
            return None
        try:
            cached = cache_backend.get(cache_key)
        except Exception as exc:
            _logger.warning(
                "Cache lookup failed for %s; continuing without cache: %s",
                cache_key,
                exc,
                exc_info=True,
            )
            bump_ingest_stat("cache_misses")
            return None
        if cached is None:
            bump_ingest_stat("cache_misses")
            return None
        try:
            cached_copy = cast(SafeDocument, self._orchestrator.clone_cached_document(cached))
            bump_ingest_stat("cache_hits")
            cached_copy.metadata[REQUESTED_MODE] = requested_mode
            record_mode_result(requested_mode, success=True)
            return cached_copy
        except Exception as exc:
            _logger.warning(
                "Failed to clone cached document for %s, cache entry may be corrupt: %s",
                cache_key,
                exc,
                exc_info=True,
            )
            _purge_corrupt_cache_entry(cache_backend, cache_key)
            bump_ingest_stat("cache_misses")
            return None

    def try_inflight_follower(
        self,
        request_key: str,
        requested_mode: str,
    ) -> SafeDocument | None:
        in_flight = self._orchestrator.acquire_inflight(request_key)
        if in_flight is None:
            return None
        bump_ingest_stat("inflight_followers")
        shared = cast(SafeDocument, self._orchestrator.await_inflight(in_flight, request_key))
        shared.metadata[INFLIGHT_DEDUPLICATED] = True
        shared.metadata.setdefault(CACHE_HIT, False)
        shared.metadata[REQUESTED_MODE] = requested_mode
        record_mode_result(requested_mode, success=True)
        return shared

    def resolve(
        self,
        request: CacheResolutionRequest,
    ) -> tuple[SafeDocument | None, str | None]:
        """Return a cached or in-flight-shared document and the cache key, or (None, key)."""
        request_identity = build_request_identity(
            request.url,
            request.resolved_config,
            request.matched_domain_policy,
        )
        cache_key = self.make_cache_key(
            request.url,
            request.resolved_config,
            request_identity,
            request.cache_backend,
        )
        hit = self.try_cache_hit(request.cache_backend, cache_key, request.requested_mode)
        if hit is not None:
            return hit, cache_key
        shared = self.try_inflight_follower(request.request_key, request.requested_mode)
        return shared, cache_key
