"""Explainability payloads for security scan results."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SecurityExplanationContext:
    final_score: float
    basic_analysis: Any
    nova_details: dict
    scan_method: str
    block_threshold: float = 0.7
    warn_threshold: float = 0.4


def build_security_explanation(context: SecurityExplanationContext) -> dict:
    """Produce actionable explainability data for downstream consumers."""
    triggers = []
    for match in context.basic_analysis.pattern_matches:
        samples = []
        for sample in match.get("samples", []):
            if isinstance(sample, tuple):
                samples.append(" ".join(str(part) for part in sample if part))
            else:
                samples.append(str(sample))
        triggers.append(
            {
                "source": "pattern",
                "name": match.get("pattern"),
                "weight": match.get("weight"),
                "occurrences": match.get("occurrences"),
                "samples": samples,
            }
        )

    triggers.extend(
        {"source": "nova", "name": rule} for rule in context.nova_details.get("matched_rules", [])
    )

    if math.isnan(context.final_score):
        recommendation = "block"
    else:
        recommendation = "allow"
        if context.final_score >= context.block_threshold:
            recommendation = "block"
        elif context.final_score >= context.warn_threshold:
            recommendation = "warn"

    return {
        "scan_method": context.scan_method,
        "recommendation": recommendation,
        "summary": (
            f"Detected {len(context.basic_analysis.pattern_matches)} heuristic pattern groups"
            f" with imperative density {context.basic_analysis.imperative_density:.3f}."
        ),
        "triggers": triggers,
        "hidden_content_detected": context.basic_analysis.hidden_content_detected,
    }
