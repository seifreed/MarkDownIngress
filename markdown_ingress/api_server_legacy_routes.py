"""Legacy route registration for the FastAPI server."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from fastapi import FastAPI

from markdown_ingress.api_server_models import (
    BatchIngestResponse,
    ExtractorComparisonResponse,
    IngestResponse,
    SecurityReportResponse,
)


def register_legacy_routes(
    app: FastAPI,
    *,
    dependencies: Sequence[Any],
    ingest_endpoint: Callable[..., Any],
    retry_ingest_endpoint: Callable[..., Any],
    batch_ingest_endpoint: Callable[..., Any],
    security_report_endpoint: Callable[..., Any],
    extractor_comparison_endpoint: Callable[..., Any],
    health_endpoint: Callable[..., Any],
) -> None:
    """Register pre-v1 compatibility aliases for existing clients."""
    app.add_api_route(
        "/ingest",
        ingest_endpoint,
        methods=["POST"],
        response_model=IngestResponse,
        dependencies=list(dependencies),
    )
    app.add_api_route(
        "/ingest/retry",
        retry_ingest_endpoint,
        methods=["POST"],
        response_model=IngestResponse,
        dependencies=list(dependencies),
    )
    app.add_api_route(
        "/ingest/batch",
        batch_ingest_endpoint,
        methods=["POST"],
        response_model=BatchIngestResponse,
        dependencies=list(dependencies),
    )
    app.add_api_route(
        "/security/report",
        security_report_endpoint,
        methods=["POST"],
        response_model=SecurityReportResponse,
        dependencies=list(dependencies),
    )
    app.add_api_route(
        "/evaluate/extractors",
        extractor_comparison_endpoint,
        methods=["POST"],
        response_model=ExtractorComparisonResponse,
        dependencies=list(dependencies),
    )
    app.add_api_route("/health", health_endpoint, methods=["GET"])
