"""Batch processing dataclasses and URL processor for concurrent batch ingestion."""

from __future__ import annotations

import asyncio
import copy
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from markdown_ingress.application.exceptions import _copy_batch_exception
from markdown_ingress.application.screenshot_policy import screenshot_requires_fresh_capture
from markdown_ingress.config_models import IngestConfig
from markdown_ingress.core.cache import Cache
from markdown_ingress.core.ingest_stats import (
    bump_ingest_stat,
    record_mode_request,
    record_mode_result,
    record_mode_timing,
)
from markdown_ingress.core.metadata_keys import (
    CACHE_HIT,
    INFLIGHT_DEDUPLICATED,
    INFLIGHT_SHARED_COUNT,
    REQUESTED_MODE,
)
from markdown_ingress.core.policy import PolicyBlockedError
from markdown_ingress.models import SafeDocument
from markdown_ingress.reporting import persist_report_for_document
from markdown_ingress.shared_results import BatchErrorItem

if TYPE_CHECKING:
    from markdown_ingress.application.use_cases import BatchIngestUseCase

_logger = logging.getLogger(__name__)


@dataclass
class _PreparedBatchRequest:
    index: int
    url: str
    requested_mode: str
    resolved_config: IngestConfig
    request_key: str
    cache_backend: Cache | None
    cache_key: str | None


@dataclass
class _BatchInFlightRecord:
    future: asyncio.Future[tuple[SafeDocument, int]]
    followers: int = 0


@dataclass
class _CostBudget:
    limit: int | None
    used: int = 0

    def consume(self, units: int, reason: str) -> None:
        if self.limit is None:
            self.used += units
            return
        if self.used + units > self.limit:
            raise RuntimeError(
                f"Render cost budget exceeded while handling {reason}: "
                f"required {self.used + units}, budget {self.limit}"
            )
        self.used += units


@dataclass
class _BatchContext:
    """Shared mutable state for a single batch execution."""

    total: int
    documents: list[SafeDocument | None]
    errors: list[BatchErrorItem]
    semaphore: asyncio.Semaphore
    errors_lock: asyncio.Lock
    batch_inflight: dict[str, _BatchInFlightRecord]
    batch_inflight_lock: asyncio.Lock
    progress_lock: asyncio.Lock
    completed: int
    execution_strategy: Literal["isolated", "local"]
    on_progress: Callable[[int, int, str], None] | None
    batch_tracks_metrics: bool

    def set_document(self, index: int, document: SafeDocument) -> None:
        self.documents[index] = document

    async def append_error(self, item: BatchErrorItem) -> None:
        async with self.errors_lock:
            self.errors.append(item)

    async def report_progress(self, url: str) -> None:
        if self.on_progress is None:
            return
        async with self.progress_lock:
            self.completed += 1
            try:
                self.on_progress(self.completed, self.total, url)
            except Exception as exc:
                _logger.warning(
                    "Batch progress callback failed for %s (%d/%d): %s",
                    url,
                    self.completed,
                    self.total,
                    exc,
                    exc_info=True,
                )


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


