"""Content normalization — implements INormalizer protocol."""

import re
import unicodedata
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

class Normalizer:  # implements INormalizer protocol
    """Normalize content for deterministic output"""

    # Zero-width and invisible Unicode characters
    # Comprehensive list including directional marks, format characters, etc.
    ZERO_WIDTH_CHARS = [
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
    ]

    # Common tracking parameters
    # Note: 'ref' and 'source' removed as they are often legitimate parameters
    TRACKING_PARAMS = {
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

    def __init__(self):
        self.zero_width_pattern = re.compile("|".join(map(re.escape, self.ZERO_WIDTH_CHARS)))

    def normalize(self, text: str) -> str:
        text = self.normalize_unicode(text)
        text = self.remove_zero_width_chars(text)
        text = self.normalize_whitespace(text)
        return text

    def normalize_unicode(self, text: str) -> str:
        return unicodedata.normalize("NFC", text)

    def remove_zero_width_chars(self, text: str) -> str:
        return str(self.zero_width_pattern.sub("", text))

    # Match lines that start with markdown-significant prefixes
    # - Unordered list items: "- ", "* ", "+ " (with optional leading spaces)
    # - Ordered list items: "1. ", "2. " etc (with optional leading spaces)
    # - Blockquotes: ">"
    # Note: Indented code blocks (4+ spaces) are NOT preserved here because
    # their interior spacing must not be collapsed. They're handled separately.
    _MARKDOWN_PREFIX_RE = re.compile(
        r"^(?:"
        r"(?:[ ]{0,3}[-*+][ \t])"  # unordered list items (CommonMark: max 3 leading spaces)
        r"|(?:[ ]{0,3}\d+\.[ \t])"  # ordered list items
        r"|(?:[ ]{0,3}>)"  # blockquotes
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
        # Track fenced code block state
        in_fenced_code = False
        fenced_code_fence = None
        in_indented_code = False
        previous_blank_outside = False

        lines = text.split("\n")
        normalized: list[str] = []
        for line in lines:
            stripped = line.strip()
            fence_char = None
            if stripped.startswith("```"):
                fence_char = "`"
            elif stripped.startswith("~~~"):
                fence_char = "~"

            if fence_char is not None:
                fence_len = 0
                for ch in stripped:
                    if ch == fence_char:
                        fence_len += 1
                    else:
                        break
                current_fence = fence_char * fence_len
                # CommonMark requires minimum 3 fence characters
                if len(current_fence) < 3:
                    normalized.append(line)
                    continue
                if not in_fenced_code:
                    in_fenced_code = True
                    fenced_code_fence = current_fence
                    normalized.append(line.rstrip())
                    previous_blank_outside = False
                elif (
                    fenced_code_fence is not None
                    and fence_char == fenced_code_fence[0]
                    and len(current_fence) >= len(fenced_code_fence)
                ):
                    in_fenced_code = False
                    fenced_code_fence = None
                    normalized.append(line.rstrip())
                    previous_blank_outside = False
                else:
                    normalized.append(line)
                continue

            if in_fenced_code:
                normalized.append(line)
                previous_blank_outside = False
            elif in_indented_code:
                if self._INDENTED_CODE_RE.match(line):
                    normalized.append(line)
                    previous_blank_outside = False
                elif not line.strip():
                    normalized.append("")
                    previous_blank_outside = True
                else:
                    in_indented_code = False
                    cleaned = re.sub(r"[ \t]+", " ", line).strip()
                    normalized.append(cleaned)
                    previous_blank_outside = False
            elif previous_blank_outside and self._INDENTED_CODE_RE.match(line):
                in_indented_code = True
                normalized.append(line)
                previous_blank_outside = False
            elif self._MARKDOWN_PREFIX_RE.match(line):
                stripped = line.lstrip(" \t")
                leading = line[: len(line) - len(stripped)]
                normalized.append(leading + re.sub(r"[ \t]+", " ", stripped).rstrip())
                previous_blank_outside = False
            else:
                cleaned = re.sub(r"[ \t]+", " ", line).strip()
                if not cleaned:
                    if not previous_blank_outside:
                        normalized.append("")
                    previous_blank_outside = True
                else:
                    normalized.append(cleaned)
                    previous_blank_outside = False

        # Auto-close unclosed fenced code blocks for consistent output
        if in_fenced_code and fenced_code_fence:
            import logging
            _logger = logging.getLogger(__name__)
            _logger.warning(
                "Unclosed fenced code block detected, auto-closing (fence=%s)",
                fenced_code_fence[:3] + "..." if len(fenced_code_fence) > 3 else fenced_code_fence,
            )
            normalized.append(fenced_code_fence)

        start = 0
        while start < len(normalized) and normalized[start] == "":
            start += 1
        if start:
            normalized = normalized[start:]
        while normalized and normalized[-1] == "":
            normalized.pop()

        return "\n".join(normalized)

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

    def normalize_heading(self, heading: str) -> str:
        """Normalize heading text: trim, single line, no excess whitespace."""
        heading = heading.replace("\n", " ")
        heading = re.sub(r"\s+", " ", heading)
        return heading.strip()
