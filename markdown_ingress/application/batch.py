"""Batch processing services at the application layer."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Literal

from markdown_ingress.application.use_cases import BatchIngestUseCase, IngestUseCase
from markdown_ingress.config_models import IngestConfig
from markdown_ingress.models import SafeDocument
from markdown_ingress.shared_results import BatchErrorItem, BatchResult

_logger = logging.getLogger(__name__)

try:
    from markdown_ingress.adapters.rendering.playwright_renderer import PLAYWRIGHT_INSTALLED

    RENDERER_AVAILABLE = PLAYWRIGHT_INSTALLED
except ImportError:
    RENDERER_AVAILABLE = False


def _validate_max_concurrent(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"max_concurrent must be an int, got {type(value).__name__}")
    if value < 1:
        raise ValueError("max_concurrent must be >= 1")
    return value


class BatchProcessor:
    """Process multiple URLs in batch via the application use case layer."""

    def __init__(
        self,
        mode: Literal["fast", "render", "auto"] = "auto",
        strict: bool = True,
        model: str = "gpt-4",
        timeout: float = 30.0,
        max_concurrent: int = 5,
        on_progress: Callable[[int, int, str], None] | None = None,
        base_config: IngestConfig | None = None,
        explicit_overrides: frozenset[str] | None = None,
    ):
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
        max_concurrent = _validate_max_concurrent(self.max_concurrent)
        if self._uses_default_process_url():
            return await self._batch_use_case.execute(
                urls,
                self._build_config,
                max_concurrent=max_concurrent,
                on_progress=self.on_progress,
            )

        total = len(urls)
        documents: list[SafeDocument | None] = [None] * total
        errors: list[BatchErrorItem] = []
        errors_lock = asyncio.Lock()
        semaphore = asyncio.Semaphore(max_concurrent)
        progress_lock = asyncio.Lock()
        completed = 0

        async def report_completion(url: str) -> None:
            nonlocal completed
            if self.on_progress is None:
                return
            async with progress_lock:
                completed += 1
                try:
                    self.on_progress(completed, total, url)
                except Exception as exc:
                    _logger.warning(
                        "Batch progress callback failed for %s (%d/%d): %s",
                        url,
                        completed,
                        total,
                        exc,
                        exc_info=True,
                    )

        async def process_one(index: int, url: str) -> bool:
            async with semaphore:
                try:
                    document = await self.process_url(url)
                    if document is None:
                        raise TypeError("process_url() returned None instead of SafeDocument")
                    if not isinstance(document, SafeDocument):
                        raise TypeError(
                            "process_url() returned "
                            f"{type(document).__name__} instead of SafeDocument"
                        )
                    documents[index] = document
                    await report_completion(url)
                    return True
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    import traceback

                    async with errors_lock:
                        errors.append(
                            BatchErrorItem(
                                index=index,
                                url=url,
                                error=str(exc),
                                error_type=type(exc).__name__,
                                traceback=traceback.format_exc(),
                            )
                        )
                    await report_completion(url)
                    return False

        tasks = [asyncio.create_task(process_one(index, url)) for index, url in enumerate(urls)]
        try:
            results = await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        successful = sum(1 for item in results if item)
        return BatchResult(
            total=total,
            successful=successful,
            failed=total - successful,
            documents=documents,
            errors=errors,
        )

    def process_batch(self, urls: list[str]) -> BatchResult:
        """Synchronous wrapper for batch processing."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.process_batch_async(urls))

        raise RuntimeError(
            "ingest_many() cannot run inside an active event loop; use ingest_many_async() instead"
        )
