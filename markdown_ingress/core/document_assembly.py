"""SafeDocument metadata and assembly helpers."""

from __future__ import annotations

import copy
import math
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

from markdown_ingress.config_models import DomainPolicy, IngestConfig
from markdown_ingress.core.document_security_patterns import (
    PatternSpec,
    _dedupe_preserving_order,
)
from markdown_ingress.core.ingest_stats import record_policy_action, timed_stage_with_snapshot
from markdown_ingress.core.interfaces import ITokenEstimator
from markdown_ingress.core.policy import PolicyBlockedError, PolicyEngine
from markdown_ingress.core.security_engine import SecurityEngine
from markdown_ingress.core.ssrf import normalize_hostname
from markdown_ingress.core.structured import blocks_to_dicts, chunks_to_dicts
from markdown_ingress.models import FetchResult, SafeDocument


def _determine_output_formats(
    config: IngestConfig,
    structured_blocks: list,
    chunks: list,
    enriched_metadata,
    security_result: dict,
) -> tuple[list[str], object, list[str]]:
    """Compute output_formats, security_explanation_payload, and emitted_output_formats."""
    output_formats = list(config.output_formats)
    if chunks and "chunks" not in output_formats:
        output_formats.append("chunks")
    available_formats = {"markdown"}
    if structured_blocks:
        available_formats.add("blocks")
    if chunks:
        available_formats.add("chunks")
    if enriched_metadata is not None:
        available_formats.add("metadata")
    security_explanation_payload = (
        security_result.get("explanation") if config.include_security_explanation else None
    )
    if security_explanation_payload is not None:
        available_formats.add("security")
    emitted_output_formats: list[str] = []
    for fmt in [*output_formats, "markdown", "blocks", "chunks", "metadata", "security"]:
        if fmt in available_formats and fmt not in emitted_output_formats:
            emitted_output_formats.append(fmt)
    return output_formats, security_explanation_payload, emitted_output_formats


def _build_fetch_metadata(
    fetch_result: FetchResult,
    extraction_result,
    hostname: str,
    config: IngestConfig,
) -> dict:
    """Build the fetch-origin fields: URL, title, timing, and request settings."""
    return {
        "url": fetch_result.url,
        "final_url": fetch_result.final_url,
        "hostname": hostname,
        "title": extraction_result.title,
        "fetch_time_ms": fetch_result.timing_ms,
        "status_code": fetch_result.status_code,
        "model": config.model,
        "mode": fetch_result.metadata.get("effective_mode", config.mode),
        "strict": config.strict,
    }


def _build_content_analysis_metadata(
    fetch_result: FetchResult,
    markdown: str,
    token_savings: dict[str, Any],
    structural_hash: str,
    security_result: dict,
    scorer,
    extraction_result,
    config: IngestConfig,
) -> dict:
    """Build content-analysis fields: sizes, hashes, security scores, and risk level."""
    return {
        "token_savings": token_savings,
        "risk_level": scorer.get_risk_level(security_result["injection_score"]),
        "structural_hash": structural_hash,
        "original_size_bytes": len(fetch_result.html.encode("utf-8")),
        "cleaned_size_bytes": len(markdown.encode("utf-8")),
        "pattern_matches": security_result["pattern_matches"],
        "imperative_density": security_result["imperative_density"],
        "hidden_content_detected": extraction_result.removed_hidden > 0,
        "advanced_security": config.advanced_security,
        "security_scan_method": security_result["scan_method"],
    }


def _build_pipeline_config_metadata(
    config: IngestConfig,
    fetch_result: FetchResult,
    output_formats: list[str],
    emitted_output_formats: list[str],
    extra_patterns: Sequence[PatternSpec],
    plugins_loaded: int,
    operational_flags: list[str],
    domain_rule_stats: dict,
) -> dict:
    """Build pipeline-configuration fields: output formats, costs, and operational flags."""
    return {
        "custom_patterns_count": len(extra_patterns),
        "plugins_loaded": plugins_loaded,
        "output_profile": config.output_profile,
        "output_formats": output_formats,
        "emitted_output_formats": emitted_output_formats,
        "chunking_strategy": config.chunking_strategy,
        "cost_units_used": fetch_result.metadata.get("cost_units_used", 0),
        "render_cost_budget": fetch_result.metadata.get("render_cost_budget"),
        "operational_flags": operational_flags,
        "domain_rule_stats": domain_rule_stats,
        "fetch_metadata": copy.deepcopy(fetch_result.metadata),
    }


