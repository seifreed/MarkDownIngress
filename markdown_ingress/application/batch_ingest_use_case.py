"""Batch ingestion use case coordinating concurrent URL execution."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from typing import Protocol, cast

from markdown_ingress.application.async_tasks import gather_or_cancel
from markdown_ingress.application.batch_processor import BatchUrlProcessor
from markdown_ingress.application.batch_state import (
    BatchContext,
    PreparedBatchRequest,
)
from markdown_ingress.application.fetcher_manager import (
    _ensure_fetcher_user_agent,
    _select_stable_fetcher_user_agent,
)
from markdown_ingress.application.screenshot_policy import screenshot_requires_fresh_capture
from markdown_ingress.application.subprocess_runner import (
    _batch_process_context,
    _execute_batch_ingest_in_subprocess,
    _poll_subprocess_queue,
    _select_execution_strategy,
    _terminate_batch_process,
)
from markdown_ingress.config_models import IngestConfig
from markdown_ingress.core.cache import Cache
from markdown_ingress.core.inflight import build_request_identity
from markdown_ingress.core.interfaces import IIngestOrchestrator
from markdown_ingress.models import SafeDocument
from markdown_ingress.shared_results import BatchErrorItem, BatchResult

_logger = logging.getLogger(__name__)


class _IngestUseCaseLike(Protocol):
    orchestrator: IIngestOrchestrator
    playwright_available: bool

    def execute(self, url: str, config: IngestConfig) -> SafeDocument: ...

    def uses_default_runtime_dependencies(self) -> bool: ...


class BatchIngestUseCase:
    """Concurrent batch ingestion on top of the single-item ingestion use case."""

    def __init__(self, ingest_use_case: _IngestUseCaseLike | None = None) -> None:
        if ingest_use_case is None:
            from markdown_ingress.application.use_cases import IngestUseCase

            ingest_use_case = IngestUseCase()
        self.ingest_use_case = ingest_use_case
        self._auto_fetcher_user_agent = getattr(
            self.ingest_use_case,
            "_auto_fetcher_user_agent",
            _select_stable_fetcher_user_agent(),
        )

    def _prepare_request(
        self,
        index: int,
        url: str,
        config: IngestConfig,
    ) -> PreparedBatchRequest:
        resolved_config, matched_domain_policy = config.resolve_for_url(url)
        _ensure_fetcher_user_agent(
            url,
            resolved_config,
            matched_domain_policy,
            default_user_agent=self._auto_fetcher_user_agent,
        )
        cache_backend = (
            None
            if screenshot_requires_fresh_capture(resolved_config)
            else cast(Cache | None, config.cache)
        )
        cache_key = None
        request_identity = build_request_identity(url, resolved_config, matched_domain_policy)
        request_key = self.ingest_use_case.orchestrator.make_request_key(
            url,
            resolved_config,
            matched_domain_policy,
        )
        if cache_backend is not None:
            cache_key = Cache.make_key(
                url=url,
                mode=resolved_config.mode,
                strict=resolved_config.strict,
                extra=request_identity,
            )
        return PreparedBatchRequest(
            index=index,
            url=url,
            requested_mode=config.mode,
            resolved_config=resolved_config,
            request_key=request_key,
            cache_backend=cache_backend,
            cache_key=cache_key,
        )

    async def _execute_item_isolated(self, prepared: PreparedBatchRequest) -> SafeDocument:
        ctx = _batch_process_context()
        if ctx is None:
            raise RuntimeError("Batch subprocess isolation requires an importable __main__ module")
        # Use Queue instead of Pipe: Queue handles arbitrarily large objects;
        # Pipe has a ~64 KB buffer that blocks the child on large SafeDocuments.
        queue = ctx.Queue()
        worker_config = prepared.resolved_config.clone()
        worker_config.cache = None
        process = ctx.Process(
            target=_execute_batch_ingest_in_subprocess,
            args=(
                prepared.url,
                worker_config,
                self.ingest_use_case.playwright_available,
                queue,
            ),
            daemon=True,
        )
        try:
            process.start()
            return await _poll_subprocess_queue(process, queue, prepared.url)
        except asyncio.CancelledError:
            _terminate_batch_process(process)
            raise
        finally:
            if process.is_alive():
                _terminate_batch_process(process)
            queue.close()
            queue.join_thread()

    async def _execute_item_in_process(self, prepared: PreparedBatchRequest) -> SafeDocument:
        """Execute a batch item locally while preserving injected dependencies."""
        worker_config = prepared.resolved_config.clone()
        worker_config.cache = None
        return await asyncio.to_thread(
            self.ingest_use_case.execute,
            prepared.url,
            worker_config,
        )

    async def execute(
        self,
        urls: Sequence[str],
        config_builder: Callable[[], IngestConfig],
        *,
        max_concurrent: int = 5,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> BatchResult:
        if isinstance(max_concurrent, bool) or not isinstance(max_concurrent, int):
            raise ValueError(f"max_concurrent must be an int, got {type(max_concurrent).__name__}")
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")

        url_list = list(urls)
        total = len(url_list)
        documents: list[SafeDocument | None] = [None] * total
        errors: list[BatchErrorItem] = []
        execution_strategy, fallback_reason = _select_execution_strategy(self.ingest_use_case)
        if execution_strategy == "local" and fallback_reason is not None:
            _logger.info("Batch falling back to in-process execution: %s.", fallback_reason)
        prepared_requests = [
            self._prepare_request(index, url, config_builder())
            for index, url in enumerate(url_list)
        ]
        ctx = BatchContext(
            total=total,
            documents=documents,
            errors=errors,
            semaphore=asyncio.Semaphore(max_concurrent),
            errors_lock=asyncio.Lock(),
            batch_inflight={},
            batch_inflight_lock=asyncio.Lock(),
            progress_lock=asyncio.Lock(),
            completed=0,
            execution_strategy=execution_strategy,
            on_progress=on_progress,
            batch_tracks_metrics=execution_strategy != "local",
        )
        processor = BatchUrlProcessor(ctx, self)
        tasks = [asyncio.create_task(processor.process(prepared)) for prepared in prepared_requests]
        results = await gather_or_cancel(tasks)
        successful = sum(1 for r in results if r)
        failed = total - successful
        return BatchResult(
            total=total,
            successful=successful,
            failed=failed,
            documents=documents,
            errors=errors,
        )
