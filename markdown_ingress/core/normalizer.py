"""
Content normalization module
"""

import re
import unicodedata
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


class Normalizer:  # implements INormalizer protocol
    """Normalize content for deterministic output"""

    # Zero-width and invisible Unicode characters
    ZERO_WIDTH_CHARS = [
        "\u200b",  # Zero-width space
        "\u200c",  # Zero-width non-joiner
        "\u200d",  # Zero-width joiner
        "\ufeff",  # Zero-width no-break space (BOM)
        "\u2060",  # Word joiner
        "\u180e",  # Mongolian vowel separator
    ]

    # Common tracking parameters
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
        "ref",
        "source",
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
        return self.zero_width_pattern.sub("", text)

    def normalize_whitespace(self, text: str) -> str:
        """
        Normalize whitespace: collapse multiple spaces, normalize line breaks.

        Args:
            text: Input text

        Returns:
            Text with normalized whitespace
        """
        # Replace multiple newlines with double newline (paragraph separator)
        text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)

        # Collapse multiple spaces on same line
        text = re.sub(r"[ \t]+", " ", text)

        # Remove trailing whitespace on lines
        text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)

        # Remove leading whitespace on lines (except paragraph starts)
        text = re.sub(r"^[ \t]+", "", text, flags=re.MULTILINE)

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

        # Filter out tracking parameters
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
