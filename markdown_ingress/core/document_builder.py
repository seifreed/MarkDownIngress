"""Helpers for turning fetched HTML into the final SafeDocument."""

from __future__ import annotations

import copy
import logging
import math
import os
import re
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import urlsplit

from markdown_ingress.config_models import DomainPolicy, IngestConfig
from markdown_ingress.core.domain_rules import apply_domain_html_rules
from markdown_ingress.core.hashing import Hasher
from markdown_ingress.core.ingest_stats import record_policy_action, timed_stage_with_snapshot
from markdown_ingress.core.interfaces import IExtractor, IMarkdownConverter, ITokenEstimator
from markdown_ingress.core.link_analyzer import LinkAnalyzer
from markdown_ingress.core.metadata_extractor import MetadataExtractor
from markdown_ingress.core.plugin import PluginLoader
from markdown_ingress.core.policy import PolicyBlockedError, PolicyEngine
from markdown_ingress.core.scoring import Scorer
from markdown_ingress.core.security import InjectionPattern, SecurityAnalyzer
from markdown_ingress.core.security_engine import SecurityEngine
from markdown_ingress.core.ssrf import normalize_hostname
from markdown_ingress.core.structured import (
    ChunkBuilder,
    HTMLStructureExtractor,
    blocks_to_dicts,
    chunks_to_dicts,
)
from markdown_ingress.models import FetchResult, InjectionAnalysis, SafeDocument

_logger = logging.getLogger(__name__)

_extractor_factory: Callable[[bool], IExtractor] | None = None
_md_converter_factory: Callable[[], IMarkdownConverter] | None = None
_token_estimator_factory: Callable[[str], ITokenEstimator] | None = None
PatternSpec = str | tuple[str, float]


def register_document_builder_factories(
    extractor_factory: Callable[[bool], IExtractor],
    md_converter_factory: Callable[[], IMarkdownConverter],
    token_estimator_factory: Callable[[str], ITokenEstimator],
) -> None:
    global _extractor_factory, _md_converter_factory, _token_estimator_factory
    _extractor_factory = extractor_factory
    _md_converter_factory = md_converter_factory
    _token_estimator_factory = token_estimator_factory


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    """Return unique values while preserving first-seen order."""
    return list(dict.fromkeys(values))


def _merge_pattern_matches(*groups: list[dict]) -> list[dict]:
    """Merge pattern matches while deduplicating by description."""
    merged: dict[str, dict] = {}
    _anon_counter = 0
    for group in groups:
        for match in group:
            if match.get("pattern") is not None:
                key = str(match.get("pattern"))
            else:
                _anon_counter += 1
                key = f"_anon_{_anon_counter}_{match.get('description', '')}"
            existing = merged.get(key)
            if existing is None:
                merged[key] = {
                    "pattern": match.get("pattern"),
                    "weight": match.get("weight"),
                    "occurrences": int(match.get("occurrences") or 0),
                    "samples": list(match.get("samples", [])),
                }
                continue

            existing["occurrences"] = int(existing.get("occurrences") or 0) + int(
                match.get("occurrences") or 0
            )
            existing["weight"] = max(
                float(existing.get("weight") or 0.0), float(match.get("weight") or 0.0)
            )
            samples = list(existing.get("samples", []))
            for sample in match.get("samples", []):
                if sample not in samples:
                    samples.append(sample)
            existing["samples"] = samples

    # Truncate samples after all merging is complete to avoid order-dependent results
    for item in merged.values():
        item["samples"] = item["samples"][:3]

    return list(merged.values())


