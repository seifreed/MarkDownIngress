"""Prompt-injection score calculation."""

from __future__ import annotations

import math


def calculate_injection_score(
    pattern_matches: list[dict],
    imperative_density: float,
    *,
    hidden_content_detected: bool,
    count_floor: int,
    count_floor_score: float,
) -> float:
    pattern_score = 0.0
    for match in pattern_matches:
        weight = match["weight"]
        occurrences = match["occurrences"]
        occurrence_multiplier = 1.0 + 0.15 * math.log2(max(1, occurrences))
        pattern_score += weight * occurrence_multiplier
    pattern_score = min(pattern_score, 1.0)

    hidden_weight = 0.3 if hidden_content_detected else 0.0
    imperative_weight = min(imperative_density * 0.5, 0.3)
    total_score = min(pattern_score + hidden_weight + imperative_weight, 1.0)

    high_count_hit = any(match.get("occurrences", 0) >= count_floor for match in pattern_matches)
    if high_count_hit and total_score < count_floor_score:
        return count_floor_score
    return total_score
