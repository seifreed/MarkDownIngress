"""Text normalization and decoding helpers for security analysis."""

from __future__ import annotations

import html
import logging
import re
import unicodedata
from urllib.parse import unquote

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
)

_logger = logging.getLogger(__name__)


def _normalize_to_ascii(text: str) -> str:
    """Normalize Unicode text to ASCII for pattern matching, handling homoglyphs.

    Converts visually similar Unicode characters to their ASCII equivalents,
    making pattern matching resistant to homoglyph attacks.

    Examples:
        - Cyrillic U+043E maps to 'o'
        - Cyrillic U+0430 maps to 'a'
        - Greek U+03BF maps to 'o'
    """
    mapped = "".join(_HOMOGLYPH_MAP.get(c, c) for c in text)
    compatible = unicodedata.normalize("NFKC", mapped)

    # Then normalize to NFD and filter combining marks (for accented chars)
    normalized = unicodedata.normalize("NFD", compatible)
    # Keep only ASCII characters (removes remaining non-ASCII and combining marks)
    return "".join(c for c in normalized if ord(c) < 128)


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
    # Reject surrogate halves (0xD800-0xDFFF): chr() accepts them but they produce
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
