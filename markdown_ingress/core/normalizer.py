"""
Content normalization module
"""

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
        """
        Apply all normalization steps to text.

        Args:
            text: Input text to normalize

        Returns:
            Normalized text
        """
        text = self.normalize_unicode(text)
        text = self.remove_zero_width_chars(text)
        text = self.normalize_whitespace(text)
        return text

    def normalize_unicode(self, text: str) -> str:
        """
        Normalize Unicode to NFC form for consistency.

        Args:
            text: Input text

        Returns:
            NFC-normalized text
        """
        return unicodedata.normalize("NFC", text)

    def remove_zero_width_chars(self, text: str) -> str:
        """
        Remove zero-width and invisible characters.

        Args:
            text: Input text

        Returns:
            Text without zero-width chars
        """
        return str(self.zero_width_pattern.sub("", text))

    # Match lines that start with markdown-significant prefixes
    # - Unordered list items: "- ", "* ", "+ " (with optional leading spaces)
    # - Ordered list items: "1. ", "2. " etc (with optional leading spaces)
    # - Blockquotes: ">"
    # Note: Indented code blocks (4+ spaces) are NOT preserved here because
    # their interior spacing must not be collapsed. They're handled separately.
    _MARKDOWN_PREFIX_RE = re.compile(
        r"^(?:"
        r"(?:[ \t]*[-*+][ \t])"  # unordered list items
        r"|(?:[ \t]*\d+\.[ \t])"  # ordered list items
        r"|(?:[ \t]*>)"  # blockquotes
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

        Args:
            text: Input text

        Returns:
            Text with normalized whitespace
        """
        # Replace multiple newlines with double newline (paragraph separator)
        text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)

        # Remove trailing whitespace on lines
        text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)

        # Track fenced code block state
        in_fenced_code = False
        fenced_code_fence = None

        # Normalize whitespace differently based on line type:
        # - Fenced code blocks: preserve ALL whitespace
        # - Indented code blocks: preserve ALL whitespace
        # - List items/blockquotes: preserve leading, collapse interior
        # - Regular lines: collapse interior, strip leading
        lines = text.split("\n")
        normalized: list[str] = []
        for line in lines:
            # Track fenced code blocks (```)
            if line.strip().startswith("```"):
                # Check if this is the same fence type as opening
                current_fence = line.strip()[:3]
                if not in_fenced_code:
                    # Opening fence
                    in_fenced_code = True
                    fenced_code_fence = current_fence
                    normalized.append(line)
                elif current_fence == fenced_code_fence:
                    # Closing fence (same type as opening)
                    in_fenced_code = False
                    fenced_code_fence = None
                    normalized.append(line)
                else:
                    # Different fence type inside code block - preserve
                    normalized.append(line)
                continue

            if in_fenced_code:
                # Inside fenced code block: preserve ALL whitespace
                normalized.append(line)
            elif self._INDENTED_CODE_RE.match(line):
                # Indented code block: preserve ALL whitespace (no collapsing)
                normalized.append(line)
            elif self._MARKDOWN_PREFIX_RE.match(line):
                # List items/blockquotes: preserve leading, collapse interior runs
                stripped = line.lstrip(" \t")
                leading = line[: len(line) - len(stripped)]
                normalized.append(leading + re.sub(r"[ \t]+", " ", stripped))
            else:
                # Regular lines: collapse spaces and strip leading whitespace
                normalized.append(re.sub(r"[ \t]+", " ", line).lstrip())
        text = "\n".join(normalized)

        return text.strip()

    def normalize_url(self, url: str) -> str:
        """
        Normalize URL by removing tracking parameters.

        Args:
            url: Input URL

        Returns:
            Normalized URL without tracking params
        """
        parsed = urlparse(url)

        if not parsed.query:
            return url

        # Parse query parameters
        params = parse_qs(parsed.query, keep_blank_values=True)

        # Filter out tracking parameters (case-insensitive key comparison)
        cleaned_params = {k: v for k, v in params.items() if k.lower() not in self.TRACKING_PARAMS}

        # Reconstruct query string
        new_query = urlencode(cleaned_params, doseq=True) if cleaned_params else ""

        # Rebuild URL
        return urlunparse(
            (parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment)
        )

    def normalize_heading(self, heading: str) -> str:
        """
        Normalize heading text: trim, single line, no excess whitespace.

        Args:
            heading: Heading text

        Returns:
            Normalized heading
        """
        # Remove newlines
        heading = heading.replace("\n", " ")

        # Collapse spaces
        heading = re.sub(r"\s+", " ", heading)

        return heading.strip()
