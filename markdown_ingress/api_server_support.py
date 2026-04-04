"""Support helpers for the FastAPI server."""

from __future__ import annotations

import asyncio
from typing import Any

from markdown_ingress.api_server_models import (
    BatchIngestRequest,
    IngestResponse,
    SecurityReportResponse,
)
from markdown_ingress.models import SafeDocument, SecurityReport


def to_document_response(doc: SafeDocument) -> IngestResponse:
    return IngestResponse(
        markdown=doc.markdown,
        metadata=doc.metadata,
        token_estimate=doc.token_estimate,
        injection_score=doc.injection_score,
        flags=doc.flags,
        content_hash=doc.content_hash,
        removed_elements=doc.removed_elements,
        structured_blocks=doc.structured_blocks,
        chunks=doc.chunks,
        security_explanation=doc.security_explanation,
        observability=doc.observability,
    )


def to_security_report_response(report: SecurityReport) -> SecurityReportResponse:
    return SecurityReportResponse(
        injection_score=report.injection_score,
        risk_level=report.risk_level,
        pattern_matches=report.pattern_matches,
        flags=report.flags,
        hidden_content_detected=report.hidden_content_detected,
        hidden_elements_count=report.hidden_elements_count,
        imperative_density=report.imperative_density,
        url=report.url,
        title=report.title,
        token_estimate=report.token_estimate,
        token_reduction_percent=report.token_reduction_percent,
        original_size_bytes=report.original_size_bytes,
        cleaned_size_bytes=report.cleaned_size_bytes,
        content_hash=report.content_hash,
        structural_hash=report.structural_hash,
        removed_elements=report.removed_elements,
        language=report.language,
        explanation=report.explanation,
        observability=report.observability,
    )


def domain_policy_payload(policies) -> list[dict[str, Any]]:
    payload = []
    for policy in policies:
        data = policy.model_dump(exclude_none=True)
        payload.append({key: value for key, value in data.items() if value not in ([], {})})
    return payload


def _serialize_batch_result(request: BatchIngestRequest, result: Any) -> dict[str, Any]:
    raw_error_items = getattr(result, "error_items", None)
    if raw_error_items is None:
        fallback = getattr(result, "errors", [])
        raw_error_items = fallback if isinstance(fallback, list) else []
    error_by_index = {
        item.index: item.error
        for item in raw_error_items
        if hasattr(item, "index") and hasattr(item, "error")
    }
    errors_by_url: dict[str, list[str]] = getattr(result, "errors_by_url", {})
    legacy_errors_raw = getattr(result, "errors", {})
    legacy_errors = legacy_errors_raw if isinstance(legacy_errors_raw, dict) else {}
    errors_by_url_offsets: dict[str, int] = {}

    def _fallback_error_for(index: int) -> str | None:
        url = str(request.urls[index])
        if errors_by_url:
            options = errors_by_url.get(url) or []
            offset = errors_by_url_offsets.get(url, 0)
            if offset < len(options):
                errors_by_url_offsets[url] = offset + 1
                return options[offset]
            return None
        if isinstance(legacy_errors, dict):
            return legacy_errors.get(url)
        return None

    rows = []
    documents = list(getattr(result, "documents", []))
    total_inputs = len(request.urls)
    for index in range(total_inputs):
        document = documents[index] if index < len(documents) else None
        error = error_by_index.get(index, _fallback_error_for(index))
        if document is None and error is None:
            error = f"Missing batch result for input index {index}"
        rows.append(
            {
                "url": str(request.urls[index]),
                "success": document is not None,
                "data": to_document_response(document).model_dump() if document is not None else None,
                "error": error,
            }
        )

    return {
        "results": rows,
        "success_count": sum(1 for row in rows if row["success"]),
        "failure_count": sum(1 for row in rows if not row["success"]),
    }


async def sync_batch_response(
    request: BatchIngestRequest,
    ingest_many_func,
) -> dict[str, Any]:
    """Run batch ingestion through the shared batch API from an async endpoint.

    Cancellation stops waiting on the wrapper, but does not abort the sync
    batch work already dispatched to the worker thread.
    """
    result = await asyncio.to_thread(
        ingest_many_func,
        urls=[str(url) for url in request.urls],
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
        max_concurrent=request.max_concurrent,
    )
    return _serialize_batch_result(request, result)


def make_batch_job_task(request: BatchIngestRequest, ingest_many_func):
    """Create the persisted batch job task."""

    def run() -> dict[str, Any]:
        result = ingest_many_func(
            urls=[str(url) for url in request.urls],
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
            max_concurrent=request.max_concurrent,
        )
        return _serialize_batch_result(request, result)

    return run
