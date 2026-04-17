"""Helpers for turning fetched HTML into the final SafeDocument."""

from __future__ import annotations

import copy
import logging
import time
from urllib.parse import urlsplit

from markdown_ingress.config_models import DomainPolicy, IngestConfig
from markdown_ingress.core.domain_rules import apply_domain_html_rules
from markdown_ingress.core.extractor import Extractor
from markdown_ingress.core.hashing import Hasher
from markdown_ingress.core.ingest_stats import record_policy_action, record_stage_timing
from markdown_ingress.core.link_analyzer import LinkAnalyzer
from markdown_ingress.core.markdown import MarkdownConverter
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
from markdown_ingress.core.tokens import TokenEstimator
from markdown_ingress.models import FetchResult, InjectionAnalysis, SafeDocument

_logger = logging.getLogger(__name__)


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    """Return unique values while preserving first-seen order."""
    return list(dict.fromkeys(values))


def _merge_pattern_matches(*groups: list[dict]) -> list[dict]:
    """Merge pattern matches while deduplicating by description."""
    merged: dict[str, dict] = {}
    for group in groups:
        for match in group:
            key = (
                str(match.get("pattern"))
                if match.get("pattern") is not None
                else f"_anon_{match.get('description', '')}"
            )
            existing = merged.get(key)
            if existing is None:
                merged[key] = {
                    "pattern": match.get("pattern"),
                    "weight": match.get("weight"),
                    "occurrences": int(match.get("occurrences", 0)),
                    "samples": list(match.get("samples", [])),
                }
                continue

            existing["occurrences"] = int(existing.get("occurrences", 0)) + int(
                match.get("occurrences", 0)
            )
            existing["weight"] = max(
                float(existing.get("weight", 0.0)), float(match.get("weight", 0.0))
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


def timed_stage_with_snapshot(stage_timings: dict[str, float], stage: str, fn):
    """Execute a stage and record both local and aggregate timing snapshots."""
    started = time.perf_counter()
    result = fn()
    duration_ms = (time.perf_counter() - started) * 1000.0
    stage_timings[stage] = duration_ms
    record_stage_timing(stage, duration_ms)
    return result


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
                adjusted = 0.0
            custom.warn_threshold = adjusted
        else:
            # block_threshold=0.0 means block everything; warn_threshold collapses to match.
            custom.warn_threshold = 0.0
    return PolicyEngine(policy=custom)


def process_fetched_content(
    orchestrator,
    fetch_result: FetchResult,
    config: IngestConfig,
    matched_domain_policy: DomainPolicy | None = None,
    operational_flags: list[str] | None = None,
) -> SafeDocument:
    """Transform already-fetched HTML into the final SafeDocument."""
    plugin_loader: PluginLoader | None = None
    extractor = orchestrator.extractor or Extractor(strict=config.strict)
    md_converter = orchestrator.md_converter or MarkdownConverter()
    hasher = orchestrator.hasher or Hasher()
    token_estimator = orchestrator.token_estimator or TokenEstimator(model=config.model)
    scorer = orchestrator.scorer or Scorer()
    structure_extractor = HTMLStructureExtractor(hasher=hasher)
    chunk_builder = ChunkBuilder(hasher=hasher, token_estimator=token_estimator)
    stage_timings: dict[str, float] = dict(fetch_result.metadata.get("stage_timings_ms", {}))
    operational_flags = list(operational_flags or [])
    document_url = fetch_result.final_url or fetch_result.url

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
            lambda: extractor.extract(
                filtered_html,
                fetch_result.url,
            ),
        )
        extraction_result.text_content = re_extracted.text_content
        extraction_result.removed_hidden = re_extracted.removed_hidden
        extraction_result.removed_tags = re_extracted.removed_tags
        if any(domain_rule_stats.values()):
            operational_flags.append("domain_rules_applied")

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

    markdown = timed_stage_with_snapshot(
        stage_timings, "markdown", lambda: md_converter.convert(extraction_result.html)
    )

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

    security_metadata = {"hidden_elements_count": extraction_result.removed_hidden}
    security_engine = SecurityEngine(
        strict=config.strict,
        advanced_security=config.advanced_security,
        use_llm=config.use_llm,
    )
    _policy_engine_for_thresholds = build_policy_engine(config.policy_name, matched_domain_policy)
    security_result = timed_stage_with_snapshot(
        stage_timings,
        "security",
        lambda: security_engine.analyze(
            extraction_result.text_content,
            security_metadata,
            block_threshold=_policy_engine_for_thresholds.policy.block_threshold,
            warn_threshold=_policy_engine_for_thresholds.policy.warn_threshold,
        ),
    )

    extra_patterns = list(config.custom_patterns)
    plugins_loaded = 0
    plugin_unload_errors: list[str] = []
    # BUG FIX: Initialize document to None to prevent NameError in finally block
    # if an exception occurs before SafeDocument is created
    document: SafeDocument | None = None
    try:
        if config.plugin_dirs:
            plugin_loader = PluginLoader()
            for plugin_dir in config.plugin_dirs:
                plugins_loaded += plugin_loader.load_from_directory(plugin_dir)
            extra_patterns.extend(plugin_loader.get_all_patterns())

        if extra_patterns:
            custom_defs = [
                InjectionPattern(
                    pattern=pattern,
                    weight=0.5,
                    description=f"custom_pattern_{idx + 1}",
                )
                for idx, pattern in enumerate(extra_patterns)
            ]

            # Only include custom patterns — default patterns are already in
            # security_result from the initial security_engine.analyze() call.
            extended_analyzer = SecurityAnalyzer(strict=config.strict)
            extended_analyzer.INJECTION_PATTERNS = custom_defs
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
            # Do NOT override imperative_density from extended_analysis — it duplicates
            # the base analysis computation on the same text, causing false inflation.
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
                block_threshold=_policy_engine_for_thresholds.policy.block_threshold,
                warn_threshold=_policy_engine_for_thresholds.policy.warn_threshold,
            )

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

        hostname = normalize_hostname(
            urlsplit(fetch_result.final_url).hostname or urlsplit(fetch_result.url).hostname or ""
        )
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
        metadata = {
            "url": fetch_result.url,
            "final_url": fetch_result.final_url,
            "hostname": hostname,
            "title": extraction_result.title,
            "fetch_time_ms": fetch_result.timing_ms,
            "status_code": fetch_result.status_code,
            "model": config.model,
            "mode": fetch_result.metadata.get("effective_mode", config.mode),
            "strict": config.strict,
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
        if enriched_metadata:
            metadata["language"] = enriched_metadata.get("language")
            metadata["language_source"] = enriched_metadata.get("language_source")
            metadata["language_confidence"] = enriched_metadata.get("language_confidence")
            if not config.detect_language:
                metadata.pop("language", None)
                metadata.pop("language_source", None)
                metadata.pop("language_confidence", None)
        if matched_domain_policy is not None:
            metadata["domain_policy"] = {
                "domain": matched_domain_policy.domain,
                "notes": matched_domain_policy.notes,
            }

        policy_engine = _policy_engine_for_thresholds
        policy_action = None
        security_explanation = security_result.get("explanation")
        if isinstance(security_explanation, dict):
            recommendation = security_explanation.get("recommendation")
            if recommendation in {"allow", "warn", "block"}:
                policy_action = recommendation
        if policy_action is None:
            policy_action = policy_engine.get_action(security_result["injection_score"])
        record_policy_action(policy_action)
        metadata["policy"] = config.policy_name
        metadata["policy_action"] = policy_action
        if policy_action == "block":
            security_result["flags"] = _dedupe_preserving_order(
                list(security_result["flags"]) + ["policy_block"]
            )

        removed_elements = {
            "tags": extraction_result.removed_tags,
            "hidden_elements": extraction_result.removed_hidden,
        }
        screenshot_path = fetch_result.metadata.get("screenshot_path")
        document = SafeDocument(
            markdown=markdown,
            metadata=metadata,
            token_estimate=token_count,
            content_hash=content_hash,
            injection_score=security_result["injection_score"],
            flags=security_result["flags"],
            removed_elements=removed_elements,
            screenshot_path=screenshot_path,
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
        # Expose stage timings in metadata as a reference to the observability
        # dict to avoid data duplication while keeping backward compatibility.
        if config.include_observability and document.observability:
            document.metadata["stage_timings_ms"] = document.observability["stage_timings_ms"]
        if policy_action == "block":
            raise PolicyBlockedError(
                f"Policy '{config.policy_name}' blocked content for {fetch_result.url}",
                document=document,
            )
        return document
    finally:
        if plugin_loader is not None:
            for plugin_name in list(plugin_loader.plugins):
                try:
                    plugin_loader.unload_plugin(plugin_name)
                except (KeyboardInterrupt, SystemExit):
                    raise  # Re-raise system exceptions immediately
                except Exception as exc:  # pragma: no cover - defensive logging path
                    plugin_unload_errors.append(f"{plugin_name}: {type(exc).__name__}: {exc}")
            if plugin_unload_errors:
                # BUG FIX: Use ERROR level for visibility in production logs
                for message in plugin_unload_errors:
                    _logger.error("Plugin unload failed after request completion: %s", message)
                # BUG FIX: Expose plugin unload errors in document metadata for observability
                if document is not None:
                    document.metadata.setdefault("warnings", []).extend(plugin_unload_errors)
                else:
                    # Keep request-scoped visibility when no document is created.
                    fetch_result.metadata.setdefault("warnings", []).extend(plugin_unload_errors)

        # BUG FIX: Do not clean up temp screenshots on successful document creation.
        # Clean only when document creation failed (document is None), because
        # consumers still need a stable screenshot_path on successful/blocked
        # responses.
        if document is None and fetch_result.metadata.get("screenshot_temp"):
            from markdown_ingress.core.renderer import Renderer

            Renderer.cleanup_screenshot(fetch_result.metadata.get("screenshot_path"))
