"""Route registration for the FastAPI server."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from markdown_ingress.api_server_handlers import _is_queue_unavailable_error
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


@dataclass(frozen=True)
class ApiRouteProviders:
    """Dynamic providers for api_server globals used by route handlers."""

    api_version: Callable[[], str]
    ingest: Callable[[], Any]
    retry_ingest: Callable[[], Any]
    ingest_many: Callable[[], Any]
    generate_security_report: Callable[[], Any]
    get_ingest_stats: Callable[[], Any]
    get_job_queue: Callable[[], Any]
    get_job_record: Callable[[], Any]
    get_job_ttl_seconds: Callable[[], int]
    snapshot_job_subsystem: Callable[[], Any]
    compare_extractors: Callable[[], Any]
    validate_batch_request_ssrf_async: Callable[[], Any]
    handle_ingest: Callable[[], Any]
    handle_retry_ingest: Callable[[], Any]
    handle_sync_batch: Callable[[], Any]
    handle_batch_submit: Callable[[], Any]
    handle_batch_status: Callable[[], Any]
    handle_security_report: Callable[[], Any]
    handle_extractor_comparison: Callable[[], Any]
    build_stats_payload: Callable[[], Any]
    build_health_payload: Callable[[], Any]
    build_detailed_health_payload: Callable[[], Any]
    build_root_payload: Callable[[], Any]


@dataclass(frozen=True)
class RegisteredApiRoutes:
    ingest_endpoint: Callable[[IngestRequest], Awaitable[IngestResponse]]
    retry_ingest_endpoint: Callable[[RetryIngestRequest], Awaitable[IngestResponse]]
    batch_ingest_endpoint: Callable[[BatchIngestRequest], Awaitable[BatchIngestResponse]]
    batch_job_submit: Callable[[BatchIngestRequest], Awaitable[BatchJobAccepted]]
    batch_job_status: Callable[[str], Awaitable[BatchJobResponse]]
    security_report_endpoint: Callable[[IngestRequest], Awaitable[SecurityReportResponse]]
    extractor_comparison_endpoint: Callable[
        [HTMLCompareRequest], Awaitable[ExtractorComparisonResponse]
    ]
    stats_endpoint: Callable[[], Awaitable[dict[str, Any]]]
    health: Callable[[], Awaitable[dict[str, Any]]]
    health_detailed: Callable[[], Awaitable[dict[str, Any]]]
    root: Callable[[], Awaitable[dict[str, str]]]


def register_api_routes(
    app: FastAPI,
    *,
    require_api_key: Callable[..., Any],
    require_rate_limit: Callable[..., Any],
    providers: ApiRouteProviders,
) -> RegisteredApiRoutes:
    protected_dependencies = [Depends(require_api_key), Depends(require_rate_limit)]

    @app.post(
        "/api/v1/ingest",
        response_model=IngestResponse,
        dependencies=protected_dependencies,
    )
    async def ingest_endpoint(request: IngestRequest):
        """Ingest a single URL and return structured markdown plus optional blocks/chunks."""
        return await providers.handle_ingest()(request, providers.ingest())

    @app.post(
        "/api/v1/ingest/retry",
        response_model=IngestResponse,
        dependencies=protected_dependencies,
    )
    async def retry_ingest_endpoint(request: RetryIngestRequest):
        """Ingest with automatic retry logic and timeout escalation."""
        return await providers.handle_retry_ingest()(request, providers.retry_ingest())

    @app.post(
        "/api/v1/ingest/batch",
        response_model=BatchIngestResponse,
        dependencies=protected_dependencies,
    )
    async def batch_ingest_endpoint(request: BatchIngestRequest):
        """Process a batch synchronously for simple clients."""
        await providers.validate_batch_request_ssrf_async()(request)
        return await providers.handle_sync_batch()(request, providers.ingest_many())

    @app.post(
        "/api/v1/jobs/batch",
        response_model=BatchJobAccepted,
        dependencies=protected_dependencies,
    )
    async def batch_job_submit(request: BatchIngestRequest):
        """Queue a batch ingestion job and return a polling handle."""
        await providers.validate_batch_request_ssrf_async()(request)
        try:
            job_queue = providers.get_job_queue()()
        except (RuntimeError, OSError, ValueError) as exc:
            if _is_queue_unavailable_error(exc):
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            raise
        job_ttl_seconds = getattr(job_queue, "ttl_seconds", None)
        if job_ttl_seconds is None:
            job_ttl_seconds = providers.get_job_ttl_seconds()
        return await providers.handle_batch_submit()(
            request,
            providers.ingest_many(),
            job_queue,
            job_ttl_seconds,
        )

    @app.get(
        "/api/v1/jobs/{job_id}",
        response_model=BatchJobResponse,
        dependencies=protected_dependencies,
    )
    async def batch_job_status(job_id: str):
        """Poll an async batch job."""
        return await providers.handle_batch_status()(job_id, providers.get_job_record())

    @app.post(
        "/api/v1/security/report",
        response_model=SecurityReportResponse,
        dependencies=protected_dependencies,
    )
    async def security_report_endpoint(request: IngestRequest):
        """Generate a detailed security report."""
        return await providers.handle_security_report()(
            request, providers.generate_security_report()
        )

    @app.post(
        "/api/v1/evaluate/extractors",
        response_model=ExtractorComparisonResponse,
        dependencies=[Depends(require_api_key)],
    )
    async def extractor_comparison_endpoint(request: HTMLCompareRequest):
        """Compare supported extractors against a raw HTML payload."""
        return await providers.handle_extractor_comparison()(
            request, providers.compare_extractors()
        )

    @app.get("/api/v1/stats", dependencies=[Depends(require_api_key)])
    async def stats_endpoint():
        """Expose process-level observability stats."""
        snapshot = providers.snapshot_job_subsystem()(start_repair=False)
        return providers.build_stats_payload()(
            api_version=providers.api_version(),
            ingest_stats=providers.get_ingest_stats()(),
            snapshot=snapshot,
        )

    @app.get("/api/v1/health")
    async def health():
        """Public health check endpoint - does not expose internal paths/state."""
        snapshot = providers.snapshot_job_subsystem()(start_repair=False)
        return providers.build_health_payload()(
            api_version=providers.api_version(),
            snapshot=snapshot,
        )

    @app.get("/api/v1/health/detailed", dependencies=[Depends(require_api_key)])
    async def health_detailed():
        """Authenticated health check with full job-queue observability."""
        snapshot = providers.snapshot_job_subsystem()(start_repair=False)
        return providers.build_detailed_health_payload()(
            api_version=providers.api_version(),
            snapshot=snapshot,
        )

    @app.get("/")
    async def root():
        """Public root endpoint - minimal metadata only."""
        return providers.build_root_payload()(api_version=providers.api_version())

    return RegisteredApiRoutes(
        ingest_endpoint=ingest_endpoint,
        retry_ingest_endpoint=retry_ingest_endpoint,
        batch_ingest_endpoint=batch_ingest_endpoint,
        batch_job_submit=batch_job_submit,
        batch_job_status=batch_job_status,
        security_report_endpoint=security_report_endpoint,
        extractor_comparison_endpoint=extractor_comparison_endpoint,
        stats_endpoint=stats_endpoint,
        health=health,
        health_detailed=health_detailed,
        root=root,
    )