def build_policy_engine(
    policy_name: str, matched_domain_policy: DomainPolicy | None
) -> PolicyEngine:
    """Create the policy engine, applying optional domain-level threshold overrides."""
    if matched_domain_policy is None:
        return PolicyEngine.from_name(policy_name)
    if (
        matched_domain_policy.block_threshold is None
        and matched_domain_policy.warn_threshold is None
    ):
        return PolicyEngine.from_name(policy_name)

    base = PolicyEngine.from_name(policy_name).policy
    custom = copy.deepcopy(base)
    if matched_domain_policy.block_threshold is not None:
        custom.block_threshold = matched_domain_policy.block_threshold
    if matched_domain_policy.warn_threshold is not None:
        custom.warn_threshold = matched_domain_policy.warn_threshold
    # Keep a warning band below the block threshold when policy values conflict.
    # If warn_threshold is >= block_threshold, clamp it just below the block level
    # so warning-only scores still exist instead of collapsing straight to block.
    if custom.warn_threshold >= custom.block_threshold:
        if custom.block_threshold > 0.0:
            adjusted = custom.block_threshold - 0.01
            if adjusted < 0.0:
                adjusted = custom.block_threshold * 0.9
            if adjusted < 0.0:
                adjusted = 0.0
            custom.warn_threshold = adjusted
        else:
            # block_threshold=0.0 means block everything; warn_threshold collapses to match.
            custom.warn_threshold = 0.0
    return PolicyEngine(policy=custom)


def _resolve_pipeline_dependencies(orchestrator, config: IngestConfig):
    """Return (extractor, md_converter, hasher, token_estimator, scorer) for the pipeline."""
    if orchestrator.extractor is None:
        if _extractor_factory is None:
            raise RuntimeError(
                "No extractor factory registered — call register_document_builder_factories()."
            )
        extractor: IExtractor = _extractor_factory(config.strict)
    else:
        extractor = orchestrator.extractor
    if orchestrator.md_converter is None:
        if _md_converter_factory is None:
            raise RuntimeError("No md_converter factory registered.")
        md_converter: IMarkdownConverter = _md_converter_factory()
    else:
        md_converter = orchestrator.md_converter
    hasher = orchestrator.hasher or Hasher()
    if orchestrator.token_estimator is None:
        if _token_estimator_factory is None:
            raise RuntimeError("No token_estimator factory registered.")
        token_estimator: ITokenEstimator = _token_estimator_factory(config.model)
    else:
        token_estimator = orchestrator.token_estimator
    scorer = orchestrator.scorer or Scorer()
    return extractor, md_converter, hasher, token_estimator, scorer


def _extract_and_apply_domain_rules(
    extractor: IExtractor,
    fetch_result: FetchResult,
    matched_domain_policy: DomainPolicy | None,
    operational_flags: list[str],
    stage_timings: dict[str, float],
):
    """Extract HTML content and apply domain-level filtering rules."""
    extraction_result = timed_stage_with_snapshot(
        stage_timings,
        "extract",
        lambda: extractor.extract(fetch_result.html, fetch_result.url),
    )
    filtered_html, domain_rule_stats = apply_domain_html_rules(
        extraction_result.html, matched_domain_policy
    )
    if filtered_html != extraction_result.html:
        extraction_result.html = filtered_html
        re_extracted = timed_stage_with_snapshot(
            stage_timings,
            "domain_rules_text",
            lambda: extractor.extract(filtered_html, fetch_result.url),
        )
        extraction_result.text_content = re_extracted.text_content
        extraction_result.title = re_extracted.title
        extraction_result.author = re_extracted.author
        extraction_result.removed_hidden += re_extracted.removed_hidden
        for tag, count in re_extracted.removed_tags.items():
            extraction_result.removed_tags[tag] = extraction_result.removed_tags.get(tag, 0) + count
        if any(domain_rule_stats.values()):
            operational_flags.append("domain_rules_applied")
    return extraction_result, domain_rule_stats


