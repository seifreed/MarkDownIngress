"""Custom security pattern helpers for document building."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from markdown_ingress.config_models import IngestConfig
from markdown_ingress.core import security as security_module
from markdown_ingress.core.policy import PolicyEngine
from markdown_ingress.core.security_engine import SecurityEngine, SecurityExplanationContext
from markdown_ingress.models import ExtractionResult, InjectionAnalysis

PatternSpec = str | tuple[str, float]


@dataclass(frozen=True)
class CustomPatternAnalysisContext:
    """Dependencies and mutable result used by custom security pattern analysis."""

    security_result: dict
    extraction_result: ExtractionResult
    security_metadata: dict
    security_engine: SecurityEngine
    policy_engine: PolicyEngine
    config: IngestConfig


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


def _create_custom_patterns(
    extra_patterns: Sequence[PatternSpec],
) -> list[security_module.InjectionPattern]:
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
            security_module.InjectionPattern(
                pattern=p,
                weight=weight,
                description=f"custom_pattern_{i + 1}",
            )
        )
    return patterns


def _run_and_merge_custom_analysis(
    custom_defs: list[security_module.InjectionPattern],
    context: CustomPatternAnalysisContext,
) -> dict:
    """Run custom-pattern-only analysis and merge results into the base security result."""
    # Only include custom patterns: default patterns are already in security_result.
    security_result = context.security_result
    extended_analyzer = security_module.SecurityAnalyzer(strict=context.config.strict)
    extended_analyzer.INJECTION_PATTERNS = tuple(custom_defs)
    extended_analysis = extended_analyzer.analyze(
        context.extraction_result.text_content,
        hidden_content_detected=context.security_metadata["hidden_elements_count"] > 0,
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
            [*list(security_result["flags"]), "multiple_injection_attempts"]
        )
    # Do NOT override imperative_density: it duplicates base computation on same text.
    block_threshold, warn_threshold = context.security_engine.effective_thresholds(
        context.policy_engine.policy.block_threshold,
        context.policy_engine.policy.warn_threshold,
        strict=context.config.strict,
    )
    security_result["explanation"] = context.security_engine._build_explanation(
        SecurityExplanationContext(
            final_score=security_result["injection_score"],
            basic_analysis=InjectionAnalysis(
                score=security_result["injection_score"],
                flags=list(security_result["flags"]),
                pattern_matches=list(security_result["pattern_matches"]),
                hidden_content_detected=context.security_metadata["hidden_elements_count"] > 0,
                imperative_density=security_result["imperative_density"],
            ),
            nova_details=security_result.get("nova_details") or {},
            scan_method=security_result["scan_method"],
            block_threshold=block_threshold,
            warn_threshold=warn_threshold,
        )
    )
    return security_result


def _apply_custom_pattern_analysis(
    extra_patterns: Sequence[PatternSpec],
    context: CustomPatternAnalysisContext,
) -> dict:
    """Extend security_result with custom/plugin patterns and rebuild explanation."""
    custom_defs = _create_custom_patterns(extra_patterns)
    return _run_and_merge_custom_analysis(custom_defs, context)