def _apply_language_metadata(metadata: dict, enriched_metadata, config: IngestConfig) -> None:
    """Conditionally add language detection fields to metadata in-place."""
    if not enriched_metadata:
        return
    metadata["language"] = enriched_metadata.get("language")
    metadata["language_source"] = enriched_metadata.get("language_source")
    metadata["language_confidence"] = enriched_metadata.get("language_confidence")
    if not config.detect_language:
        metadata.pop("language", None)
        metadata.pop("language_source", None)
        metadata.pop("language_confidence", None)


def _apply_domain_policy_metadata(
    metadata: dict, matched_domain_policy: DomainPolicy | None
) -> None:
    """Conditionally add domain policy fields to metadata in-place."""
    if matched_domain_policy is not None:
        metadata["domain_policy"] = {
            "domain": matched_domain_policy.domain,
            "notes": matched_domain_policy.notes,
        }


def _assemble_document_metadata(
    config: IngestConfig,
    fetch_result: FetchResult,
    extraction_result,
    hostname: str,
    token_savings: dict[str, Any],
    structural_hash: str,
    output_formats: list[str],
    emitted_output_formats: list[str],
    security_result: dict,
    scorer,
    enriched_metadata,
    matched_domain_policy: DomainPolicy | None,
    extra_patterns: Sequence[PatternSpec],
    plugins_loaded: int,
    operational_flags: list[str],
    domain_rule_stats: dict,
    markdown: str,
) -> dict:
    """Build the metadata dict that accompanies the SafeDocument."""
    metadata: dict = {
        **_build_fetch_metadata(fetch_result, extraction_result, hostname, config),
        **_build_content_analysis_metadata(
            fetch_result,
            markdown,
            token_savings,
            structural_hash,
            security_result,
            scorer,
            extraction_result,
            config,
        ),
        **_build_pipeline_config_metadata(
            config,
            fetch_result,
            output_formats,
            emitted_output_formats,
            extra_patterns,
            plugins_loaded,
            operational_flags,
            domain_rule_stats,
        ),
    }
    _apply_language_metadata(metadata, enriched_metadata, config)
    _apply_domain_policy_metadata(metadata, matched_domain_policy)
    return metadata


def _apply_policy_decision(
    security_result: dict,
    policy_engine: PolicyEngine,
    config: IngestConfig,
    metadata: dict,
) -> str:
    """Determine the policy action, record it, and annotate metadata and flags in-place."""
    block_threshold, warn_threshold = SecurityEngine.effective_thresholds(
        policy_engine.policy.block_threshold,
        policy_engine.policy.warn_threshold,
        strict=config.strict,
    )
    injection_score = float(security_result["injection_score"])
    if math.isnan(injection_score):
        policy_action = "block"
    elif injection_score >= block_threshold:
        policy_action = "block"
    elif injection_score >= warn_threshold:
        policy_action = "warn"
    else:
        policy_action = "allow"
    record_policy_action(policy_action)
    metadata["policy"] = config.policy_name
    metadata["policy_action"] = policy_action
    if policy_action == "block":
        security_result["flags"] = _dedupe_preserving_order(
            [*list(security_result["flags"]), "policy_block"]
        )
    return policy_action


