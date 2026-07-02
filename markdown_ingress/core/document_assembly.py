"""SafeDocument metadata and assembly helpers."""

from __future__ import annotations

import copy
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from markdown_ingress.config_models import DomainPolicy, IngestConfig
from markdown_ingress.core.document_output_selection import (
    DocumentOutputSelection,
    build_output_selection,
)
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


@dataclass(frozen=True)
class _SafeDocumentAssemblyContext:
    config: IngestConfig
    fetch_result: FetchResult
    extraction_result: Any
    markdown: str
    structured_blocks: list
    chunks: list
    chunks_requested: bool
    enriched_metadata: Any
    links: Any
    security_result: dict
    token_estimator: ITokenEstimator
    hasher: Any
    scorer: Any
    matched_domain_policy: DomainPolicy | None
    operational_flags: list[str]
    domain_rule_stats: dict
    extra_patterns: Sequence[PatternSpec]
    plugins_loaded: int
    stage_timings: dict[str, float]
    policy_engine: PolicyEngine


@dataclass(frozen=True)
class _DocumentComputedFields:
    content_hash: str
    structural_hash: str
    token_count: int
    token_savings: dict[str, Any]
    hostname: str


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
    context: _SafeDocumentAssemblyContext,
    computed: _DocumentComputedFields,
) -> dict:
    """Build content-analysis fields: sizes, hashes, security scores, and risk level."""
    fetch_result = context.fetch_result
    extraction_result = context.extraction_result
    security_result = context.security_result
    return {
        "token_savings": computed.token_savings,
        "risk_level": context.scorer.get_risk_level(security_result["injection_score"]),
        "structural_hash": computed.structural_hash,
        "original_size_bytes": len(fetch_result.html.encode("utf-8")),
        "cleaned_size_bytes": len(context.markdown.encode("utf-8")),
        "pattern_matches": security_result["pattern_matches"],
        "imperative_density": security_result["imperative_density"],
        "hidden_content_detected": extraction_result.removed_hidden > 0,
        "advanced_security": context.config.advanced_security,
        "security_scan_method": security_result["scan_method"],
    }


def _build_pipeline_config_metadata(
    context: _SafeDocumentAssemblyContext,
    output_selection: DocumentOutputSelection,
) -> dict:
    """Build pipeline-configuration fields: output formats, costs, and operational flags."""
    config = context.config
    fetch_result = context.fetch_result
    return {
        "custom_patterns_count": len(context.extra_patterns),
        "plugins_loaded": context.plugins_loaded,
        "output_profile": config.output_profile,
        "output_formats": output_selection.output_formats,
        "emitted_output_formats": output_selection.emitted_output_formats,
        "chunking_strategy": config.chunking_strategy,
        "cost_units_used": fetch_result.metadata.get("cost_units_used", 0),
        "render_cost_budget": fetch_result.metadata.get("render_cost_budget"),
        "operational_flags": context.operational_flags,
        "domain_rule_stats": context.domain_rule_stats,
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
    context: _SafeDocumentAssemblyContext,
    computed: _DocumentComputedFields,
    output_selection: DocumentOutputSelection,
) -> dict:
    """Build the metadata dict that accompanies the SafeDocument."""
    metadata: dict = {
        **_build_fetch_metadata(
            context.fetch_result,
            context.extraction_result,
            computed.hostname,
            context.config,
        ),
        **_build_content_analysis_metadata(context, computed),
        **_build_pipeline_config_metadata(context, output_selection),
    }
    _apply_language_metadata(metadata, context.enriched_metadata, context.config)
    _apply_domain_policy_metadata(metadata, context.matched_domain_policy)
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
    context: _SafeDocumentAssemblyContext,
    computed: _DocumentComputedFields,
    metadata: dict,
    policy_action: str,
    security_explanation_payload: dict | None,
) -> SafeDocument:
    """Construct and return the SafeDocument from assembled pipeline outputs."""
    config = context.config
    extraction_result = context.extraction_result
    fetch_result = context.fetch_result
    security_result = context.security_result
    removed_elements = {
        "tags": extraction_result.removed_tags,
        "hidden_elements": extraction_result.removed_hidden,
    }
    injection_score = float(security_result["injection_score"])
    if math.isnan(injection_score):
        injection_score = 1.0
    document = SafeDocument(
        markdown=context.markdown,
        metadata=metadata,
        token_estimate=computed.token_count,
        content_hash=computed.content_hash,
        injection_score=injection_score,
        flags=security_result["flags"],
        removed_elements=removed_elements,
        screenshot_path=fetch_result.metadata.get("screenshot_path"),
        enriched_metadata=context.enriched_metadata,
        links=context.links,
        nova_score=security_result.get("nova_score"),
        nova_details=security_result.get("nova_details"),
        structured_blocks=(
            blocks_to_dicts(context.structured_blocks) if config.extract_blocks else None
        ),
        chunks=chunks_to_dicts(context.chunks) if context.chunks_requested else None,
        security_explanation=security_explanation_payload,
        observability=(
            {
                "stage_timings_ms": context.stage_timings,
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


def _compute_document_fields(
    context: _SafeDocumentAssemblyContext,
) -> _DocumentComputedFields:
    content_hash = timed_stage_with_snapshot(
        context.stage_timings,
        "hash_content",
        lambda: context.hasher.hash_content(context.markdown),
    )
    structural_hash = timed_stage_with_snapshot(
        context.stage_timings,
        "hash_structural",
        lambda: context.hasher.hash_structural(context.markdown),
    )
    token_count = timed_stage_with_snapshot(
        context.stage_timings,
        "tokens",
        lambda: context.token_estimator.estimate(context.markdown),
    )
    token_savings = context.token_estimator.estimate_savings(
        context.fetch_result.html,
        context.markdown,
    )
    final_url = context.fetch_result.final_url or context.fetch_result.url or ""
    hostname = normalize_hostname(urlsplit(final_url).hostname or "")
    return _DocumentComputedFields(
        content_hash=content_hash,
        structural_hash=structural_hash,
        token_count=token_count,
        token_savings=token_savings,
        hostname=hostname,
    )


def _build_output_selection(
    context: _SafeDocumentAssemblyContext,
) -> DocumentOutputSelection:
    return build_output_selection(
        context.config,
        context.structured_blocks,
        context.chunks,
        context.enriched_metadata,
        context.security_result,
    )


def _build_safe_document(context: _SafeDocumentAssemblyContext) -> SafeDocument:
    """Hash, assemble metadata, apply policy, and construct the SafeDocument."""
    computed = _compute_document_fields(context)
    output_selection = _build_output_selection(context)
    metadata = _assemble_document_metadata(context, computed, output_selection)
    policy_action = _apply_policy_decision(
        context.security_result,
        context.policy_engine,
        context.config,
        metadata,
    )
    document = _construct_safe_document_instance(
        context,
        computed,
        metadata,
        policy_action,
        output_selection.security_explanation_payload,
    )
    if policy_action == "block":
        raise PolicyBlockedError(
            f"Policy '{context.config.policy_name}' blocked content for {context.fetch_result.url}",
            document=document,
        )
    return document
