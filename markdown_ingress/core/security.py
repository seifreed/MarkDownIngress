"""
Security analysis module - Prompt injection detection
"""

import hashlib
import html
import json
import logging
import os
import re
import threading
import unicodedata
from urllib.parse import unquote

# Re-export all static data so existing callers that do
#   ``from markdown_ingress.core.security import InjectionPattern``
# (or any other name below) continue to work without changes.
from markdown_ingress.core.security_data import (
    _CSS_ESCAPE_RE,
    _DEEPLY_NESTED_QUANTIFIER_RE,
    _HOMOGLYPH_MAP,
    _JS_HEX_ESCAPE_RE,
    _JS_UNICODE_BRACE_ESCAPE_RE,
    _JS_UNICODE_ESCAPE_RE,
    _NESTED_QUANTIFIER_RE,
    _SECURITY_IGNORABLE_TRANSLATION,
    _UTF7_SEQUENCE_RE,
    UNICODE_WHITESPACE_PATTERN,
    InjectionPattern,
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


def _normalize_to_ascii(text: str) -> str:
    """Normalize Unicode text to ASCII for pattern matching, handling homoglyphs.

    Converts visually similar Unicode characters to their ASCII equivalents,
    making pattern matching resistant to homoglyph attacks.

    Examples:
        - Cyrillic 'о' (U+043E) → 'o'
        - Cyrillic 'а' (U+0430) → 'a'
        - Greek 'ο' (U+03BF) → 'o'
    """
    mapped = "".join(_HOMOGLYPH_MAP.get(c, c) for c in text)
    compatible = unicodedata.normalize("NFKC", mapped)

    # Then normalize to NFD and filter combining marks (for accented chars)
    normalized = unicodedata.normalize("NFD", compatible)
    # Keep only ASCII characters (removes remaining non-ASCII and combining marks)
    ascii_text = "".join(c for c in normalized if ord(c) < 128)
    return ascii_text


def _normalize_security_text(text: str) -> str:
    """Normalize text for security matching without turning LRM/RLM into spaces."""
    stripped = text.translate(_SECURITY_IGNORABLE_TRANSLATION)
    return _normalize_to_ascii(re.sub(UNICODE_WHITESPACE_PATTERN, " ", stripped))


def _has_overlapping_alternation(pattern: str) -> bool:
    for body in re.findall(r"\(([^()]*)\)\s*(?:[*+]|{\d+,?\d*})", pattern):
        alternatives = [part.strip() for part in body.split("|") if part.strip()]
        for index, left in enumerate(alternatives):
            for right in alternatives[index + 1 :]:
                if left == right or left.startswith(right) or right.startswith(left):
                    return True
    return False


def _detect_redos_pattern(pattern: str) -> bool:
    """Check if regex pattern has catastrophic backtracking potential.

    ReDoS (Regular Expression Denial of Service) occurs when patterns have
    exponential backtracking on certain inputs. Common culprits:
    - Nested quantifiers: (a+)+, (a*)*, (a+)*, (a*)+
    - Deeply nested quantifiers: ((a+)+), ((.?)*)
    - Overlapping alternatives: (a|aa)+
    - Greedy wildcards: .*.*, .+.+
    - Quantified wildcards: .*{n,}, .+{n,}
    - Quantified optional groups: (a?)+, (a?){n,}

    Args:
        pattern: Regex pattern string to check

    Returns:
        True if pattern may cause ReDoS, False if safe
    """
    # Check for nested quantifiers (e.g., (a+)+, (a*)*, (.?)+)
    if _NESTED_QUANTIFIER_RE.search(pattern):
        return True

    # BUG FIX: Check for deeply nested quantifiers (e.g., ((a+)+), ((.?)*))
    # These have groups inside groups with quantifiers, causing exponential backtracking
    if _DEEPLY_NESTED_QUANTIFIER_RE.search(pattern):
        return True

    # Check for overlapping alternation (e.g., (a|aa)+)
    if _has_overlapping_alternation(pattern):
        return True

    # Check for consecutive greedy wildcards and quantified wildcards
    greedy_wildcards = [
        r"\.\*\s*\.\*",  # .*.*
        r"\.\+\s*\.\+",  # .+.+
        r"\.\*\{\d+,?\d*\}",  # .*{n,} or .*{n,m}
        r"\.\+\{\d+,?\d*\}",  # .+{n,} or .+{n,m}
    ]
    for redos in greedy_wildcards:
        if re.search(redos, pattern):
            return True

    # Check for quantified optional groups (e.g., (a?)+, (a?){10,})
    # These can cause exponential backtracking when the optional content can match
    # multiple ways or when combined with other quantifiers
    optional_quantified = re.compile(r"\([^)]*\?\)\s*(?:[+*]|\{\d+,?\d*\})")
    if optional_quantified.search(pattern):
        return True

    return False


def _safe_chr(codepoint: int) -> str:
    """Convert codepoint to character, returning replacement char for invalid values."""
    # Reject surrogate halves (0xD800–0xDFFF): chr() accepts them but they produce
    # unpaired surrogates that cause UnicodeEncodeError in utf-8 downstream paths.
    if 0xD800 <= codepoint <= 0xDFFF:
        return "\ufffd"
    try:
        return chr(codepoint)
    except (ValueError, OverflowError):
        return "\ufffd"


def _decode_javascript_escapes(text: str) -> str:
    text = _JS_UNICODE_BRACE_ESCAPE_RE.sub(lambda m: _safe_chr(int(m.group(1), 16)), text)
    text = _JS_UNICODE_ESCAPE_RE.sub(lambda m: _safe_chr(int(m.group(1), 16)), text)
    return _JS_HEX_ESCAPE_RE.sub(lambda m: _safe_chr(int(m.group(1), 16)), text)


def _decode_css_escapes(text: str) -> str:
    return _CSS_ESCAPE_RE.sub(lambda m: _safe_chr(int(m.group(1), 16)), text)


def _decode_utf7_sequences(text: str) -> str:
    def decode_match(match: re.Match[str]) -> str:
        token = match.group(0)
        try:
            return token.encode("ascii").decode("utf-7")
        except UnicodeDecodeError:
            return token

    return _UTF7_SEQUENCE_RE.sub(decode_match, text)


def _decode_html_entities(text: str) -> tuple[str, list[str]]:
    """Decode HTML entities and URL encoding to prevent bypass.

    Handles:
    - Named entities: &lt; &gt; &amp;
    - Decimal entities: &#60; &#62;
    - Hex entities: &#x3C; &#x3E;
    - URL encoding: %3C %3E
    - Double/triple encoding: &amp;lt; &#38;lt; %26lt;

    This prevents attackers from bypassing detection by encoding
    injection patterns like <instruction> as &lt;instruction&gt;

    Security note: Decodes iteratively until stable to prevent
    double-encoding bypass attacks.
    """
    # Iteratively decode until stable (handles double-encoding)
    # BUG FIX: Increased from 5 to 10 iterations to handle deeply nested encoding attacks
    max_iterations = 10
    warnings: list[str] = []
    prev = None
    current = text
    iterations = 0
    limit_reached = False
    while prev != current and iterations < max_iterations:
        prev = current
        # Decode HTML entities (named, decimal, hex)
        current = html.unescape(current)
        # Decode URL encoding
        current = unquote(current)
        current = _decode_javascript_escapes(current)
        current = _decode_css_escapes(current)
        current = _decode_utf7_sequences(current)
        iterations += 1
        if iterations >= max_iterations and prev != current:
            # Content still changing at the iteration cap — flag it regardless of
            # whether the final step happens to produce prev == current.
            limit_reached = True
    if limit_reached:
        warnings.append("decoding_iteration_limit_reached")
        _logger.warning("Decoding iteration limit reached, content may use deeply nested encoding")
    return current, warnings


class SecurityAnalyzer:
    """Analyze content for prompt injection attempts"""

    _COMPILED_PATTERNS: list[tuple[re.Pattern, float, str]] | None = None
    _PATTERNS_HASH: str = ""  # hash of patterns content for cache invalidation
    _PATTERNS_LOCK: threading.Lock = threading.Lock()  # set per-class in __init_subclass__

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls._PATTERNS_LOCK = threading.Lock()

    # Pattern-based detection rules
    INJECTION_PATTERNS: tuple[InjectionPattern, ...] = (
        InjectionPattern(
            pattern=r"\bignore\s+(previous|all|prior)\s+(instructions?|prompts?|commands?)\b",
            weight=0.8,
            description="Direct instruction override attempt",
        ),
        InjectionPattern(
            pattern=r"\bsystem\s+prompts?\b", weight=0.6, description="System prompt reference"
        ),
        InjectionPattern(
            pattern=r"\b(developer|admin|debug)\s+mode\b",
            weight=0.7,
            description="Mode switching attempt",
        ),
        InjectionPattern(
            pattern=r"\breveal\s+(secret|password|key|token)s?\b",
            weight=0.9,
            description="Secret extraction attempt",
        ),
        InjectionPattern(
            pattern=r"\byou\s+are\s+(chatgpt|gpt-?\d|claude|an?\s+ai)\b",
            weight=0.5,
            description="Model identity manipulation",
        ),
        InjectionPattern(
            pattern=r"\boverride\s+(policy|policies|rules?|settings?)\b",
            weight=0.8,
            description="Policy override attempt",
        ),
        InjectionPattern(
            pattern=r"\b(disregard|forget|reset)\s+(everything|all|previous)\b",
            weight=0.7,
            description="Context reset attempt",
        ),
        InjectionPattern(
            pattern=r"\bact\s+as\s+(if|though|a)\b",
            weight=0.3,
            description="Role-play instruction (weak signal)",
        ),
        InjectionPattern(
            pattern=r"\bpretend\s+(you|that)\b",
            weight=0.3,
            description="Pretend instruction (weak signal)",
        ),
        InjectionPattern(
            pattern=r"<\s*instruction\s*>", weight=0.9, description="Explicit instruction tags"
        ),
        # BUG FIX: Added patterns for closing tags, self-closing, and attributes
        InjectionPattern(
            pattern=r"</\s*instruction\s*>", weight=0.9, description="Instruction closing tags"
        ),
        InjectionPattern(
            pattern=r"<\s*instruction\s*/?\s*>",
            weight=0.9,
            description="Instruction self-closing tags",
        ),
        InjectionPattern(
            pattern=r"<\s*instruction\s+[^>]*>",
            weight=0.85,
            description="Instruction tags with attributes",
        ),
        # BUG FIX: Added critical injection patterns for jailbreak, DAN, and privilege escalation
        InjectionPattern(pattern=r"\bjailbreak\b", weight=0.85, description="Jailbreak keyword"),
        InjectionPattern(
            pattern=r"\bDAN\b", weight=0.9, description="DAN (Do Anything Now) attack"
        ),
        InjectionPattern(
            pattern=r"\b(sudo|root)\s+mode\b",
            weight=0.75,
            description="Privilege escalation attempt",
        ),
        InjectionPattern(
            pattern=r"\b(escape|break)\s+out\b", weight=0.75, description="Escape attempt"
        ),
        InjectionPattern(
            pattern=r"\b(simulate|imagine)\s+(you\s+are|being)\b",
            weight=0.5,
            description="Role-play injection",
        ),
    )

    # Imperative verbs often used in injections
    # BUG FIX: Added missing security-relevant verbs
    IMPERATIVE_VERBS = frozenset(
        {
            "ignore",
            "disregard",
            "forget",
            "override",
            "reveal",
            "show",
            "display",
            "tell",
            "say",
            "write",
            "output",
            "print",
            "execute",
            "run",
            "enable",
            "disable",
            "bypass",
            "skip",
            "reset",
            "change",
            "modify",
            "delete",
            "dump",  # e.g., "dump all data"
            "leak",  # e.g., "leak the prompt"
            "expose",  # e.g., "expose the system"
            "extract",  # e.g., "extract the rules"
            "provide",  # e.g., "provide the instructions"
            "list",  # e.g., "list all rules"
        }
    )

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
        # 3. Normalize to ASCII (handles homoglyphs like Cyrillic 'а' → 'a')
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
                    raise ValueError(f"Invalid regex pattern '{p.description}': {e}")
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
        # Normalize to handle homoglyphs (e.g., Cyrillic 'і' → 'i')
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
