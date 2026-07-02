"""Batch processing dataclasses and URL processor for concurrent batch ingestion."""

from __future__ import annotations

import asyncio
import copy
import logging
import time
from typing import TYPE_CHECKING

from markdown_ingress.application.batch_cache import read_batch_cached_document, write_batch_cache
from markdown_ingress.application.batch_document_metadata import mark_batch_document
from markdown_ingress.application.batch_inflight import (
    cancel_batch_inflight,
    publish_batch_inflight_exception,
    publish_batch_inflight_result,
    register_batch_inflight,
    remove_finished_batch_inflight,
)
from markdown_ingress.application.batch_state import (
    BatchContext,
    BatchInFlightRecord,
    CostBudget,
    PreparedBatchRequest,
)
from markdown_ingress.application.screenshot_policy import screenshot_requires_fresh_capture
from markdown_ingress.core.ingest_stats import (
    bump_ingest_stat,
    record_mode_request,
    record_mode_result,
    record_mode_timing,
)
from markdown_ingress.core.policy import PolicyBlockedError
from markdown_ingress.reporting import persist_report_for_document
from markdown_ingress.shared_results import BatchErrorItem

if TYPE_CHECKING:
    from markdown_ingress.application.batch_ingest_use_case import BatchIngestUseCase
    from markdown_ingress.models import SafeDocument

_logger = logging.getLogger(__name__)

__all__ = [
    "BatchContext",
    "BatchInFlightRecord",
    "_BatchUrlProcessor",
    "CostBudget",
    "PreparedBatchRequest",
]


