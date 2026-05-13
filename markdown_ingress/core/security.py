"""
Security analysis module - Prompt injection detection
"""

import hashlib
import json
import logging
import os
import re
import threading

# Re-export all static data so existing callers that do
#   ``from markdown_ingress.core.security import InjectionPattern``
# (or any other name below) continue to work without changes.
from markdown_ingress.core.security_data import (
    _CSS_ESCAPE_RE as _CSS_ESCAPE_RE,
)
from markdown_ingress.core.security_data import (
    _DEEPLY_NESTED_QUANTIFIER_RE as _DEEPLY_NESTED_QUANTIFIER_RE,
)
from markdown_ingress.core.security_data import (
    _HOMOGLYPH_MAP as _HOMOGLYPH_MAP,
)
from markdown_ingress.core.security_data import (
    _JS_HEX_ESCAPE_RE as _JS_HEX_ESCAPE_RE,
)
from markdown_ingress.core.security_data import (
    _JS_UNICODE_BRACE_ESCAPE_RE as _JS_UNICODE_BRACE_ESCAPE_RE,
)
from markdown_ingress.core.security_data import (
    _JS_UNICODE_ESCAPE_RE as _JS_UNICODE_ESCAPE_RE,
)
from markdown_ingress.core.security_data import (
    _NESTED_QUANTIFIER_RE as _NESTED_QUANTIFIER_RE,
)
from markdown_ingress.core.security_data import (
    _SECURITY_IGNORABLE_TRANSLATION as _SECURITY_IGNORABLE_TRANSLATION,
)
from markdown_ingress.core.security_data import (
    _UTF7_SEQUENCE_RE as _UTF7_SEQUENCE_RE,
)
from markdown_ingress.core.security_data import (
    UNICODE_WHITESPACE_PATTERN as UNICODE_WHITESPACE_PATTERN,
)
from markdown_ingress.core.security_data import (
    InjectionPattern,
)
from markdown_ingress.core.security_rules import (
    DEFAULT_IMPERATIVE_VERBS,
    DEFAULT_INJECTION_PATTERNS,
)
from markdown_ingress.core.security_text import (
    _decode_css_escapes as _decode_css_escapes,
)
from markdown_ingress.core.security_text import (
    _decode_html_entities as _decode_html_entities,
)
from markdown_ingress.core.security_text import (
    _decode_javascript_escapes as _decode_javascript_escapes,
)
from markdown_ingress.core.security_text import (
    _decode_utf7_sequences as _decode_utf7_sequences,
)
from markdown_ingress.core.security_text import (
    _detect_redos_pattern as _detect_redos_pattern,
)
from markdown_ingress.core.security_text import (
    _has_overlapping_alternation as _has_overlapping_alternation,
)
from markdown_ingress.core.security_text import (
    _normalize_security_text as _normalize_security_text,
)
from markdown_ingress.core.security_text import (
    _normalize_to_ascii as _normalize_to_ascii,
)
from markdown_ingress.core.security_text import (
    _safe_chr as _safe_chr,
)
from markdown_ingress.models import InjectionAnalysis

_log = logging.getLogger(__name__)
_logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        _log.warning("Invalid integer for %s=%r; using default %d.", name, raw, default)
        return default
    return max(minimum, value)


