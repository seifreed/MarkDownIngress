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
from markdown_ingress.core.security_scoring import calculate_injection_score
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
        total_score = calculate_injection_score(
            pattern_matches,
            imperative_density,
            hidden_content_detected=hidden_content_detected,
            count_floor=_INJECTION_COUNT_FLOOR,
            count_floor_score=_INJECTION_COUNT_FLOOR_SCORE,
        )

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
            if cls._COMPILED_PATTERNS is not None and current_hash == cls._PATTERNS_HASH:
                return cls._COMPILED_PATTERNS
            result = [
                (re.compile(p.pattern, p.flags), p.weight, p.description)
                for p in cls.INJECTION_PATTERNS
            ]
            cls._COMPILED_PATTERNS = result
            cls._PATTERNS_HASH = current_hash
            return result

    def _compiled_patterns_for_detection(self) -> list[tuple[re.Pattern, float, str]]:
        if self.INJECTION_PATTERNS is not SecurityAnalyzer.INJECTION_PATTERNS:
            return self._compile_custom_injection_patterns()
        return self._get_compiled_patterns()

    def _compile_custom_injection_patterns(self) -> list[tuple[re.Pattern, float, str]]:
        compiled = []
        for pattern in self.INJECTION_PATTERNS:
            compiled_pattern = self._compile_custom_injection_pattern(pattern)
            if compiled_pattern is not None:
                compiled.append(compiled_pattern)
        return compiled

    @staticmethod
    def _compile_custom_injection_pattern(
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
        except re.error as e:
            raise ValueError(f"Invalid regex pattern '{pattern.description}': {e}") from e

    @staticmethod
    def _normalized_detection_variants(
        text: str, decoded_text: str, decode_warnings: list[str]
    ) -> list[str]:
        normalized_variants = [_normalize_security_text(decoded_text)]
        if "decoding_iteration_limit_reached" in decode_warnings:
            original_normalized = _normalize_security_text(text)
            if original_normalized not in normalized_variants:
                normalized_variants.append(original_normalized)
        return normalized_variants

    @staticmethod
    def _best_pattern_occurrences(
        regex: re.Pattern, normalized_variants: list[str]
    ) -> tuple[int, list]:
        best_found = []
        best_occurrences = 0
        for normalized_text in normalized_variants:
            found = regex.findall(normalized_text)
            if len(found) > best_occurrences:
                best_occurrences = len(found)
                best_found = found
        return best_occurrences, best_found

    def _collect_pattern_matches(
        self,
        compiled: list[tuple[re.Pattern, float, str]],
        normalized_variants: list[str],
    ) -> list[dict]:
        matches: list[dict] = []
        for regex, weight, description in compiled:
            occurrences, found = self._best_pattern_occurrences(regex, normalized_variants)
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

    @staticmethod
    def _append_decoding_limit_match(matches: list[dict], decode_warnings: list[str]) -> None:
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

    def _detect_patterns(self, text: str) -> tuple[list[dict], list[str]]:
        """
        Detect injection patterns in text.

        Applies normalization to handle Unicode homoglyphs, non-standard whitespace,
        and HTML entity encoding that could be used to bypass detection.

        Returns list of matched patterns with metadata.
        """
        decoded_text, decode_warnings = _decode_html_entities(text)
        normalized_variants = self._normalized_detection_variants(
            text, decoded_text, decode_warnings
        )
        matches = self._collect_pattern_matches(
            self._compiled_patterns_for_detection(),
            normalized_variants,
        )
        self._append_decoding_limit_match(matches, decode_warnings)
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
