"""Shared batch execution state models."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from markdown_ingress.config_models import IngestConfig
from markdown_ingress.core.cache import Cache
from markdown_ingress.models import SafeDocument
from markdown_ingress.shared_results import BatchErrorItem

_logger = logging.getLogger(__name__)

PROGRESS_CALLBACK_ERRORS: tuple[type[Exception], ...] = (
    LookupError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)


@dataclass
class PreparedBatchRequest:
    index: int
    url: str
    requested_mode: str
    resolved_config: IngestConfig
    request_key: str
    cache_backend: Cache | None
    cache_key: str | None


@dataclass
class BatchInFlightRecord:
    future: asyncio.Future[tuple[SafeDocument, int]]
    followers: int = 0


@dataclass
class CostBudget:
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
class BatchContext:
    """Shared mutable state for a single batch execution."""

    total: int
    documents: list[SafeDocument | None]
    errors: list[BatchErrorItem]
    semaphore: asyncio.Semaphore
    errors_lock: asyncio.Lock
    batch_inflight: dict[str, BatchInFlightRecord]
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
            current = self.completed
            try:
                self.on_progress(current, self.total, url)
            except PROGRESS_CALLBACK_ERRORS as exc:
                _logger.warning(
                    "Batch progress callback failed for %s (%d/%d): %s",
                    url,
                    current,
                    self.total,
                    exc,
                    exc_info=True,
                )
