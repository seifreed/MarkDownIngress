"""Thin handler functions for FastAPI transport endpoints."""

from __future__ import annotations

import asyncio
import logging

from fastapi import HTTPException

from markdown_ingress.api_server_handler_errors import (
    INTERNAL_ERROR_DETAIL,
    is_queue_full_error,
    is_queue_unavailable_error,
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

_logger = logging.getLogger(__name__)
_API_RUNTIME_FAILURES = (Exception,)


async def handle_ingest(request: IngestRequest, ingest_func) -> IngestResponse:
    # Request cancellation stops waiting on the response, but the sync ingest
    # work already dispatched to the worker thread will continue to completion.
    try:
        doc = await asyncio.to_thread(
            ingest_func,
            url=str(request.url),
            **_common_ingest_kwargs(request),
        )
        return to_document_response(doc)
    except _API_RUNTIME_FAILURES as exc:
        log_runtime_error("Error processing ingest request for %s", exc, request.url)
        raise_runtime_http_error(exc)


async def handle_retry_ingest(request: RetryIngestRequest, retry_ingest_func) -> IngestResponse:
    # Cancellation interrupts the async wait only; the worker thread keeps
    # running until the sync retry flow completes.
    try:
        doc = await asyncio.to_thread(
            retry_ingest_func,
            url=str(request.url),
            mode=request.mode,
            strict=request.strict,
            model=request.model,
            max_retries=request.max_retries,
            enable_stealth=request.enable_stealth,
            initial_timeout=request.initial_timeout,
            max_timeout=request.max_timeout,
        )
        return to_document_response(doc)
    except _API_RUNTIME_FAILURES as exc:
        log_runtime_error("Error processing retry ingest request for %s", exc, request.url)
        raise_runtime_http_error(exc)


async def handle_sync_batch(request: BatchIngestRequest, ingest_many_func) -> BatchIngestResponse:
    try:
        return BatchIngestResponse(**await sync_batch_response(request, ingest_many_func))
    except _API_RUNTIME_FAILURES as exc:
        log_runtime_error("Error processing sync batch request", exc)
        raise_runtime_http_error(exc)


async def handle_batch_submit(
    request: BatchIngestRequest,
    ingest_many_func,
    job_queue,
    job_ttl_seconds: int,
) -> BatchJobAccepted:
    try:
        job = job_queue.submit(
            make_batch_job_task(request, ingest_many_func),
            webhook_url=str(request.webhook_url) if request.webhook_url is not None else None,
            start_immediately=False,
        )
    except _API_RUNTIME_FAILURES as exc:
        if is_queue_full_error(exc):
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        if is_queue_unavailable_error(exc):
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if isinstance(exc, ValueError):
            raise HTTPException(status_code=400, detail="Invalid request") from exc
        _logger.exception("Unexpected batch submit error")
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL) from exc

    return BatchJobAccepted(
        job_id=job.job_id,
        status="queued",
        created_at=job.created_at,
        poll_url=f"/api/v1/jobs/{job.job_id}",
        expires_in_seconds=job_ttl_seconds,
        ttl_applies_to="completed_jobs",
    )


async def handle_batch_status(job_id: str, job_source) -> BatchJobResponse:
    try:
        job = job_source(job_id) if callable(job_source) else job_source.get(job_id)
    except _API_RUNTIME_FAILURES as exc:
        if is_queue_unavailable_error(exc):
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        _logger.exception("Unexpected batch status error for %s", job_id)
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL) from exc
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
    try:
        report = await asyncio.to_thread(
            generate_security_report_func,
            url=str(request.url),
            **_common_ingest_kwargs(request),
        )
        return to_security_report_response(report)
    except _API_RUNTIME_FAILURES as exc:
        raise_runtime_http_error(exc)


async def handle_extractor_comparison(
    request: HTMLCompareRequest,
    compare_extractors_func,
) -> ExtractorComparisonResponse:
    try:
        results = await asyncio.to_thread(
            compare_extractors_func, request.html, model=request.model
        )
        return ExtractorComparisonResponse(results=results)
    except _API_RUNTIME_FAILURES as exc:
        _logger.exception("Error processing extractor comparison request")
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL) from exc
