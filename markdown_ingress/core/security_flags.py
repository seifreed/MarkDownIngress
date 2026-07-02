"""Flag generation for prompt-injection analysis."""

from __future__ import annotations


def generate_security_flags(
    pattern_matches: list[dict],
    hidden_content: bool,
    imperative_density: float,
    decode_warnings: list[str],
) -> list[str]:
    """Generate human-readable warning flags."""
    flags = []

    if pattern_matches:
        flags.append(f"injection_patterns_detected:{len(pattern_matches)}")

    if hidden_content:
        flags.append("hidden_content")

    if imperative_density > 0.05:
        flags.append(f"high_imperative_density:{imperative_density:.2f}")

    flags.extend(decode_warnings)

    if len(pattern_matches) > 3:
        flags.append("multiple_injection_attempts")

    return flags
