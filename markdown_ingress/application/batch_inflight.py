"""In-flight coordination helpers for batch ingestion."""

from __future__ import annotations

import asyncio
import copy
import logging

from markdown_ingress.application.batch_state import (
    _BatchContext,
    _BatchInFlightRecord,
)
from markdown_ingress.application.exceptions import _copy_batch_exception
from markdown_ingress.models import SafeDocument

_logger = logging.getLogger(__name__)


def _remove_if_current(ctx: _BatchContext, request_key: str, record: _BatchInFlightRecord) -> None:
    if ctx.batch_inflight.get(request_key) is record:
        ctx.batch_inflight.pop(request_key, None)


async def register_batch_inflight(
    ctx: _BatchContext, request_key: str
) -> tuple[_BatchInFlightRecord, bool]:
    """Register a batch request as in-flight and return (record, is_leader)."""
    async with ctx.batch_inflight_lock:
        record = ctx.batch_inflight.get(request_key)
        if record is not None and record.future.done():
            ctx.batch_inflight.pop(request_key, None)
            record = None
        if record is None:
            record = _BatchInFlightRecord(future=asyncio.get_running_loop().create_future())
            ctx.batch_inflight[request_key] = record
            return record, True
        record.followers += 1
        return record, False


async def remove_finished_batch_inflight(
    ctx: _BatchContext, request_key: str, record: _BatchInFlightRecord
) -> None:
    """Remove a completed in-flight record if it still belongs to the request."""
    async with ctx.batch_inflight_lock:
        if record.future.done():
            _remove_if_current(ctx, request_key, record)


async def cancel_batch_inflight(
    ctx: _BatchContext, request_key: str, record: _BatchInFlightRecord
) -> None:
    """Cancel an in-flight future and remove it from the registry."""
    async with ctx.batch_inflight_lock:
        if not record.future.done():
            record.future.cancel()
        _remove_if_current(ctx, request_key, record)


async def publish_batch_inflight_result(
    ctx: _BatchContext,
    request_key: str,
    record: _BatchInFlightRecord,
    document: SafeDocument,
) -> int:
    """Publish the leader document copy to followers and return follower count."""
    async with ctx.batch_inflight_lock:
        try:
            shared_document = copy.deepcopy(document)
        except Exception as exc:
            _logger.error(
                "Batch deepcopy failed for %s: %s",
                request_key[:32],
                exc,
                exc_info=True,
            )
            try:
                record.future.set_exception(exc)
            except asyncio.InvalidStateError as state_exc:
                _logger.debug(
                    "Batch inflight future already resolved after deepcopy failure for %s: %s",
                    request_key[:32],
                    state_exc,
                    exc_info=True,
                )
            _remove_if_current(ctx, request_key, record)
            raise
        shared_count = record.followers
        try:
            record.future.set_result((shared_document, shared_count))
        except asyncio.InvalidStateError:
            _logger.warning(
                "Batch inflight future already done for %s (state: %s); "
                "followers may have been cancelled",
                request_key[:32],
                getattr(record.future, "_state", "unknown"),
            )
        return shared_count


async def publish_batch_inflight_exception(
    ctx: _BatchContext,
    request_key: str,
    record: _BatchInFlightRecord,
    exc: Exception,
) -> None:
    """Publish an execution failure to current and queued in-flight followers."""
    async with ctx.batch_inflight_lock:
        try:
            if not record.future.done():
                record.future.set_exception(_copy_batch_exception(exc))
        except asyncio.InvalidStateError:
            _logger.warning(
                "Batch inflight future already done when setting exception for %s",
                request_key[:32],
            )
        if record.followers == 0 and record.future.done():
            try:
                record.future.exception()
            except (asyncio.CancelledError, asyncio.InvalidStateError) as future_exc:
                _logger.debug(
                    "Consumed batch inflight exception for %s: %s",
                    request_key[:32],
                    future_exc,
                    exc_info=True,
                )
        # Keep the record available so followers waiting on the semaphore can
        # observe the same exception. register_batch_inflight removes stale done
        # records on the next request.