def _construct_safe_document_instance(
    markdown: str,
    metadata: dict,
    token_count: int,
    content_hash: str,
    security_result: dict,
    extraction_result,
    fetch_result: FetchResult,
    config: IngestConfig,
    links,
    enriched_metadata,
    structured_blocks: list,
    chunks: list,
    chunks_requested: bool,
    policy_action: str,
    stage_timings: dict[str, float],
    security_explanation_payload,
) -> SafeDocument:
    """Construct and return the SafeDocument from assembled pipeline outputs."""
    removed_elements = {
        "tags": extraction_result.removed_tags,
        "hidden_elements": extraction_result.removed_hidden,
    }
    injection_score = float(security_result["injection_score"])
    if math.isnan(injection_score):
        injection_score = 1.0
    document = SafeDocument(
        markdown=markdown,
        metadata=metadata,
        token_estimate=token_count,
        content_hash=content_hash,
        injection_score=injection_score,
        flags=security_result["flags"],
        removed_elements=removed_elements,
        screenshot_path=fetch_result.metadata.get("screenshot_path"),
        enriched_metadata=enriched_metadata,
        links=links,
        nova_score=security_result.get("nova_score"),
        nova_details=security_result.get("nova_details"),
        structured_blocks=blocks_to_dicts(structured_blocks) if config.extract_blocks else None,
        chunks=chunks_to_dicts(chunks) if chunks_requested else None,
        security_explanation=security_explanation_payload,
        observability=(
            {
                "stage_timings_ms": stage_timings,
                "policy_action": policy_action,
                "cost_units_used": fetch_result.metadata.get("cost_units_used", 0),
            }
            if config.include_observability
            else None
        ),
    )
    if config.include_observability and document.observability:
        document.metadata["stage_timings_ms"] = document.observability["stage_timings_ms"]
    return document


def _build_safe_document(
    *,
    config: IngestConfig,
    fetch_result: FetchResult,
    extraction_result,
    markdown: str,
    structured_blocks: list,
    chunks: list,
    chunks_requested: bool,
    enriched_metadata,
    links,
    security_result: dict,
    token_estimator: ITokenEstimator,
    hasher,
    scorer,
    matched_domain_policy: DomainPolicy | None,
    operational_flags: list[str],
    domain_rule_stats: dict,
    extra_patterns: Sequence[PatternSpec],
    plugins_loaded: int,
    stage_timings: dict[str, float],
    policy_engine: PolicyEngine,
) -> SafeDocument:
    """Hash, assemble metadata, apply policy, and construct the SafeDocument."""
    content_hash = timed_stage_with_snapshot(
        stage_timings, "hash_content", lambda: hasher.hash_content(markdown)
    )
    structural_hash = timed_stage_with_snapshot(
        stage_timings,
        "hash_structural",
        lambda: hasher.hash_structural(markdown),
    )
    token_count = timed_stage_with_snapshot(
        stage_timings, "tokens", lambda: token_estimator.estimate(markdown)
    )
    token_savings = token_estimator.estimate_savings(fetch_result.html, markdown)
    final_url = fetch_result.final_url or fetch_result.url or ""
    hostname = normalize_hostname(urlsplit(final_url).hostname or "")
    output_formats, security_explanation_payload, emitted_output_formats = (
        _determine_output_formats(
            config, structured_blocks, chunks, enriched_metadata, security_result
        )
    )
    metadata = _assemble_document_metadata(
        config,
        fetch_result,
        extraction_result,
        hostname,
        token_savings,
        structural_hash,
        output_formats,
        emitted_output_formats,
        security_result,
        scorer,
        enriched_metadata,
        matched_domain_policy,
        extra_patterns,
        plugins_loaded,
        operational_flags,
        domain_rule_stats,
        markdown,
    )
    policy_action = _apply_policy_decision(security_result, policy_engine, config, metadata)
    document = _construct_safe_document_instance(
        markdown,
        metadata,
        token_count,
        content_hash,
        security_result,
        extraction_result,
        fetch_result,
        config,
        links,
        enriched_metadata,
        structured_blocks,
        chunks,
        chunks_requested,
        policy_action,
        stage_timings,
        security_explanation_payload,
    )
    if policy_action == "block":
        raise PolicyBlockedError(
            f"Policy '{config.policy_name}' blocked content for {fetch_result.url}",
            document=document,
        )
    return document