class _BatchUrlProcessor:
    """Processes a single URL within a batch, encapsulating the leader/follower logic."""

    def __init__(self, ctx: _BatchContext, use_case: BatchIngestUseCase) -> None:
        self._ctx = ctx
        self._use_case = use_case

    async def _report_completion(self, url: str) -> None:
        await self._ctx.report_progress(url)

    @staticmethod
    def _uses_uncacheable_screenshot(prepared: _PreparedBatchRequest) -> bool:
        return screenshot_requires_fresh_capture(prepared.resolved_config)

    async def _try_cache(self, prepared: _PreparedBatchRequest) -> bool:
        """Check cache. Returns True and handles all completion if hit, False on miss."""
        ctx = self._ctx
        if prepared.cache_backend is None or prepared.cache_key is None:
            return False
        try:
            cached = prepared.cache_backend.get(prepared.cache_key)
        except Exception as exc:
            _logger.warning(
                "Batch cache lookup failed for %s; continuing without cache: %s",
                prepared.cache_key,
                exc,
                exc_info=True,
            )
            cached = None
        if cached is not None:
            try:
                cached_copy = self._use_case.ingest_use_case.orchestrator.clone_cached_document(
                    cached
                )
                bump_ingest_stat("cache_hits")
                cached_copy.metadata[REQUESTED_MODE] = prepared.requested_mode
                ctx.set_document(prepared.index, cached_copy)
                record_mode_result(prepared.requested_mode, success=True)
                await self._report_completion(prepared.url)
                return True
            except Exception as exc:
                _logger.warning(
                    "Failed to clone cached batch document for %s, cache entry may be corrupt: %s",
                    prepared.cache_key,
                    exc,
                    exc_info=True,
                )
                _purge_corrupt_cache_entry(prepared.cache_backend, prepared.cache_key)
        bump_ingest_stat("cache_misses")
        return False

    async def _register_inflight(
        self, prepared: _PreparedBatchRequest
    ) -> tuple[_BatchInFlightRecord, bool]:
        """Register in-flight tracking. Returns (record, is_leader)."""
        ctx = self._ctx
        async with ctx.batch_inflight_lock:
            record = ctx.batch_inflight.get(prepared.request_key)
            if record is None:
                record = _BatchInFlightRecord(future=asyncio.get_running_loop().create_future())
                ctx.batch_inflight[prepared.request_key] = record
                return record, True
            record.followers += 1
            return record, False

    async def _handle_follower(
        self, prepared: _PreparedBatchRequest, record: _BatchInFlightRecord
    ) -> bool:
        """Handle follower path: await leader's future and propagate result."""
        ctx = self._ctx
        # Release semaphore BEFORE awaiting — followers do no real work, so holding
        # a slot wastes concurrency and with max_concurrent=1 + duplicates can deadlock.
        ctx.semaphore.release()
        bump_ingest_stat("inflight_followers")
        try:
            shared_document, shared_count = await record.future
        except asyncio.CancelledError:
            # Clean up stale entry so future requests for the same key don't
            # find a cancelled future and deadlock.
            async with ctx.batch_inflight_lock:
                rec = ctx.batch_inflight.get(prepared.request_key)
                if rec is not None and rec.future.done():
                    ctx.batch_inflight.pop(prepared.request_key, None)
            raise
        except Exception as exc:
            import traceback

            await ctx.append_error(
                BatchErrorItem(
                    index=prepared.index,
                    url=prepared.url,
                    error=str(exc),
                    error_type=type(exc).__name__,
                    traceback=traceback.format_exc(),
                )
            )
            record_mode_result(prepared.requested_mode, success=False)
            async with ctx.batch_inflight_lock:
                if ctx.batch_inflight.get(prepared.request_key) is record:
                    ctx.batch_inflight.pop(prepared.request_key, None)
            await self._report_completion(prepared.url)
            return False
        shared = copy.deepcopy(shared_document)
        shared.metadata[INFLIGHT_DEDUPLICATED] = True
        shared.metadata[INFLIGHT_SHARED_COUNT] = shared_count
        shared.metadata.setdefault(CACHE_HIT, False)
        shared.metadata[REQUESTED_MODE] = prepared.requested_mode
        ctx.set_document(prepared.index, shared)
        record_mode_result(prepared.requested_mode, success=True)
        await self._report_completion(prepared.url)
        return True

    async def _execute_leader(
        self, prepared: _PreparedBatchRequest, record: _BatchInFlightRecord
    ) -> bool:
        """Handle leader path: execute ingestion, cache result, resolve future."""
        ctx = self._ctx
        if ctx.batch_tracks_metrics:
            bump_ingest_stat("leader_executions")
        if ctx.execution_strategy == "isolated":
            document = await self._use_case._execute_item_isolated(prepared)
        else:
            document = await self._use_case._execute_item_in_process(prepared)
        should_write_cache = not screenshot_requires_fresh_capture(prepared.resolved_config)
        if (
            should_write_cache
            and prepared.cache_backend is not None
            and prepared.cache_key is not None
        ):
            try:
                prepared.cache_backend.set(
                    prepared.cache_key,
                    document,
                    ttl=prepared.resolved_config.cache_ttl,
                )
            except Exception as exc:
                _logger.warning(
                    "Batch cache write failed for %s; continuing without cache: %s",
                    prepared.cache_key,
                    exc,
                    exc_info=True,
                )
        document.metadata[REQUESTED_MODE] = prepared.requested_mode
        async with ctx.batch_inflight_lock:
            shared_count = record.followers
            shared_document = copy.deepcopy(document)
            try:
                record.future.set_result((shared_document, shared_count))
            except asyncio.InvalidStateError:
                _logger.warning(
                    "Batch inflight future already done for %s (state: %s); "
                    "followers may have been cancelled",
                    prepared.request_key[:32],
                    getattr(record.future, "_state", "unknown"),
                )
            if ctx.batch_inflight.get(prepared.request_key) is record:
                ctx.batch_inflight.pop(prepared.request_key, None)
        document.metadata[INFLIGHT_DEDUPLICATED] = False
        document.metadata[INFLIGHT_SHARED_COUNT] = shared_count
        document.metadata.setdefault(CACHE_HIT, False)
        ctx.set_document(prepared.index, document)
        if ctx.batch_tracks_metrics:
            record_mode_result(prepared.requested_mode, success=True)
        await self._report_completion(prepared.url)
        return True

    async def _execute_direct(self, prepared: _PreparedBatchRequest) -> bool:
        """Execute an item without batch in-flight sharing."""
        ctx = self._ctx
        if ctx.batch_tracks_metrics:
            bump_ingest_stat("leader_executions")
        if ctx.execution_strategy == "isolated":
            document = await self._use_case._execute_item_isolated(prepared)
        else:
            document = await self._use_case._execute_item_in_process(prepared)
        document.metadata[REQUESTED_MODE] = prepared.requested_mode
        document.metadata[INFLIGHT_DEDUPLICATED] = False
        document.metadata[INFLIGHT_SHARED_COUNT] = 0
        document.metadata.setdefault(CACHE_HIT, False)
        ctx.set_document(prepared.index, document)
        if ctx.batch_tracks_metrics:
            record_mode_result(prepared.requested_mode, success=True)
        await self._report_completion(prepared.url)
        return True

    async def _handle_cancelled(
        self, record: _BatchInFlightRecord | None, prepared: _PreparedBatchRequest
    ) -> None:
        """Cancel in-flight future and clean up registry on task cancellation."""
        if record is None:
            return
        ctx = self._ctx
        async with ctx.batch_inflight_lock:
            if not record.future.done():
                record.future.cancel()
            if ctx.batch_inflight.get(prepared.request_key) is record:
                ctx.batch_inflight.pop(prepared.request_key, None)

    async def _handle_process_exception(
        self,
        prepared: _PreparedBatchRequest,
        record: _BatchInFlightRecord | None,
        exc: Exception,
    ) -> bool:
        """Resolve in-flight future with error, record error item, and report progress."""
        import traceback

        ctx = self._ctx
        if (
            isinstance(exc, PolicyBlockedError)
            and exc.document is not None
            and prepared.resolved_config.save_reports
        ):
            try:
                await asyncio.to_thread(
                    persist_report_for_document,
                    exc.document,
                    prepared.resolved_config.reports_dir,
                )
            except Exception as persist_exc:
                _logger.warning(
                    "Failed to persist security report for %s: %s",
                    prepared.url,
                    persist_exc,
                )
        if record is not None:
            async with ctx.batch_inflight_lock:
                try:
                    if record.followers > 0 and not record.future.done():
                        record.future.set_exception(_copy_batch_exception(exc))
                except asyncio.InvalidStateError:
                    _logger.warning(
                        "Batch inflight future already done when setting exception for %s",
                        prepared.request_key[:32],
                    )
                if ctx.batch_inflight.get(prepared.request_key) is record:
                    ctx.batch_inflight.pop(prepared.request_key, None)
        await ctx.append_error(
            BatchErrorItem(
                index=prepared.index,
                url=prepared.url,
                error=str(exc),
                error_type=type(exc).__name__,
                traceback=traceback.format_exc(),
            )
        )
        if ctx.batch_tracks_metrics:
            record_mode_result(prepared.requested_mode, success=False)
        await self._report_completion(prepared.url)
        return False

    async def process(self, prepared: _PreparedBatchRequest) -> bool:
        """Process a single URL: cache check, inflight dedup, then leader or follower path."""
        ctx = self._ctx
        started_at = time.perf_counter()
        if ctx.batch_tracks_metrics:
            bump_ingest_stat("requests_total")
            record_mode_request(prepared.requested_mode)
        record: _BatchInFlightRecord | None = None
        semaphore_held = False
        try:
            # Acquire semaphore for cache check + inflight detection.
            # This preserves sequential cache reuse (with max_concurrent=1
            # the second identical URL finds the first's result cached).
            await ctx.semaphore.acquire()
            semaphore_held = True
            if await self._try_cache(prepared):
                return True
            if self._uses_uncacheable_screenshot(prepared):
                return await self._execute_direct(prepared)
            record, is_leader = await self._register_inflight(prepared)
            if not is_leader:
                semaphore_held = False  # _handle_follower releases the semaphore
                return await self._handle_follower(prepared, record)
            return await self._execute_leader(prepared, record)
        except asyncio.CancelledError:
            await self._handle_cancelled(record, prepared)
            raise
        except Exception as exc:
            return await self._handle_process_exception(prepared, record, exc)
        finally:
            if semaphore_held:
                ctx.semaphore.release()
            if ctx.batch_tracks_metrics:
                record_mode_timing(
                    prepared.requested_mode,
                    (time.perf_counter() - started_at) * 1000.0,
                )