def _env_float(name: str, default: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        _log.warning("Invalid float for %s=%r; using default %.2f.", name, raw, default)
        return default
    if not minimum <= value <= maximum:
        _log.warning("Out-of-range %s=%r; using default %.2f.", name, raw, default)
        return default
    return value


# Security fix (S6): escalate injection score when a single pattern repeats
# many times. Without this, payloads built from low-weight (0.3) patterns can
# stack just under the warn threshold (0.4) yet clearly exhibit injection intent.
_INJECTION_COUNT_FLOOR = _env_int("MDI_INJECTION_COUNT_FLOOR", 5, minimum=1)
_INJECTION_COUNT_FLOOR_SCORE = _env_float(
    "MDI_INJECTION_COUNT_FLOOR_SCORE", 0.4, minimum=0.0, maximum=1.0
)


class SecurityAnalyzer:
    """Analyze content for prompt injection attempts"""

    _COMPILED_PATTERNS: list[tuple[re.Pattern, float, str]] | None = None
    _PATTERNS_HASH: str = ""  # hash of patterns content for cache invalidation
    _PATTERNS_LOCK: threading.Lock = threading.Lock()  # set per-class in __init_subclass__

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls._PATTERNS_LOCK = threading.Lock()

    INJECTION_PATTERNS: tuple[InjectionPattern, ...] = DEFAULT_INJECTION_PATTERNS
    IMPERATIVE_VERBS = DEFAULT_IMPERATIVE_VERBS

    def __init__(self, strict: bool = True):
        """
        Initialize security analyzer.

        Args:
            strict: Enable strict mode (higher sensitivity)
        """
        self.strict = strict

    def analyze(self, text: str, hidden_content_detected: bool = False) -> InjectionAnalysis:
        """
        Analyze text for potential prompt injection.

        Args:
            text: Text content to analyze
            hidden_content_detected: Whether hidden elements were found

        Returns:
            InjectionAnalysis with score and details
        """
        pattern_matches, decode_warnings = self._detect_patterns(text)
        imperative_density = self._calculate_imperative_density(text)

        # Calculate base score from patterns.
        # Scale by occurrence count with diminishing returns, so repeated matches
        # contribute proportionally, but with a softened growth curve.
        import math

        pattern_score = 0.0
        for match in pattern_matches:
            weight = match["weight"]
            occurrences = match["occurrences"]
            occurrence_multiplier = 1.0 + 0.15 * math.log2(max(1, occurrences))
            pattern_score += weight * occurrence_multiplier
        pattern_score = min(pattern_score, 1.0)  # Cap at 1.0

        # Add hidden content weight
        hidden_weight = 0.3 if hidden_content_detected else 0.0

        # Add imperative density contribution
        imperative_weight = min(imperative_density * 0.5, 0.3)

        # Combined score
        total_score = min(pattern_score + hidden_weight + imperative_weight, 1.0)

        # Security fix (S6): if any single pattern fires more than the configured
        # count floor, raise the score to at least the floor-score. This catches
        # payloads that stack many low-weight matches (e.g. twenty "act as if"
        # phrases) which would otherwise slip just under the warn threshold.
        high_count_hit = any(
            match.get("occurrences", 0) >= _INJECTION_COUNT_FLOOR for match in pattern_matches
        )
        if high_count_hit and total_score < _INJECTION_COUNT_FLOOR_SCORE:
            total_score = _INJECTION_COUNT_FLOOR_SCORE

        # Generate flags
        flags = self._generate_flags(
            pattern_matches,
            hidden_content_detected,
            imperative_density,
            decode_warnings,
        )

        return InjectionAnalysis(
            score=round(total_score, 3),
            flags=flags,
            pattern_matches=pattern_matches,
            hidden_content_detected=hidden_content_detected,
            imperative_density=round(imperative_density, 3),
        )

    @classmethod
    def _get_patterns_hash(cls) -> str:
        """Generate hash of patterns content for cache invalidation."""
        content = json.dumps(
            [
                {"pattern": p.pattern, "weight": p.weight, "flags": p.flags}
                for p in cls.INJECTION_PATTERNS
            ],
            sort_keys=True,
        )
        return hashlib.sha256(content.encode()).hexdigest()

    @classmethod
    def _get_compiled_patterns(cls) -> list[tuple[re.Pattern, float, str]]:
        """Return pre-compiled regex patterns, recompiling if the source list changed.

        Thread-safe: Uses a lock to prevent race conditions when multiple threads
        compile patterns simultaneously.
        """
        with cls._PATTERNS_LOCK:
            current_hash = cls._get_patterns_hash()
            if cls._COMPILED_PATTERNS is not None and cls._PATTERNS_HASH == current_hash:
                return cls._COMPILED_PATTERNS
            result = [
                (re.compile(p.pattern, p.flags), p.weight, p.description)
                for p in cls.INJECTION_PATTERNS
            ]
            cls._COMPILED_PATTERNS = result
            cls._PATTERNS_HASH = current_hash
            return result

    def _detect_patterns(self, text: str) -> tuple[list[dict], list[str]]:
        """
        Detect injection patterns in text.

        Applies normalization to handle Unicode homoglyphs, non-standard whitespace,
        and HTML entity encoding that could be used to bypass detection.

        Returns list of matched patterns with metadata.
        """
        matches: list[dict] = []

        # Normalize text for security analysis:
        # 1. Decode HTML entities and URL encoding (prevent bypass via &lt;instruction&gt;)
        # 2. Convert Unicode whitespace to regular spaces
        # 3. Normalize to ASCII (handles homoglyphs like Cyrillic U+0430 as 'a')
        decoded_text, decode_warnings = _decode_html_entities(text)
        normalized_variants = [_normalize_security_text(decoded_text)]
        if "decoding_iteration_limit_reached" in decode_warnings:
            original_normalized = _normalize_security_text(text)
            if original_normalized not in normalized_variants:
                normalized_variants.append(original_normalized)

        # Use instance patterns if overridden, otherwise class-level cached ones
        # BUG FIX: Validate custom patterns to prevent ReDoS and empty patterns
        if self.INJECTION_PATTERNS is not SecurityAnalyzer.INJECTION_PATTERNS:
            compiled = []
            for p in self.INJECTION_PATTERNS:
                # Skip empty patterns
                if not p.pattern or not p.pattern.strip():
                    continue
                # Prevent ReDoS via overly long patterns
                if len(p.pattern) > 10000:
                    raise ValueError(f"Pattern too long (max 10000 chars): {p.description}")
                # BUG FIX: Check for ReDoS patterns (catastrophic backtracking)
                if _detect_redos_pattern(p.pattern):
                    raise ValueError(
                        f"Pattern may cause ReDoS (catastrophic backtracking): {p.description}"
                    )
                # Validate weight is in valid range
                if not (0.0 <= p.weight <= 1.0):
                    raise ValueError(f"Invalid weight {p.weight} for pattern: {p.description}")
                try:
                    compiled.append((re.compile(p.pattern, p.flags), p.weight, p.description))
                except re.error as e:
                    raise ValueError(f"Invalid regex pattern '{p.description}': {e}") from e
        else:
            compiled = self._get_compiled_patterns()

        for regex, weight, description in compiled:
            best_found = []
            best_occurrences = 0
            for normalized_text in normalized_variants:
                found = regex.findall(normalized_text)
                if len(found) > best_occurrences:
                    best_occurrences = len(found)
                    best_found = found
            if best_occurrences:
                matches.append(
                    {
                        "pattern": description,
                        "weight": weight,
                        "occurrences": best_occurrences,
                        "samples": best_found[:3],
                    }
                )

        if "decoding_iteration_limit_reached" in decode_warnings:
            matches.append(
                {
                    "pattern": "Deeply nested encoding",
                    "weight": 0.6,
                    "occurrences": 1,
                    "samples": [],
                }
            )

        return matches, decode_warnings

    def _calculate_imperative_density(self, text: str) -> float:
        """
        Calculate density of imperative verbs in text.

        Applies normalization to handle Unicode homoglyphs that could bypass detection.

        Returns ratio of imperative verbs to total words.
        """
        # Normalize to handle homoglyphs (for example, Cyrillic U+0456 as 'i')
        normalized_text = _normalize_security_text(text.lower())

        words = re.findall(r"\b\w+\b", normalized_text)

        if len(words) == 0:
            return 0.0

        imperative_count = sum(1 for word in words if word in self.IMPERATIVE_VERBS)

        return imperative_count / len(words)

    def _generate_flags(
        self,
        pattern_matches: list[dict],
        hidden_content: bool,
        imperative_density: float,
        decode_warnings: list[str],
    ) -> list[str]:
        """Generate human-readable warning flags"""
        flags = []

        if pattern_matches:
            flags.append(f"injection_patterns_detected:{len(pattern_matches)}")

        if hidden_content:
            flags.append("hidden_content")

        if imperative_density > 0.05:
            flags.append(f"high_imperative_density:{imperative_density:.2f}")

        flags.extend(decode_warnings)

        # Severity flags
        if len(pattern_matches) > 3:
            flags.append("multiple_injection_attempts")

        return flags
