"""Batch processing services at the application layer."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from importlib.util import find_spec
from typing import TypedDict, Unpack, cast

from markdown_ingress.application.async_tasks import gather_or_cancel
from markdown_ingress.application.batch_ingest_use_case import BatchIngestUseCase
from markdown_ingress.application.batch_state import PROGRESS_CALLBACK_ERRORS
from markdown_ingress.application.use_cases import IngestUseCase
from markdown_ingress.config_models import IngestConfig
from markdown_ingress.config_validation import Mode, collect_option_values
from markdown_ingress.models import SafeDocument
from markdown_ingress.runtime_helpers import (
    run_ingest_many_blocking as _shared_run_ingest_many_blocking,
)
from markdown_ingress.runtime_helpers import (
    validate_batch_max_concurrent,
)
from markdown_ingress.shared_results import BatchErrorItem, BatchResult

_logger = logging.getLogger(__name__)

RENDERER_AVAILABLE = find_spec("playwright") is not None
_CUSTOM_BATCH_ITEM_FAILURES = (Exception,)
run_ingest_many_blocking = _shared_run_ingest_many_blocking


def _validate_batch_max_concurrent(value: object) -> int:
    return validate_batch_max_concurrent(value)


@dataclass
class _CustomBatchState:
    total: int
    documents: list[SafeDocument | None]
    errors: list[BatchErrorItem]
    errors_lock: asyncio.Lock
    semaphore: asyncio.Semaphore
    progress_lock: asyncio.Lock
    completed: int = 0


class BatchProcessorOptions(TypedDict, total=False):
    mode: Mode
    strict: bool
    model: str
    timeout: float
    max_concurrent: int
    on_progress: Callable[[int, int, str], None] | None
    base_config: IngestConfig | None
    explicit_overrides: frozenset[str] | None


_BATCH_PROCESSOR_OPTION_NAMES = (
    "mode",
    "strict",
    "model",
    "timeout",
    "max_concurrent",
    "on_progress",
    "base_config",
    "explicit_overrides",
)


def _normalize_batch_processor_options(
    args: tuple[object, ...],
    options: BatchProcessorOptions,
) -> BatchProcessorOptions:
    return cast(
        BatchProcessorOptions,
        collect_option_values(
            "BatchProcessor()",
            _BATCH_PROCESSOR_OPTION_NAMES,
            args,
            options,
        ),
    )


def _ensure_safe_document(document: object) -> SafeDocument:
    if document is None:
        raise TypeError("process_url() returned None instead of SafeDocument")
    if not isinstance(document, SafeDocument):
        raise TypeError(
            "process_url() returned " f"{type(document).__name__} instead of SafeDocument"
        )
    return document


async def _record_custom_batch_error(
    state: _CustomBatchState, index: int, url: str, exc: Exception
) -> None:
    async with state.errors_lock:
        state.errors.append(BatchErrorItem.from_exception(index, url, exc))


class BatchProcessor:
    """Process multiple URLs in batch via the application use case layer."""

    def __init__(self, *args: object, **options: Unpack[BatchProcessorOptions]) -> None:
        parsed = _normalize_batch_processor_options(args, options)
        mode = parsed.get("mode", "auto")
        strict = parsed.get("strict", True)
        model = parsed.get("model", "gpt-4")
        timeout = parsed.get("timeout", 30.0)
        max_concurrent = parsed.get("max_concurrent", 5)
        on_progress = parsed.get("on_progress")
        base_config = parsed.get("base_config")
        explicit_overrides = parsed.get("explicit_overrides")

        self.mode = mode
        self.strict = strict
        self.model = model
        self.timeout = timeout
        self.max_concurrent = max_concurrent
        self.on_progress = on_progress
        self._base_config = base_config.clone() if base_config is not None else None
        self._explicit_overrides = frozenset(explicit_overrides or ())
        self._batch_use_case = BatchIngestUseCase(
            ingest_use_case=IngestUseCase(playwright_available=RENDERER_AVAILABLE)
        )

    def _build_config(self) -> IngestConfig:
        if self._base_config is None:
            return IngestConfig(
                mode=self.mode,
                strict=self.strict,
                model=self.model,
                timeout=self.timeout,
            )

        config = self._base_config.clone()
        explicit_keys = set(config.explicit_keys())
        if "mode" in self._explicit_overrides:
            config.mode = self.mode
        if "strict" in self._explicit_overrides:
            config.strict = self.strict
        if "model" in self._explicit_overrides:
            config.model = self.model
        if "timeout" in self._explicit_overrides:
            config.timeout = self.timeout
        explicit_keys.update(self._explicit_overrides)
        object.__setattr__(config, "_explicit_keys", frozenset(explicit_keys))
        return config.validate()

    async def process_url(self, url: str) -> SafeDocument:
        """Process a single URL asynchronously through the application layer.

        Cancellation interrupts the awaiter, but does not stop a background
        thread that has already started executing the sync ingest path.
        """
        return await asyncio.to_thread(
            self._batch_use_case.ingest_use_case.execute,
            url,
            self._build_config(),
        )

    def _uses_default_process_url(self) -> bool:
        bound = getattr(self.process_url, "__func__", None)
        owner = getattr(self.process_url, "__self__", None)
        return bound is BatchProcessor.process_url and owner is self

    async def process_batch_async(self, urls: list[str]) -> BatchResult:
        """Process multiple URLs concurrently while preserving input order."""
        max_concurrent = _validate_batch_max_concurrent(self.max_concurrent)
        if self._uses_default_process_url():
            return await self._batch_use_case.execute(
                urls,
                self._build_config,
                max_concurrent=max_concurrent,
                on_progress=self.on_progress,
            )

        return await self._process_custom_batch_async(urls, max_concurrent)

    async def _process_custom_batch_async(
        self, urls: list[str], max_concurrent: int
    ) -> BatchResult:
        total = len(urls)
        state = _CustomBatchState(
            total=total,
            documents=[None] * total,
            errors=[],
            errors_lock=asyncio.Lock(),
            semaphore=asyncio.Semaphore(max_concurrent),
            progress_lock=asyncio.Lock(),
        )
        results = await self._run_custom_batch_tasks(urls, state)
        successful = sum(1 for item in results if item)
        return BatchResult(
            total=total,
            successful=successful,
            failed=total - successful,
            documents=state.documents,
            errors=state.errors,
        )

    async def _run_custom_batch_tasks(
        self, urls: list[str], state: _CustomBatchState
    ) -> list[bool]:
        tasks = [
            asyncio.create_task(self._process_custom_batch_url(state, index, url))
            for index, url in enumerate(urls)
        ]
        return await gather_or_cancel(tasks)

    async def _process_custom_batch_url(
        self, state: _CustomBatchState, index: int, url: str
    ) -> bool:
        async with state.semaphore:
            try:
                document = await self.process_url(url)
                state.documents[index] = _ensure_safe_document(document)
                await self._report_custom_batch_completion(state, url)
            except asyncio.CancelledError:
                raise
            except _CUSTOM_BATCH_ITEM_FAILURES as exc:
                await _record_custom_batch_error(state, index, url, exc)
                await self._report_custom_batch_completion(state, url)
                return False
            else:
                return True

    async def _report_custom_batch_completion(self, state: _CustomBatchState, url: str) -> None:
        if self.on_progress is None:
            return
        async with state.progress_lock:
            state.completed += 1
            try:
                self.on_progress(state.completed, state.total, url)
            except PROGRESS_CALLBACK_ERRORS as exc:
                _logger.warning(
                    "Batch progress callback failed for %s (%d/%d): %s",
                    url,
                    state.completed,
                    state.total,
                    exc,
                    exc_info=True,
                )

    def process_batch(self, urls: list[str]) -> BatchResult:
        """Synchronous wrapper for batch processing."""
        return run_ingest_many_blocking(lambda: self.process_batch_async(urls))
