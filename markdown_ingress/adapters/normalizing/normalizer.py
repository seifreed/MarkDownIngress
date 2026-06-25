"""Content normalization — implements INormalizer protocol."""

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


@dataclass
class _WhitespaceState:
    in_fenced_code: bool = False
    fenced_code_fence: str | None = None
    in_indented_code: bool = False
    previous_blank_outside: bool = False


class Normalizer:  # implements INormalizer protocol
    """Normalize content for deterministic output"""

    # Zero-width and invisible Unicode characters
    # Comprehensive list including directional marks, format characters, etc.
    ZERO_WIDTH_CHARS = (
        "\u200b",  # Zero-width space
        "\u200c",  # Zero-width non-joiner
        "\u200d",  # Zero-width joiner
        "\ufeff",  # Zero-width no-break space (BOM)
        "\u2060",  # Word joiner
        "\u180e",  # Mongolian vowel separator
        # Directional marks
        "\u200e",  # Left-to-right mark (LRM)
        "\u200f",  # Right-to-left mark (RLM)
        # Invisible format characters
        "\u2061",  # Function application
        "\u2062",  # Invisible times
        "\u2063",  # Invisible separator
        "\u2064",  # Invisible plus
        "\u206a",  # Inhibit symmetric swapping
        "\u206b",  # Inhibit Arabic form shaping
        "\u206c",  # Inhibit national digit shapes
        "\u206d",  # National digit shapes
        "\u206e",  # Nominal digit shapes
        "\u206f",  # Nominal digit shapes
        # Additional invisible characters
        "\u034f",  # Combining grapheme joiner
        "\u061c",  # Arabic letter mark
        "\u00ad",  # Soft hyphen
        # Isolate format characters
        "\u2066",  # Left-to-right isolate
        "\u2067",  # Right-to-left isolate
        "\u2068",  # First strong isolate
        "\u2069",  # Pop directional isolate
    )

    # Common tracking parameters
    # Note: 'ref' and 'source' removed as they are often legitimate parameters
    TRACKING_PARAMS = frozenset(
        {
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_term",
            "utm_content",
            "fbclid",
            "gclid",
            "msclkid",
            "mc_cid",
            "mc_eid",
            "_ga",
            "_gl",
            # Modern tracking parameters
            "_ga_client_id",  # Google Analytics 4
            "yclid",  # Yahoo
            "ttwid",  # TikTok
            "li_fat_id",  # LinkedIn
            "igshid",  # Instagram
            "_p_id",  # Pinterest
            "fb_action_ids",  # Facebook
            "fb_action_types",  # Facebook
            "fb_source",  # Facebook
            "fb_ref",  # Facebook
        }
    )

    def __init__(self):
        self.zero_width_pattern = re.compile("|".join(map(re.escape, self.ZERO_WIDTH_CHARS)))

    def normalize(self, text: str) -> str:
        text = self.normalize_unicode(text)
        text = self.remove_zero_width_chars(text)
        return self.normalize_whitespace(text)

    def normalize_unicode(self, text: str) -> str:
        return unicodedata.normalize("NFC", text)

    def remove_zero_width_chars(self, text: str) -> str:
        return str(self.zero_width_pattern.sub("", text))

    # Match lines that start with markdown-significant prefixes
    # - Unordered list items: "- ", "* ", "+ " (with optional leading spaces)
    # - Ordered list items: "1. ", "2. " etc (with optional leading spaces)
    # - Blockquotes: ">"
    # Leading indentation is allowed to any depth so that nested list items
    # (3rd level and deeper accumulate to 4+ spaces) keep their indentation.
    # Genuine indented code blocks reach _append_indented_code_line first via
    # the previous_blank_outside gate, so they are never matched here.
    _MARKDOWN_PREFIX_RE = re.compile(
        r"^(?:"
        r"(?:[ ]*[-*+][ \t])"  # unordered list items (nested lists indent past 3 spaces)
        r"|(?:[ ]*\d+\.[ \t])"  # ordered list items
        r"|(?:[ ]*>)"  # blockquotes
        r")",
    )

    # Match lines that start with 4+ spaces or a tab (indented code blocks)
    _INDENTED_CODE_RE = re.compile(r"^(?:[ ]{4,}|\t)")

    def normalize_whitespace(self, text: str) -> str:
        """
        Normalize whitespace: collapse multiple spaces, normalize line breaks.

        Preserves leading whitespace that is semantically meaningful in
        markdown (list indentation, blockquotes, indented code blocks).

        Also preserves interior whitespace in fenced code blocks (```).
        """
        state = _WhitespaceState()
        lines = text.split("\n")
        normalized: list[str] = []
        for line in lines:
            if self._append_fenced_code_line(line, state, normalized):
                continue

            self._append_non_fenced_line(line, state, normalized)

        self._auto_close_fenced_code(state, normalized)
        return "\n".join(self._trim_blank_edges(normalized))

    @staticmethod
    def _fence_for_line(line: str) -> str | None:
        stripped = line.strip()
        if stripped.startswith("```"):
            fence_char = "`"
        elif stripped.startswith("~~~"):
            fence_char = "~"
        else:
            return None

        fence_len = 0
        for char in stripped:
            if char == fence_char:
                fence_len += 1
            else:
                break
        current_fence = fence_char * fence_len
        if len(current_fence) < 3:
            return None
        return current_fence

    def _append_fenced_code_line(
        self,
        line: str,
        state: _WhitespaceState,
        normalized: list[str],
    ) -> bool:
        stripped = line.strip()
        current_fence = self._fence_for_line(line)
        if current_fence is None:
            return False

        if not state.in_fenced_code:
            state.in_fenced_code = True
            state.fenced_code_fence = current_fence
            normalized.append(line.rstrip())
            state.previous_blank_outside = False
            return True

        if self._is_closing_fence(stripped, current_fence, state):
            state.in_fenced_code = False
            state.fenced_code_fence = None
            normalized.append(line.rstrip())
            state.previous_blank_outside = False
            return True

        normalized.append(line)
        return True

    @staticmethod
    def _is_closing_fence(
        stripped: str,
        current_fence: str,
        state: _WhitespaceState,
    ) -> bool:
        if state.fenced_code_fence is None:
            return False
        return (
            current_fence[0] == state.fenced_code_fence[0]
            and len(current_fence) >= len(state.fenced_code_fence)
            # CommonMark: closing fence must be followed only by whitespace
            and stripped[len(current_fence) :].strip() == ""
        )

    def _append_non_fenced_line(
        self,
        line: str,
        state: _WhitespaceState,
        normalized: list[str],
    ) -> None:
        if state.in_fenced_code:
            normalized.append(line)
            state.previous_blank_outside = False
            return
        if state.in_indented_code:
            self._append_indented_code_line(line, state, normalized)
            return
        if state.previous_blank_outside and self._INDENTED_CODE_RE.match(line):
            state.in_indented_code = True
            normalized.append(line)
            state.previous_blank_outside = False
            return
        if self._MARKDOWN_PREFIX_RE.match(line):
            self._append_markdown_prefix_line(line, state, normalized)
            return
        self._append_regular_line(line, state, normalized)

    def _append_indented_code_line(
        self,
        line: str,
        state: _WhitespaceState,
        normalized: list[str],
    ) -> None:
        if self._INDENTED_CODE_RE.match(line):
            normalized.append(line)
            state.previous_blank_outside = False
            return
        if not line.strip():
            normalized.append("")
            state.previous_blank_outside = True
            return
        state.in_indented_code = False
        normalized.append(self._clean_regular_line(line))
        state.previous_blank_outside = False

    @staticmethod
    def _append_markdown_prefix_line(
        line: str,
        state: _WhitespaceState,
        normalized: list[str],
    ) -> None:
        stripped = line.lstrip(" \t")
        leading = line[: len(line) - len(stripped)]
        normalized.append(leading + re.sub(r"[ \t]+", " ", stripped).rstrip())
        state.previous_blank_outside = False

    def _append_regular_line(
        self,
        line: str,
        state: _WhitespaceState,
        normalized: list[str],
    ) -> None:
        cleaned = self._clean_regular_line(line)
        if not cleaned:
            if not state.previous_blank_outside:
                normalized.append("")
            state.previous_blank_outside = True
            return
        normalized.append(cleaned)
        state.previous_blank_outside = False

    @staticmethod
    def _clean_regular_line(line: str) -> str:
        return re.sub(r"[ \t]+", " ", line).strip()

    @staticmethod
    def _auto_close_fenced_code(state: _WhitespaceState, normalized: list[str]) -> None:
        # Auto-close unclosed fenced code blocks for consistent output.
        # A warning is logged so operators can detect when the output was
        # altered; the warning level ensures it is visible in production logs.
        if state.in_fenced_code and state.fenced_code_fence:
            import logging

            _logger = logging.getLogger(__name__)
            _logger.warning(
                "Unclosed fenced code block detected, auto-closing (fence=%s)",
                (
                    state.fenced_code_fence[:3] + "..."
                    if len(state.fenced_code_fence) > 3
                    else state.fenced_code_fence
                ),
            )
            normalized.append(state.fenced_code_fence)

    @staticmethod
    def _trim_blank_edges(normalized: list[str]) -> list[str]:
        start = 0
        while start < len(normalized) and normalized[start] == "":
            start += 1
        if start:
            normalized = normalized[start:]
        while normalized and normalized[-1] == "":
            normalized.pop()
        return normalized

    def normalize_url(self, url: str) -> str:
        """Normalize URL by removing tracking parameters."""
        parsed = urlparse(url)

        if not parsed.query:
            return url

        params = parse_qs(parsed.query, keep_blank_values=True)
        cleaned_params = {k: v for k, v in params.items() if k.lower() not in self.TRACKING_PARAMS}
        new_query = urlencode(cleaned_params, doseq=True) if cleaned_params else ""

        return urlunparse(
            (parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment)
        )
