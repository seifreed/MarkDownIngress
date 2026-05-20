"""URL value helpers for HTML and Markdown sanitization."""

from __future__ import annotations

import html
import unicodedata
from urllib.parse import unquote

_MAX_URL_DECODE_ITERATIONS = 5
_SAFE_DATA_URL_MEDIA_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
_URL_SCHEME_IGNORABLE_CHARS = frozenset(
    {
        "\u034f",  # Combining grapheme joiner
        "\u061c",  # Arabic letter mark
        "\u00ad",  # Soft hyphen
    }
)


def _decode_url_value_for_detection(value: object) -> str:
    decoded = str(value).strip()
    for _ in range(_MAX_URL_DECODE_ITERATIONS):
        previous = decoded
        decoded = html.unescape(unquote(decoded))
        if decoded == previous:
            break
    return decoded


def _is_ignorable_url_scheme_char(char: str) -> bool:
    return (
        char.isspace()
        or unicodedata.category(char) in {"Cc", "Cf", "Mn"}
        or char in _URL_SCHEME_IGNORABLE_CHARS
    )


def normalize_url_value_for_scheme_detection(value: object) -> str:
    """Return a normalized URL value suitable for dangerous-scheme checks."""
    decoded = _decode_url_value_for_detection(value)
    normalized = unicodedata.normalize("NFKC", decoded).strip().lower()
    return "".join(char for char in normalized if not _is_ignorable_url_scheme_char(char))


def _is_safe_data_url(clean_value: str) -> bool:
    data_content = clean_value[5:]
    comma_pos = data_content.find(",")
    media_type_part = data_content[:comma_pos].split(";", 1)[0].strip() if comma_pos != -1 else ""
    if not media_type_part:
        # RFC 2397: data:,hello defaults to text/plain.
        media_type_part = "text/plain"
    return media_type_part in _SAFE_DATA_URL_MEDIA_TYPES


def dangerous_url_scheme(value: object) -> str | None:
    """Classify dangerous URL schemes after decoding common obfuscation."""
    clean_value = normalize_url_value_for_scheme_detection(value)
    if clean_value.startswith("javascript:"):
        return "javascript"
    if clean_value.startswith("vbscript:"):
        return "vbscript"
    if clean_value.startswith("data:") and not _is_safe_data_url(clean_value):
        return "data"
    return None
