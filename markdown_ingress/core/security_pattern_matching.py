"""Pattern matching helpers for prompt-injection analysis."""

from __future__ import annotations

import hashlib
import json
import re

from markdown_ingress.core.security_data import InjectionPattern
from markdown_ingress.core.security_text import _detect_redos_pattern, _normalize_security_text


def patterns_hash(patterns: tuple[InjectionPattern, ...]) -> str:
    content = json.dumps(
        [{"pattern": p.pattern, "weight": p.weight, "flags": p.flags} for p in patterns],
        sort_keys=True,
    )
    return hashlib.sha256(content.encode()).hexdigest()


def compile_injection_pattern(
    pattern: InjectionPattern,
) -> tuple[re.Pattern, float, str] | None:
    if not pattern.pattern or not pattern.pattern.strip():
        return None
    if len(pattern.pattern) > 10000:
        raise ValueError(f"Pattern too long (max 10000 chars): {pattern.description}")
    if _detect_redos_pattern(pattern.pattern):
        raise ValueError(
            f"Pattern may cause ReDoS (catastrophic backtracking): {pattern.description}"
        )
    if not (0.0 <= pattern.weight <= 1.0):
        raise ValueError(f"Invalid weight {pattern.weight} for pattern: {pattern.description}")
    try:
        return re.compile(pattern.pattern, pattern.flags), pattern.weight, pattern.description
    except re.error as exc:
        raise ValueError(f"Invalid regex pattern '{pattern.description}': {exc}") from exc


def compile_injection_patterns(
    patterns: tuple[InjectionPattern, ...],
) -> list[tuple[re.Pattern, float, str]]:
    compiled = []
    for pattern in patterns:
        compiled_pattern = compile_injection_pattern(pattern)
        if compiled_pattern is not None:
            compiled.append(compiled_pattern)
    return compiled


def normalized_detection_variants(
    text: str, decoded_text: str, decode_warnings: list[str]
) -> list[str]:
    normalized_variants = [_normalize_security_text(decoded_text)]
    if "decoding_iteration_limit_reached" in decode_warnings:
        original_normalized = _normalize_security_text(text)
        if original_normalized not in normalized_variants:
            normalized_variants.append(original_normalized)
    return normalized_variants


def best_pattern_occurrences(regex: re.Pattern, normalized_variants: list[str]) -> tuple[int, list]:
    best_found = []
    best_occurrences = 0
    for normalized_text in normalized_variants:
        found = regex.findall(normalized_text)
        if len(found) > best_occurrences:
            best_occurrences = len(found)
            best_found = found
    return best_occurrences, best_found


def collect_pattern_matches(
    compiled: list[tuple[re.Pattern, float, str]],
    normalized_variants: list[str],
) -> list[dict]:
    matches: list[dict] = []
    for regex, weight, description in compiled:
        occurrences, found = best_pattern_occurrences(regex, normalized_variants)
        if occurrences:
            matches.append(
                {
                    "pattern": description,
                    "weight": weight,
                    "occurrences": occurrences,
                    "samples": found[:3],
                }
            )
    return matches


def append_decoding_limit_match(matches: list[dict], decode_warnings: list[str]) -> None:
    if "decoding_iteration_limit_reached" not in decode_warnings:
        return
    matches.append(
        {
            "pattern": "Deeply nested encoding",
            "weight": 0.6,
            "occurrences": 1,
            "samples": [],
        }
    )
