"""Batch processing dataclasses and URL processor for concurrent batch ingestion."""

from __future__ import annotations

import asyncio
import copy
import logging
import time
from typing import TYPE_CHECKING

from markdown_ingress.application.batch_inflight import (
    cancel_batch_inflight,
    publish_batch_inflight_exception,
    publish_batch_inflight_result,
    register_batch_inflight,
    remove_finished_batch_inflight,
)
from markdown_ingress.application.batch_state import (
    _BatchContext,
    _BatchInFlightRecord,
    _CostBudget,
    _PreparedBatchRequest,
)
from markdown_ingress.application.cache_resolution import _purge_corrupt_cache_entry
from markdown_ingress.application.screenshot_policy import screenshot_requires_fresh_capture
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
from markdown_ingress.reporting import persist_report_for_document
from markdown_ingress.shared_results import BatchErrorItem

if TYPE_CHECKING:
    from markdown_ingress.application.batch_ingest_use_case import BatchIngestUseCase

_logger = logging.getLogger(__name__)

__all__ = [
    "_BatchContext",
    "_BatchInFlightRecord",
    "_BatchUrlProcessor",
    "_CostBudget",
    "_PreparedBatchRequest",
]


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

    def _record_shortcut_request_for_local_strategy(
        self,
        prepared: _PreparedBatchRequest,
        *,
        success: bool,
        started_at: float | None = None,
    ) -> None:
        if self._ctx.batch_tracks_metrics:
            return
        bump_ingest_stat("requests_total")
        record_mode_request(prepared.requested_mode)
        record_mode_result(prepared.requested_mode, success=success)
        if started_at is not None:
            record_mode_timing(
                prepared.requested_mode,
                (time.perf_counter() - started_at) * 1000.0,
            )

    async def _try_cache(self, prepared: _PreparedBatchRequest, started_at: float) -> bool:
        """Check cache. Returns True and handles all completion if hit, False on miss."""
        ctx = self._ctx
        if prepared.cache_backend is None or prepared.cache_key is None:
            return False
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
        if cached is not None:
            try:
                cached_copy = self._use_case.ingest_use_case.orchestrator.clone_cached_document(
                    cached
                )
                bump_ingest_stat("cache_hits")
                cached_copy.metadata[REQUESTED_MODE] = prepared.requested_mode
                ctx.set_document(prepared.index, cached_copy)
                if ctx.batch_tracks_metrics:
                    record_mode_result(prepared.requested_mode, success=True)
                else:
                    self._record_shortcut_request_for_local_strategy(
                        prepared,
                        success=True,
                        started_at=started_at,
                    )
                await self._report_completion(prepared.url)
            except Exception as exc:  # noqa: BLE001 - corrupt cached values are purged
                _logger.warning(
                    "Failed to clone cached batch document for %s, cache entry may be corrupt: %s",
                    prepared.cache_key,
                    exc,
                    exc_info=True,
                )
                _purge_corrupt_cache_entry(prepared.cache_backend, prepared.cache_key)
            else:
                return True
        bump_ingest_stat("cache_misses")
        return False

    async def _register_inflight(
        self, prepared: _PreparedBatchRequest
    ) -> tuple[_BatchInFlightRecord, bool]:
        """Register in-flight tracking. Returns (record, is_leader)."""
        return await register_batch_inflight(self._ctx, prepared.request_key)

    async def _handle_follower(
        self,
        prepared: _PreparedBatchRequest,
        record: _BatchInFlightRecord,
        started_at: float,
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
            await remove_finished_batch_inflight(ctx, prepared.request_key, record)
            raise
        except Exception as exc:  # noqa: BLE001 - follower records leader failure as item
            await ctx.append_error(BatchErrorItem.from_exception(prepared.index, prepared.url, exc))
            if ctx.batch_tracks_metrics:
                record_mode_result(prepared.requested_mode, success=False)
            else:
                self._record_shortcut_request_for_local_strategy(
                    prepared,
                    success=False,
                    started_at=started_at,
                )
            await remove_finished_batch_inflight(ctx, prepared.request_key, record)
            await self._report_completion(prepared.url)
            return False
        shared = copy.deepcopy(shared_document)
        shared.metadata[INFLIGHT_DEDUPLICATED] = True
        shared.metadata[INFLIGHT_SHARED_COUNT] = shared_count
        shared.metadata.setdefault(CACHE_HIT, False)
        shared.metadata[REQUESTED_MODE] = prepared.requested_mode
        ctx.set_document(prepared.index, shared)
        if ctx.batch_tracks_metrics:
            record_mode_result(prepared.requested_mode, success=True)
        else:
            self._record_shortcut_request_for_local_strategy(
                prepared,
                success=True,
                started_at=started_at,
            )
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
            except Exception as exc:  # noqa: BLE001 - cache writes are optional side effects
                _logger.warning(
                    "Batch cache write failed for %s; continuing without cache: %s",
                    prepared.cache_key,
                    exc,
                    exc_info=True,
                )
        document.metadata[REQUESTED_MODE] = prepared.requested_mode
        shared_count = await publish_batch_inflight_result(
            ctx,
            prepared.request_key,
            record,
            document,
        )
        document.metadata[INFLIGHT_DEDUPLICATED] = False
        document.metadata[INFLIGHT_SHARED_COUNT] = shared_count
        document.metadata.setdefault(CACHE_HIT, False)
        ctx.set_document(prepared.index, document)
        if ctx.batch_tracks_metrics:
            record_mode_result(prepared.requested_mode, success=True)
        await self._report_completion(prepared.url)
        return True

    async def _execute_direct(
        self, prepared: _PreparedBatchRequest, started_at: float | None = None
    ) -> bool:
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
        else:
            self._record_shortcut_request_for_local_strategy(
                prepared, success=True, started_at=started_at
            )
        await self._report_completion(prepared.url)
        return True

    async def _handle_cancelled(
        self, record: _BatchInFlightRecord | None, prepared: _PreparedBatchRequest
    ) -> None:
        """Cancel in-flight future and clean up registry on task cancellation."""
        if record is None:
            return
        await cancel_batch_inflight(self._ctx, prepared.request_key, record)

    async def _handle_process_exception(
        self,
        prepared: _PreparedBatchRequest,
        record: _BatchInFlightRecord | None,
        exc: Exception,
    ) -> bool:
        """Resolve in-flight future with error, record error item, and report progress."""
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
            except Exception as persist_exc:  # noqa: BLE001 - report persistence is optional
                _logger.warning(
                    "Failed to persist security report for %s: %s",
                    prepared.url,
                    persist_exc,
                )
        if record is not None:
            await publish_batch_inflight_exception(ctx, prepared.request_key, record, exc)
        await ctx.append_error(BatchErrorItem.from_exception(prepared.index, prepared.url, exc))
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
        cache_hit = False
        try:
            # Acquire semaphore for cache check + inflight detection.
            # This preserves sequential cache reuse (with max_concurrent=1
            # the second identical URL finds the first's result cached).
            await ctx.semaphore.acquire()
            semaphore_held = True
            if await self._try_cache(prepared, started_at):
                cache_hit = True
                return True
            if self._uses_uncacheable_screenshot(prepared):
                return await self._execute_direct(prepared, started_at)
            record, is_leader = await self._register_inflight(prepared)
            if not is_leader:
                semaphore_held = False  # _handle_follower releases the semaphore
                return await self._handle_follower(prepared, record, started_at)
            return await self._execute_leader(prepared, record)
        except asyncio.CancelledError:
            await self._handle_cancelled(record, prepared)
            raise
        except Exception as exc:  # noqa: BLE001 - per-item batch failures are collected
            return await self._handle_process_exception(prepared, record, exc)
        finally:
            if semaphore_held:
                ctx.semaphore.release()
            if ctx.batch_tracks_metrics and not cache_hit:
                record_mode_timing(
                    prepared.requested_mode,
                    (time.perf_counter() - started_at) * 1000.0,
                )