class _BatchUrlProcessor:
    """Processes a single URL within a batch, encapsulating the leader/follower logic."""

    def __init__(self, ctx: BatchContext, use_case: BatchIngestUseCase) -> None:
        self._ctx = ctx
        self._use_case = use_case

    async def _report_completion(self, url: str) -> None:
        await self._ctx.report_progress(url)

    @staticmethod
    def _uses_uncacheable_screenshot(prepared: PreparedBatchRequest) -> bool:
        return screenshot_requires_fresh_capture(prepared.resolved_config)

    def _record_shortcut_request_for_local_strategy(
        self,
        prepared: PreparedBatchRequest,
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

    def _record_item_result(
        self,
        prepared: PreparedBatchRequest,
        *,
        success: bool,
        started_at: float | None = None,
    ) -> None:
        if self._ctx.batch_tracks_metrics:
            record_mode_result(prepared.requested_mode, success=success)
            return
        self._record_shortcut_request_for_local_strategy(
            prepared,
            success=success,
            started_at=started_at,
        )

    def _record_process_start(self, prepared: PreparedBatchRequest) -> None:
        if not self._ctx.batch_tracks_metrics:
            return
        bump_ingest_stat("requests_total")
        record_mode_request(prepared.requested_mode)

    def _record_process_timing(
        self,
        prepared: PreparedBatchRequest,
        *,
        started_at: float,
        cache_hit: bool,
    ) -> None:
        if not self._ctx.batch_tracks_metrics or cache_hit:
            return
        record_mode_timing(
            prepared.requested_mode,
            (time.perf_counter() - started_at) * 1000.0,
        )

    async def _try_cache(self, prepared: PreparedBatchRequest, started_at: float) -> bool:
        """Check cache. Returns True and handles all completion if hit, False on miss."""
        ctx = self._ctx
        cached_copy = read_batch_cached_document(
            prepared,
            self._use_case.ingest_use_case.orchestrator.clone_cached_document,
        )
        if cached_copy is None:
            return False
        ctx.set_document(prepared.index, cached_copy)
        self._record_item_result(prepared, success=True, started_at=started_at)
        await self._report_completion(prepared.url)
        return True

    async def _register_inflight(
        self, prepared: PreparedBatchRequest
    ) -> tuple[BatchInFlightRecord, bool]:
        """Register in-flight tracking. Returns (record, is_leader)."""
        return await register_batch_inflight(self._ctx, prepared.request_key)

    async def _append_error_for_exception(
        self, prepared: PreparedBatchRequest, exc: Exception
    ) -> None:
        await self._ctx.append_error(
            BatchErrorItem.from_exception(prepared.index, prepared.url, exc)
        )

    async def _handle_follower_exception(
        self,
        prepared: PreparedBatchRequest,
        record: BatchInFlightRecord,
        started_at: float,
        exc: Exception,
    ) -> bool:
        await self._append_error_for_exception(prepared, exc)
        self._record_item_result(prepared, success=False, started_at=started_at)
        await remove_finished_batch_inflight(self._ctx, prepared.request_key, record)
        await self._report_completion(prepared.url)
        return False

    async def _handle_follower(
        self,
        prepared: PreparedBatchRequest,
        record: BatchInFlightRecord,
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
            return await self._handle_follower_exception(prepared, record, started_at, exc)
        shared = copy.deepcopy(shared_document)
        mark_batch_document(
            shared,
            requested_mode=prepared.requested_mode,
            inflight_deduplicated=True,
            shared_count=shared_count,
        )
        ctx.set_document(prepared.index, shared)
        self._record_item_result(prepared, success=True, started_at=started_at)
        await self._report_completion(prepared.url)
        return True

    async def _execute_item(self, prepared: PreparedBatchRequest) -> SafeDocument:
        """Run a single item via the configured strategy, recording the leader metric."""
        if self._ctx.batch_tracks_metrics:
            bump_ingest_stat("leader_executions")
        if self._ctx.execution_strategy == "isolated":
            return await self._use_case._execute_item_isolated(prepared)
        return await self._use_case._execute_item_in_process(prepared)

    async def _execute_leader(
        self, prepared: PreparedBatchRequest, record: BatchInFlightRecord
    ) -> bool:
        """Handle leader path: execute ingestion, cache result, resolve future."""
        ctx = self._ctx
        document = await self._execute_item(prepared)
        write_batch_cache(prepared, document)
        shared_count = await publish_batch_inflight_result(
            ctx,
            prepared.request_key,
            record,
            document,
        )
        mark_batch_document(
            document,
            requested_mode=prepared.requested_mode,
            inflight_deduplicated=False,
            shared_count=shared_count,
        )
        ctx.set_document(prepared.index, document)
        if ctx.batch_tracks_metrics:
            record_mode_result(prepared.requested_mode, success=True)
        await self._report_completion(prepared.url)
        return True

    async def _execute_direct(
        self, prepared: PreparedBatchRequest, started_at: float | None = None
    ) -> bool:
        """Execute an item without batch in-flight sharing."""
        ctx = self._ctx
        document = await self._execute_item(prepared)
        mark_batch_document(
            document,
            requested_mode=prepared.requested_mode,
            inflight_deduplicated=False,
            shared_count=0,
        )
        ctx.set_document(prepared.index, document)
        self._record_item_result(prepared, success=True, started_at=started_at)
        await self._report_completion(prepared.url)
        return True

    async def _handle_cancelled(
        self, record: BatchInFlightRecord | None, prepared: PreparedBatchRequest
    ) -> None:
        """Cancel in-flight future and clean up registry on task cancellation."""
        if record is None:
            return
        await cancel_batch_inflight(self._ctx, prepared.request_key, record)

    async def _persist_blocked_policy_report(
        self,
        prepared: PreparedBatchRequest,
        exc: Exception,
    ) -> None:
        if not (
            isinstance(exc, PolicyBlockedError)
            and exc.document is not None
            and prepared.resolved_config.save_reports
        ):
            return
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

    async def _handle_process_exception(
        self,
        prepared: PreparedBatchRequest,
        record: BatchInFlightRecord | None,
        exc: Exception,
    ) -> bool:
        """Resolve in-flight future with error, record error item, and report progress."""
        ctx = self._ctx
        await self._persist_blocked_policy_report(prepared, exc)
        if record is not None:
            await publish_batch_inflight_exception(ctx, prepared.request_key, record, exc)
        await self._append_error_for_exception(prepared, exc)
        if ctx.batch_tracks_metrics:
            record_mode_result(prepared.requested_mode, success=False)
        await self._report_completion(prepared.url)
        return False

    async def process(self, prepared: PreparedBatchRequest) -> bool:
        """Process a single URL: cache check, inflight dedup, then leader or follower path."""
        ctx = self._ctx
        started_at = time.perf_counter()
        self._record_process_start(prepared)
        record: BatchInFlightRecord | None = None
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
            self._record_process_timing(
                prepared,
                started_at=started_at,
                cache_hit=cache_hit,
            )
