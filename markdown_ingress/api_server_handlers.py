"""Thin handler functions for FastAPI transport endpoints."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, NoReturn

import httpx
from fastapi import HTTPException

_logger = logging.getLogger(__name__)
_INTERNAL_ERROR_DETAIL = "Internal server error"

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
    domain_policy_payload,
    make_batch_job_task,
    sync_batch_response,
    to_document_response,
    to_security_report_response,
)
from markdown_ingress.core.fetcher import DomainCircuitOpenError, UnsupportedContentTypeError
from markdown_ingress.core.policy import PolicyBlockedError


def _is_playwright_runtime_import_error(exc: ImportError) -> bool:
    """Return whether this ImportError is the explicit render-mode Playwright denial."""
    message = str(exc)
    return (
        message.startswith("Render mode requires Playwright")
        or message.startswith("Playwright is not installed.")
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
    if isinstance(exc, PolicyBlockedError):
        detail = {"type": "policy_blocked", "message": str(exc)}
        if exc.document is not None:
            detail["policy_action"] = exc.document.metadata.get("policy_action")
            detail["flags"] = exc.document.flags
        raise HTTPException(status_code=403, detail=detail)
    raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


def _is_queue_full_error(exc: RuntimeError) -> bool:
    """Return whether a RuntimeError from the job queue is the expected capacity denial."""
    return str(exc) == "Job queue is full"


def _is_queue_unavailable_error(exc: RuntimeError) -> bool:
    """Return whether the queue is operationally unavailable but not internally broken."""
    message = str(exc)
    return message in {
        "Job queue is unavailable",
        "Job queue is closing",
        "Job queue is closed",
        "Job queue lease was lost; this instance can no longer accept or execute jobs",
        "Job queue is unavailable because the DB is owned by another active instance",
        "Job queue backend is temporarily unavailable because the current owner is busy",
    } or message.startswith("Job queue backend read failed:")


async def handle_ingest(request: IngestRequest, ingest_func) -> IngestResponse:
    # Request cancellation stops waiting on the response, but the sync ingest
    # work already dispatched to the worker thread will continue to completion.
    try:
        doc = await asyncio.to_thread(
            ingest_func,
            url=str(request.url),
            mode=request.mode,
            strict=request.strict,
            timeout=float(request.timeout),
            model=request.model,
            auto_render_threshold=request.auto_render_threshold,
            stealth=request.stealth,
            disable_http2=request.disable_http2,
            extreme_mode=request.extreme_mode,
            screenshot=request.screenshot,
            extract_metadata=request.extract_metadata,
            extract_links=request.extract_links,
            advanced_security=request.advanced_security,
            use_llm=request.use_llm,
            policy_name=request.policy_name,
            custom_patterns=request.custom_patterns,
            plugin_dirs=request.plugin_dirs,
            output_profile=request.output_profile,
            extract_blocks=request.extract_blocks,
            chunking_strategy=request.chunking_strategy,
            chunk_size=request.chunk_size,
            chunk_overlap=request.chunk_overlap,
            detect_language=request.detect_language,
            normalize_multilingual=request.normalize_multilingual,
            include_security_explanation=request.include_security_explanation,
            include_observability=request.include_observability,
            render_cost_budget=request.render_cost_budget,
            domain_policies=domain_policy_payload(request.domain_policies) or None,
        )
        return to_document_response(doc)
    except Exception as exc:
        _logger.exception("Error processing ingest request for %s", request.url)
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
        _logger.exception("Error processing retry ingest request for %s", request.url)
        _raise_runtime_http_error(exc)


async def handle_sync_batch(request: BatchIngestRequest, ingest_many_func) -> BatchIngestResponse:
    return BatchIngestResponse(**await sync_batch_response(request, ingest_many_func))


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
    except RuntimeError as exc:
        if _is_queue_full_error(exc):
            raise HTTPException(status_code=429, detail=str(exc))
        if _is_queue_unavailable_error(exc):
            raise HTTPException(status_code=503, detail=str(exc))
        _logger.exception("Unexpected batch submit error")
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)

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
    except RuntimeError as exc:
        if _is_queue_unavailable_error(exc):
            raise HTTPException(status_code=503, detail=str(exc))
        _logger.exception("Unexpected batch status error for %s", job_id)
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)
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


async def handle_security_report(request: IngestRequest, generate_security_report_func) -> SecurityReportResponse:
    # Cancellation interrupts the async wait only; report generation continues
    # in the dispatched worker thread.
    try:
        report = await asyncio.to_thread(
            generate_security_report_func,
            url=str(request.url),
            mode=request.mode,
            strict=request.strict,
            model=request.model,
            timeout=float(request.timeout),
            auto_render_threshold=request.auto_render_threshold,
            stealth=request.stealth,
            disable_http2=request.disable_http2,
            extreme_mode=request.extreme_mode,
            screenshot=request.screenshot,
            extract_metadata=request.extract_metadata,
            extract_links=request.extract_links,
            advanced_security=request.advanced_security,
            use_llm=request.use_llm,
            policy_name=request.policy_name,
            custom_patterns=request.custom_patterns,
            plugin_dirs=request.plugin_dirs,
            output_profile=request.output_profile,
            extract_blocks=request.extract_blocks,
            chunking_strategy=request.chunking_strategy,
            chunk_size=request.chunk_size,
            chunk_overlap=request.chunk_overlap,
            detect_language=request.detect_language,
            normalize_multilingual=request.normalize_multilingual,
            include_security_explanation=request.include_security_explanation,
            include_observability=request.include_observability,
            render_cost_budget=request.render_cost_budget,
            domain_policies=domain_policy_payload(request.domain_policies) or None,
        )
        return to_security_report_response(report)
    except Exception as exc:
        _raise_runtime_http_error(exc)


async def handle_extractor_comparison(
    request: HTMLCompareRequest,
    compare_extractors_func,
) -> ExtractorComparisonResponse:
    try:
        return ExtractorComparisonResponse(results=compare_extractors_func(request.html, model=request.model))
    except Exception as exc:
        _logger.exception("Error processing extractor comparison request")
        raise HTTPException(status_code=500, detail=_INTERNAL_ERROR_DETAIL)


def build_stats_payload(api_version: str, get_ingest_stats_func, job_queue, job_ttl_seconds: int, max_queued_jobs: int):
    try:
        pending = job_queue.pending_count(cleanup_expired=False)
    except TypeError:
        try:
            pending = job_queue.pending_count()
        except RuntimeError:
            pending = None
    except RuntimeError:
        pending = None
    return {
        "version": api_version,
        "stats": get_ingest_stats_func(),
        "job_queue": {
            "pending": pending,
            "ttl_seconds": job_ttl_seconds,
            "ttl_applies_to": "completed_jobs_with_persisted_ttl_or_legacy_compatibility_ttl",
            "max_queued_jobs": max_queued_jobs,
        },
    }


def build_root_payload(api_version: str) -> dict[str, Any]:
    return {
        "message": "MarkDownIngress API - See /docs for interactive documentation",
        "version": api_version,
        "endpoints": {
            "docs": "/docs",
            "health": "/api/v1/health",
            "ingest": "/api/v1/ingest",
            "retry_ingest": "/api/v1/ingest/retry",
            "batch_ingest": "/api/v1/ingest/batch",
            "batch_jobs": "/api/v1/jobs/batch",
            "security_report": "/api/v1/security/report",
            "extractor_evaluation": "/api/v1/evaluate/extractors",
            "stats": "/api/v1/stats",
        },
    }