def _enrich_metadata_and_links(
    orchestrator,
    config: IngestConfig,
    fetch_result: FetchResult,
    extraction_result,
    document_url: str,
    stage_timings: dict[str, float],
):
    """Optionally extract enriched page metadata and outbound links."""
    enriched_metadata = None
    if config.extract_metadata:
        metadata_extractor = orchestrator.metadata_extractor or MetadataExtractor()
        enriched_metadata = timed_stage_with_snapshot(
            stage_timings,
            "metadata",
            lambda: metadata_extractor.extract(
                fetch_result.html,
                document_url,
                detect_language=config.detect_language,
                normalize_multilingual=config.normalize_multilingual,
            ),
        )
    links = None
    if config.extract_links:
        link_analyzer = orchestrator.link_analyzer or LinkAnalyzer()
        links = timed_stage_with_snapshot(
            stage_timings,
            "links",
            lambda: link_analyzer.analyze(extraction_result.html, document_url),
        )
    return enriched_metadata, links


def _extract_structure(
    structure_extractor,
    chunk_builder,
    config: IngestConfig,
    extraction_result,
    stage_timings: dict[str, float],
):
    """Conditionally extract structural blocks and semantic chunks from the HTML."""
    chunking_explicit = "chunking_strategy" in config.explicit_keys()
    chunks_requested = config.chunking_strategy != "none" and (
        config.extract_blocks or "chunks" in config.output_formats or chunking_explicit
    )
    extracted_blocks = []
    if config.extract_blocks or chunks_requested:
        extracted_blocks = timed_stage_with_snapshot(
            stage_timings,
            "blocks",
            lambda: structure_extractor.extract(extraction_result.html),
        )
    chunks = []
    if extracted_blocks and chunks_requested:
        chunks = timed_stage_with_snapshot(
            stage_timings,
            "chunking",
            lambda: chunk_builder.build(
                extracted_blocks,
                strategy=config.chunking_strategy,
                chunk_size=config.chunk_size,
                chunk_overlap=config.chunk_overlap,
            ),
        )
    structured_blocks = extracted_blocks if config.extract_blocks else []
    return structured_blocks, chunks, chunks_requested


def _run_content_pipeline(
    extractor: IExtractor,
    md_converter: IMarkdownConverter,
    structure_extractor,
    chunk_builder,
    orchestrator,
    fetch_result: FetchResult,
    config: IngestConfig,
    matched_domain_policy: DomainPolicy | None,
    operational_flags: list[str],
    stage_timings: dict[str, float],
    document_url: str,
):
    """Extract, normalise, and structure the fetched HTML. Returns pipeline outputs."""
    extraction_result, domain_rule_stats = _extract_and_apply_domain_rules(
        extractor, fetch_result, matched_domain_policy, operational_flags, stage_timings
    )
    enriched_metadata, links = _enrich_metadata_and_links(
        orchestrator, config, fetch_result, extraction_result, document_url, stage_timings
    )
    markdown = timed_stage_with_snapshot(
        stage_timings, "markdown", lambda: md_converter.convert(extraction_result.html)
    )
    structured_blocks, chunks, chunks_requested = _extract_structure(
        structure_extractor, chunk_builder, config, extraction_result, stage_timings
    )
    return (
        extraction_result,
        markdown,
        structured_blocks,
        chunks,
        enriched_metadata,
        links,
        operational_flags,
        domain_rule_stats,
        chunks_requested,
    )


def _create_custom_patterns(extra_patterns: Sequence[PatternSpec]) -> list[InjectionPattern]:
    """Build InjectionPattern objects from pattern strings or (pattern, weight) tuples."""
    patterns = []
    for i, item in enumerate(extra_patterns):
        if isinstance(item, tuple):
            p, weight = item
        else:
            p = item
            weight = 0.5
        try:
            re.compile(p)
        except re.error as exc:
            raise ValueError(f"Invalid custom pattern at index {i}: {exc}") from exc
        patterns.append(
            InjectionPattern(pattern=p, weight=weight, description=f"custom_pattern_{i + 1}")
        )
    return patterns


