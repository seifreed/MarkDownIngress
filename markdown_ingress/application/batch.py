"""Batch processing services at the application layer."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Literal

from markdown_ingress.adapters.rendering.playwright_renderer import PLAYWRIGHT_AVAILABLE
from markdown_ingress.application.use_cases import BatchIngestUseCase, IngestUseCase
from markdown_ingress.config_models import IngestConfig
from markdown_ingress.models import SafeDocument
from markdown_ingress.shared_results import BatchErrorItem, BatchResult

RENDERER_AVAILABLE = PLAYWRIGHT_AVAILABLE


class BatchProcessor:
    """Process multiple URLs in batch via the application use case layer."""

    def __init__(
        self,
        mode: Literal["fast", "render", "auto"] = "fast",
        strict: bool = True,
        model: str = "gpt-4",
        timeout: float = 30.0,
        max_concurrent: int = 5,
        on_progress: Callable[[int, int, str], None] | None = None,
    ):
        self.mode = mode
        self.strict = strict
        self.model = model
        self.timeout = timeout
        self.max_concurrent = max_concurrent
        self.on_progress = on_progress
        self._batch_use_case = BatchIngestUseCase(
            ingest_use_case=IngestUseCase(playwright_available=RENDERER_AVAILABLE)
        )

    def _build_config(self) -> IngestConfig:
        return IngestConfig(
            mode=self.mode,
            strict=self.strict,
            model=self.model,
            timeout=self.timeout,
        )

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
        if self._uses_default_process_url():
            return await self._batch_use_case.execute(
                urls,
                self._build_config,
                max_concurrent=self.max_concurrent,
                on_progress=self.on_progress,
            )

        total = len(urls)
        documents: list[SafeDocument | None] = [None] * total
        errors: list[BatchErrorItem] = []
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def process_one(index: int, url: str) -> bool:
            async with semaphore:
                if self.on_progress is not None:
                    self.on_progress(index + 1, total, url)
                try:
                    documents[index] = await self.process_url(url)
                    return True
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    errors.append(BatchErrorItem(index=index, url=url, error=str(exc)))
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
