"""Legacy route registration for the FastAPI server."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI

from markdown_ingress.api_server_models import (
    BatchIngestResponse,
    ExtractorComparisonResponse,
    IngestResponse,
    SecurityReportResponse,
)


@dataclass(frozen=True)
class LegacyRouteHandlers:
    ingest_endpoint: Callable[..., Any]
    retry_ingest_endpoint: Callable[..., Any]
    batch_ingest_endpoint: Callable[..., Any]
    security_report_endpoint: Callable[..., Any]
    extractor_comparison_endpoint: Callable[..., Any]
    health_endpoint: Callable[..., Any]


def register_legacy_routes(
    app: FastAPI,
    dependencies: Sequence[Any],
    handlers: LegacyRouteHandlers,
) -> None:
    """Register pre-v1 compatibility aliases for existing clients."""
    route_dependencies = list(dependencies)
    post_routes = [
        ("/ingest", handlers.ingest_endpoint, IngestResponse),
        ("/ingest/retry", handlers.retry_ingest_endpoint, IngestResponse),
        ("/ingest/batch", handlers.batch_ingest_endpoint, BatchIngestResponse),
        ("/security/report", handlers.security_report_endpoint, SecurityReportResponse),
        (
            "/evaluate/extractors",
            handlers.extractor_comparison_endpoint,
            ExtractorComparisonResponse,
        ),
    ]
    for path, endpoint, response_model in post_routes:
        app.add_api_route(
            path,
            endpoint,
            methods=["POST"],
            response_model=response_model,
            dependencies=route_dependencies,
        )
    app.add_api_route("/health", handlers.health_endpoint, methods=["GET"])
