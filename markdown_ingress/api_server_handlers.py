"""Thin handler functions for FastAPI transport endpoints."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from typing import NoReturn

import httpx
from fastapi import HTTPException

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
from markdown_ingress.core.policy import (
    DomainCircuitOpenError,
    PolicyBlockedError,
    UnsupportedContentTypeError,
)

_logger = logging.getLogger(__name__)
_INTERNAL_ERROR_DETAIL = "Internal server error"


def _is_playwright_runtime_import_error(exc: ImportError) -> bool:
    """Return whether this ImportError is the explicit render-mode Playwright denial."""
    message = str(exc)
    return message.startswith("Render mode requires Playwright") or message.startswith(
        "Playwright is not installed."
    )


def _raise_runtime_http_error(exc: Exception) -> NoReturn:
    """Map expected runtime denials and environment errors to stable HTTP responses."""
    if isinstance(exc, ImportError) and _is_playwright_runtime_import_error(exc):
        raise HTTPException(status_code=400, detail="Render mode requires Playwright")
    if isinstance(exc, UnsupportedContentTypeError):
        raise HTTPException(status_code=415, detail=str(exc))
    if isinstance(exc, DomainCircuitOpenError):
        raise HTTPException(status_code=429, detail=str(exc))
    if isinstance(exc, (httpx.InvalidURL, httpx.UnsupportedProtocol)):
        raise HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        if 400 <= status_code < 500:
            mapped_status = status_code
        else:
            mapped_status = 502
        raise HTTPException(
            status_code=mapped_status,
            detail="Upstream fetch returned an HTTP error",
        )
    if isinstance(exc, ValueError):
        # Never echo ValueError message back — it often carries internal
        # hostnames, IP addresses, or filesystem paths (e.g. SSRF protection).
        _logger.warning("ValueError mapped to 400 Bad Request: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid request")
    if isinstance(exc, PolicyBlockedError):
        # Generic policy message only. Flags and policy_action may reveal
        # internal detection patterns and are logged server-side instead.
        if exc.document is not None:
            _logger.info(
                "PolicyBlockedError flags=%s action=%s",
                exc.document.flags,
                exc.document.metadata.get("policy_action"),
            )
        raise HTTPException(
            status_code=403,
            detail={"type": "policy_blocked", "message": "Content blocked by security policy"},
        )
    raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


def _log_runtime_error(message: str, exc: Exception, *args: object) -> None:
    """Keep expected HTTP-mapped failures out of server error logs."""
    if isinstance(
        exc,
        (
            ImportError,
            UnsupportedContentTypeError,
            DomainCircuitOpenError,
            httpx.InvalidURL,
            httpx.UnsupportedProtocol,
            httpx.HTTPStatusError,
            ValueError,
            PolicyBlockedError,
        ),
    ):
        _logger.debug(message, *args, exc_info=True)
        return
    _logger.exception(message, *args)


def _is_queue_full_error(exc: Exception) -> bool:
    """Return whether a RuntimeError from the job queue is the expected capacity denial."""
    return str(exc) == "Job queue is full"


def _is_queue_unavailable_error(exc: Exception) -> bool:
    """Return whether the queue is operationally unavailable but not internally broken."""
    message = str(exc)
    return (
        isinstance(exc, sqlite3.Error)
        or message
        in {
            "Job queue is unavailable",
            "Job queue is closing",
            "Job queue is closed",
            "Job queue lease was lost; this instance can no longer accept or execute jobs",
            "Job queue is unavailable because the DB is owned by another active instance",
            "Job queue backend is temporarily unavailable because the current owner is busy",
        }
        or message.startswith("Job queue backend read failed:")
    )


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
    except Exception as exc:
        _log_runtime_error("Error processing ingest request for %s", exc, request.url)
        _raise_runtime_http_error(exc)


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
    except Exception as exc:
        _log_runtime_error("Error processing retry ingest request for %s", exc, request.url)
        _raise_runtime_http_error(exc)


async def handle_sync_batch(request: BatchIngestRequest, ingest_many_func) -> BatchIngestResponse:
    try:
        return BatchIngestResponse(**await sync_batch_response(request, ingest_many_func))
    except Exception as exc:
        _log_runtime_error("Error processing sync batch request", exc)
        _raise_runtime_http_error(exc)


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
    except Exception as exc:
        if _is_queue_full_error(exc):
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        if _is_queue_unavailable_error(exc):
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if isinstance(exc, ValueError):
            raise HTTPException(status_code=400, detail="Invalid request") from exc
        _logger.exception("Unexpected batch submit error")
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL) from exc

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
    except Exception as exc:
        if _is_queue_unavailable_error(exc):
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        _logger.exception("Unexpected batch status error for %s", job_id)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL) from exc
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
    except Exception as exc:  # noqa: BLE001 - API boundary converts failures to HTTP errors
        _raise_runtime_http_error(exc)


async def handle_extractor_comparison(
    request: HTMLCompareRequest,
    compare_extractors_func,
) -> ExtractorComparisonResponse:
    try:
        results = await asyncio.to_thread(
            compare_extractors_func, request.html, model=request.model
        )
        return ExtractorComparisonResponse(results=results)
    except Exception as exc:
        _logger.exception("Error processing extractor comparison request")
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL) from exc
