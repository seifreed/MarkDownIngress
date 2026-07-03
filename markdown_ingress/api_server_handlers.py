"""Thin handler functions for FastAPI transport endpoints."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Literal

from fastapi import HTTPException

from markdown_ingress.api_server_handler_errors import (
    log_runtime_error,
    raise_runtime_http_error,
)
from markdown_ingress.api_server_models import (
    BatchIngestRequest,
    BatchIngestResponse,
    BatchJobAccepted,
    BatchJobResponse,
    ExtractorComparisonResponse,
    HTMLCompareRequest,
    IngestRequest,
    IngestResponse,
    RetryIngestRequest,
    SecurityReportResponse,
)
from markdown_ingress.api_server_support import (
    _common_ingest_kwargs,
    make_batch_job_task,
    sync_batch_response,
    to_document_response,
    to_security_report_response,
)


def _handle_handler_exception(message: str, exc_ctx: object, exc: Exception) -> None:
    """Map an execution failure to transport-safe HTTP handling."""
    log_runtime_error(message, exc, exc_ctx)
    raise_runtime_http_error(exc)


_RunMode = Literal["thread", "awaitable"]


def _run_blocking(
    message: str,
    exc_ctx: object,
    func: Callable[..., Any],
    func_args: tuple[Any, ...] = (),
    func_kwargs: dict[str, Any] | None = None,
) -> Any:
    """Execute a synchronous handler path with unified transport error mapping."""
    if func_kwargs is None:
        func_kwargs = {}
    try:
        return func(*func_args, **func_kwargs)
    except HTTPException:
        raise
    except Exception as exc:
        _handle_handler_exception(message, exc_ctx, exc)


async def _run_handler(
    message: str,
    exc_ctx: object,
    func: Callable[..., Any],
    run_mode: _RunMode,
    func_args: tuple[Any, ...] = (),
    func_kwargs: dict[str, Any] | None = None,
) -> Any:
    try:
        if run_mode == "thread":
            return await asyncio.to_thread(func, *func_args, **(func_kwargs or {}))
        return await func(*func_args, **(func_kwargs or {}))
    except HTTPException:
        raise
    except Exception as exc:
        _handle_handler_exception(message, exc_ctx, exc)


async def _run_to_thread(
    message: str,
    exc_ctx: object,
    func: Callable[..., Any],
    func_args: tuple[Any, ...] = (),
    func_kwargs: dict[str, Any] | None = None,
) -> Any:
    return await _run_handler(
        message,
        exc_ctx,
        func,
        "thread",
        func_args,
        func_kwargs,
    )


async def _run_async(
    message: str,
    exc_ctx: object,
    func: Callable[..., Any],
    func_args: tuple[Any, ...] = (),
    func_kwargs: dict[str, Any] | None = None,
) -> Any:
    return await _run_handler(message, exc_ctx, func, "awaitable", func_args, func_kwargs)


async def handle_ingest(request: IngestRequest, ingest_func) -> IngestResponse:
    # Request cancellation stops waiting on the response, but the sync ingest
    # work already dispatched to the worker thread will continue to completion.
    doc = await _run_to_thread(
        message="Error processing ingest request for %s",
        exc_ctx=request.url,
        func=ingest_func,
        func_kwargs={"url": str(request.url), **_common_ingest_kwargs(request)},
    )
    return to_document_response(doc)


async def handle_retry_ingest(request: RetryIngestRequest, retry_ingest_func) -> IngestResponse:
    # Cancellation interrupts the async wait only; the worker thread keeps
    # running until the sync retry flow completes.
    doc = await _run_to_thread(
        message="Error processing retry ingest request for %s",
        exc_ctx=request.url,
        func=retry_ingest_func,
        func_kwargs={
            "url": str(request.url),
            "mode": request.mode,
            "strict": request.strict,
            "model": request.model,
            "max_retries": request.max_retries,
            "enable_stealth": request.enable_stealth,
            "initial_timeout": request.initial_timeout,
            "max_timeout": request.max_timeout,
        },
    )
    return to_document_response(doc)


async def handle_sync_batch(request: BatchIngestRequest, ingest_many_func) -> BatchIngestResponse:
    response = await _run_async(
        message="Error processing sync batch request",
        exc_ctx=request,
        func=sync_batch_response,
        func_kwargs={"request": request, "ingest_many_func": ingest_many_func},
    )
    return BatchIngestResponse(**response)


async def handle_batch_submit(
    request: BatchIngestRequest,
    ingest_many_func,
    job_queue,
    job_ttl_seconds: int,
) -> BatchJobAccepted:
    webhook_url = str(request.webhook_url) if request.webhook_url is not None else None
    job = _run_blocking(
        message="Error processing batch submit for %s",
        exc_ctx=request.webhook_url,
        func=job_queue.submit,
        func_args=(make_batch_job_task(request, ingest_many_func),),
        func_kwargs={"webhook_url": webhook_url, "start_immediately": False},
    )

    return BatchJobAccepted(
        job_id=job.job_id,
        status="queued",
        created_at=job.created_at,
        poll_url=f"/api/v1/jobs/{job.job_id}",
        expires_in_seconds=job_ttl_seconds,
        ttl_applies_to="completed_jobs",
    )


async def handle_batch_status(job_id: str, job_source) -> BatchJobResponse:
    job = _run_blocking(
        message="Error processing batch status request for %s",
        exc_ctx=job_id,
        func=(job_source if callable(job_source) else job_source.get),
        func_args=(job_id,),
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return BatchJobResponse(
        job_id=job.job_id,
        status=job.status,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        result=job.result,
        error=job.error,
    )


async def handle_security_report(
    request: IngestRequest, generate_security_report_func
) -> SecurityReportResponse:
    # Cancellation interrupts the async wait only; report generation continues
    # in the dispatched worker thread.
    report = await _run_to_thread(
        message="Error processing security report request for %s",
        exc_ctx=request.url,
        func=generate_security_report_func,
        func_kwargs={"url": str(request.url), **_common_ingest_kwargs(request)},
    )
    return to_security_report_response(report)


async def handle_extractor_comparison(
    request: HTMLCompareRequest,
    compare_extractors_func,
) -> ExtractorComparisonResponse:
    results = await _run_to_thread(
        message="Error processing extractor comparison request",
        exc_ctx=request.model,
        func=compare_extractors_func,
        func_args=(request.html,),
        func_kwargs={"model": request.model},
    )
    return ExtractorComparisonResponse(results=results)