def _run_and_merge_custom_analysis(
    custom_defs: list[InjectionPattern],
    security_result: dict,
    extraction_result,
    security_metadata: dict,
    security_engine: SecurityEngine,
    policy_engine: PolicyEngine,
    config: IngestConfig,
) -> dict:
    """Run custom-pattern-only analysis and merge results into the base security result."""
    # Only include custom patterns — default patterns are already in security_result.
    extended_analyzer = SecurityAnalyzer(strict=config.strict)
    extended_analyzer.INJECTION_PATTERNS = tuple(custom_defs)
    extended_analysis = extended_analyzer.analyze(
        extraction_result.text_content,
        hidden_content_detected=security_metadata["hidden_elements_count"] > 0,
    )
    security_result["injection_score"] = max(
        security_result["injection_score"], extended_analysis.score
    )
    security_result["flags"] = _dedupe_preserving_order(
        list(security_result["flags"]) + list(extended_analysis.flags)
    )
    security_result["pattern_matches"] = _merge_pattern_matches(
        security_result["pattern_matches"],
        extended_analysis.pattern_matches,
    )
    if len(security_result["pattern_matches"]) > 3:
        security_result["flags"] = _dedupe_preserving_order(
            list(security_result["flags"]) + ["multiple_injection_attempts"]
        )
    # Do NOT override imperative_density — it duplicates base computation on same text.
    block_threshold, warn_threshold = security_engine.effective_thresholds(
        policy_engine.policy.block_threshold,
        policy_engine.policy.warn_threshold,
        strict=config.strict,
    )
    security_result["explanation"] = security_engine._build_explanation(
        final_score=security_result["injection_score"],
        basic_analysis=InjectionAnalysis(
            score=security_result["injection_score"],
            flags=list(security_result["flags"]),
            pattern_matches=list(security_result["pattern_matches"]),
            hidden_content_detected=security_metadata["hidden_elements_count"] > 0,
            imperative_density=security_result["imperative_density"],
        ),
        nova_details=security_result.get("nova_details") or {},
        scan_method=security_result["scan_method"],
        block_threshold=block_threshold,
        warn_threshold=warn_threshold,
    )
    return security_result


def _apply_custom_pattern_analysis(
    extra_patterns: Sequence[PatternSpec],
    security_result: dict,
    extraction_result,
    security_metadata: dict,
    security_engine: SecurityEngine,
    policy_engine: PolicyEngine,
    config: IngestConfig,
) -> dict:
    """Extend security_result with custom/plugin patterns and rebuild explanation."""
    custom_defs = _create_custom_patterns(extra_patterns)
    return _run_and_merge_custom_analysis(
        custom_defs,
        security_result,
        extraction_result,
        security_metadata,
        security_engine,
        policy_engine,
        config,
    )


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
    for fmt in output_formats + ["markdown", "blocks", "chunks", "metadata", "security"]:
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
            list(security_result["flags"]) + ["policy_block"]
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


def _run_security_analysis(
    config: IngestConfig,
    extraction_result,
    security_metadata: dict,
    stage_timings: dict[str, float],
    matched_domain_policy: DomainPolicy | None,
) -> tuple:
    """Run security and policy analysis on extracted content."""
    security_engine = SecurityEngine(
        strict=config.strict,
        advanced_security=config.advanced_security,
        use_llm=config.use_llm,
    )
    policy_engine = build_policy_engine(config.policy_name, matched_domain_policy)
    security_result = timed_stage_with_snapshot(
        stage_timings,
        "security",
        lambda: security_engine.analyze(
            extraction_result.text_content,
            security_metadata,
            block_threshold=policy_engine.policy.block_threshold,
            warn_threshold=policy_engine.policy.warn_threshold,
        ),
    )
    return security_engine, policy_engine, security_result


