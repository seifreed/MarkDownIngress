"""
Security analysis module - Prompt injection detection
"""

import re
import threading

from markdown_ingress.core.config_env import read_float_env, read_positive_int_env

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
from markdown_ingress.core.security_flags import generate_security_flags
from markdown_ingress.core.security_imperative import calculate_imperative_density
from markdown_ingress.core.security_pattern_matching import (
    append_decoding_limit_match,
    collect_pattern_matches,
    compile_injection_pattern,
    compile_injection_patterns,
    normalized_detection_variants,
    patterns_hash,
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

# Security fix (S6): escalate injection score when a single pattern repeats
# many times. Without this, payloads built from low-weight (0.3) patterns can
# stack just under the warn threshold (0.4) yet clearly exhibit injection intent.
_INJECTION_COUNT_FLOOR = read_positive_int_env("MDI_INJECTION_COUNT_FLOOR", 5, minimum=1)
_INJECTION_COUNT_FLOOR_SCORE = read_float_env(
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
        imperative_density = calculate_imperative_density(text, self.IMPERATIVE_VERBS)
        total_score = calculate_injection_score(
            pattern_matches,
            imperative_density,
            hidden_content_detected=hidden_content_detected,
            count_floor=_INJECTION_COUNT_FLOOR,
            count_floor_score=_INJECTION_COUNT_FLOOR_SCORE,
        )

        flags = generate_security_flags(
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
        return patterns_hash(cls.INJECTION_PATTERNS)

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
            result = compile_injection_patterns(cls.INJECTION_PATTERNS)
            cls._COMPILED_PATTERNS = result
            cls._PATTERNS_HASH = current_hash
            return result

    def _compiled_patterns_for_detection(self) -> list[tuple[re.Pattern, float, str]]:
        if self.INJECTION_PATTERNS is not SecurityAnalyzer.INJECTION_PATTERNS:
            return self._compile_custom_injection_patterns()
        return self._get_compiled_patterns()

    def _compile_custom_injection_patterns(self) -> list[tuple[re.Pattern, float, str]]:
        return compile_injection_patterns(self.INJECTION_PATTERNS)

    @staticmethod
    def _compile_custom_injection_pattern(
        pattern: InjectionPattern,
    ) -> tuple[re.Pattern, float, str] | None:
        return compile_injection_pattern(pattern)

    @staticmethod
    def _normalized_detection_variants(
        text: str, decoded_text: str, decode_warnings: list[str]
    ) -> list[str]:
        return normalized_detection_variants(text, decoded_text, decode_warnings)

    @staticmethod
    def _best_pattern_occurrences(
        regex: re.Pattern, normalized_variants: list[str]
    ) -> tuple[int, list]:
        from markdown_ingress.core.security_pattern_matching import best_pattern_occurrences

        return best_pattern_occurrences(regex, normalized_variants)

    def _collect_pattern_matches(
        self,
        compiled: list[tuple[re.Pattern, float, str]],
        normalized_variants: list[str],
    ) -> list[dict]:
        return collect_pattern_matches(compiled, normalized_variants)

    @staticmethod
    def _append_decoding_limit_match(matches: list[dict], decode_warnings: list[str]) -> None:
        append_decoding_limit_match(matches, decode_warnings)

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