def _handle_plugin_unload_errors(
    plugin_loader: PluginLoader | None,
    document: SafeDocument | None,
    fetch_result: FetchResult,
) -> None:
    """Unload plugins and attach any unload errors to the document or fetch metadata."""
    if plugin_loader is None:
        return
    unload_errors: list[str] = []
    for plugin_name in list(plugin_loader.plugins):
        try:
            plugin_loader.unload_plugin(plugin_name)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:  # pragma: no cover - defensive logging path
            unload_errors.append(f"{plugin_name}: {type(exc).__name__}: {exc}")
    if unload_errors:
        for message in unload_errors:
            _logger.error("Plugin unload failed after request completion: %s", message)
        if document is not None:
            document.metadata.setdefault("warnings", []).extend(unload_errors)
        else:
            fetch_result.metadata.setdefault("warnings", []).extend(unload_errors)


def _cleanup_screenshot_on_failure(
    document: SafeDocument | None,
    fetch_result: FetchResult,
) -> None:
    """Remove a temporary screenshot file when document creation failed."""
    if document is None and fetch_result.metadata.get("screenshot_temp"):
        path = fetch_result.metadata.get("screenshot_path")
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def process_fetched_content(
    orchestrator,
    fetch_result: FetchResult,
    config: IngestConfig,
    matched_domain_policy: DomainPolicy | None = None,
    operational_flags: list[str] | None = None,
) -> SafeDocument:
    """Transform already-fetched HTML into the final SafeDocument."""
    plugin_loader: PluginLoader | None = None
    document: SafeDocument | None = None

    extractor, md_converter, hasher, token_estimator, scorer = _resolve_pipeline_dependencies(
        orchestrator, config
    )
    structure_extractor = HTMLStructureExtractor(hasher=hasher)
    chunk_builder = ChunkBuilder(hasher=hasher, token_estimator=token_estimator)
    stage_timings: dict[str, float] = dict(fetch_result.metadata.get("stage_timings_ms", {}))
    operational_flags = list(operational_flags or [])
    document_url = fetch_result.final_url or fetch_result.url

    (
        extraction_result,
        markdown,
        structured_blocks,
        chunks,
        enriched_metadata,
        links,
        operational_flags,
        domain_rule_stats,
        chunks_requested,
    ) = _run_content_pipeline(
        extractor,
        md_converter,
        structure_extractor,
        chunk_builder,
        orchestrator,
        fetch_result,
        config,
        matched_domain_policy,
        operational_flags,
        stage_timings,
        document_url,
    )

    security_metadata = {"hidden_elements_count": extraction_result.removed_hidden}
    security_engine, policy_engine, security_result = _run_security_analysis(
        config, extraction_result, security_metadata, stage_timings, matched_domain_policy
    )

    extra_patterns: list[str | tuple[str, float]] = list(config.custom_patterns)
    plugins_loaded = 0
    try:
        if config.plugin_dirs:
            plugin_loader = PluginLoader()
            for plugin_dir in config.plugin_dirs:
                plugins_loaded += plugin_loader.load_from_directory(plugin_dir)
            extra_patterns.extend(plugin_loader.get_all_patterns())

        if extra_patterns:
            security_result = _apply_custom_pattern_analysis(
                extra_patterns,
                security_result,
                extraction_result,
                security_metadata,
                security_engine,
                policy_engine,
                config,
            )

        try:
            document = _build_safe_document(
                config=config,
                fetch_result=fetch_result,
                extraction_result=extraction_result,
                markdown=markdown,
                structured_blocks=structured_blocks,
                chunks=chunks,
                chunks_requested=chunks_requested,
                enriched_metadata=enriched_metadata,
                links=links,
                security_result=security_result,
                token_estimator=token_estimator,
                hasher=hasher,
                scorer=scorer,
                matched_domain_policy=matched_domain_policy,
                operational_flags=operational_flags,
                domain_rule_stats=domain_rule_stats,
                extra_patterns=extra_patterns,
                plugins_loaded=plugins_loaded,
                stage_timings=stage_timings,
                policy_engine=policy_engine,
            )
        except PolicyBlockedError as exc:
            if exc.document is not None:
                document = exc.document
            raise
        return document
    finally:
        _handle_plugin_unload_errors(plugin_loader, document, fetch_result)
        _cleanup_screenshot_on_failure(document, fetch_result)
