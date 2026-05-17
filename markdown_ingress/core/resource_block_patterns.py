"""Resource blocking URL normalization and pattern matching."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

# Resource types that can be blocked
BLOCKED_RESOURCE_TYPES = [
    "image",  # Block images (we only need text)
    "font",  # Block fonts (faster)
    "media",  # Block videos/audio
    "stylesheet",  # Optionally block CSS
]

# Domain patterns commonly used for ads and tracking
_TRACKER_DOMAINS = [
    "google-analytics.com",
    "googletagmanager.com",
    "facebook.net",
    "scorecardresearch.com",
    "quantserve.com",
    "hotjar.com",
    "mouseflow.com",
    "fullstory.com",
    "clarity.ms",
    "segment.io",
    "cdn.segment.com",
    "api.segment.com",
    "mixpanel.com",
    "amplitude.com",
]

_AD_DOMAINS = [
    "doubleclick.net",
    "googlesyndication.com",
    "adservice.google",
    "adnxs.com",
    "adsrvr.org",
    "criteo.com",
    "taboola.com",
    "outbrain.com",
]

_TRACKER_HOST_PATH_PATTERNS = [
    ("facebook.com", "/tr"),
]

BLOCKED_DOMAINS = (
    _TRACKER_DOMAINS + [f"{host}{path}" for host, path in _TRACKER_HOST_PATH_PATTERNS] + _AD_DOMAINS
)

# Patterns matched against the URL domain only (not the full path)
# to avoid false positives like "loads" matching "ads".
_TRACKER_DOMAIN_ONLY_PATTERNS = [
    "analytics.",
    "tracker.",
    "telemetry.",
]

_AD_DOMAIN_ONLY_PATTERNS = [
    "ads.",
    ".ads.",
]

# Path-level patterns: matched against the full URL but use boundary-aware
# patterns (slash or dot prefix) to avoid substring false positives.
_TRACKER_PATH_PATTERNS = [
    "/tracking.",
    "/tracking/",
    "/pixel.",
    "/pixel/",
    "/beacon.",
    "/beacon/",
    "/beacon?",
]

_DOMAIN_ONLY_PATTERNS = _AD_DOMAIN_ONLY_PATTERNS + _TRACKER_DOMAIN_ONLY_PATTERNS
_PATH_PATTERNS = list(_TRACKER_PATH_PATTERNS)


@dataclass(frozen=True)
class ResourceUrlParts:
    """Decoded URL parts used by resource block pattern matching."""

    domain: str
    path: str
    path_with_query: str


def _decode_url_fully(url: str) -> str:
    """Decode URL recursively to handle multi-level encoding."""
    decoded = unquote(url)
    max_iterations = 10
    iterations = 0
    while decoded != unquote(decoded) and iterations < max_iterations:
        decoded = unquote(decoded)
        iterations += 1
    return decoded.lower()


def extract_resource_url_parts(url: str) -> ResourceUrlParts:
    """Return decoded URL parts or raise ValueError for malformed resource URLs."""
    try:
        parts = urlsplit(_decode_url_fully(url))
        domain = (parts.hostname or "").lower().rstrip(".")
    except ValueError as exc:
        raise ValueError("Malformed resource URL") from exc

    if not domain:
        raise ValueError("Resource URL does not contain a host")

    path = parts.path or "/"
    path_with_query = path if not parts.query else f"{path}?{parts.query}"
    return ResourceUrlParts(domain=domain, path=path, path_with_query=path_with_query)


def match_host_patterns(domain: str, patterns: list[str]) -> str | None:
    """Match full-domain host patterns with subdomain boundary support."""
    for pattern in patterns:
        if "/" in pattern:
            continue
        if domain == pattern:
            return pattern
        if domain.endswith(f".{pattern}"):
            return pattern
    return None


def match_host_path_patterns(domain: str, path: str, patterns: list[tuple[str, str]]) -> str | None:
    """Match known tracker endpoints that depend on both host and path."""
    for host_pattern, path_pattern in patterns:
        if not (domain == host_pattern or domain.endswith(f".{host_pattern}")):
            continue
        if path == path_pattern or path.startswith(f"{path_pattern}/"):
            return f"{host_pattern}{path_pattern}"
    return None


def match_domain_only_patterns(domain: str, patterns: list[str]) -> str | None:
    """Match boundary-aware domain fragments."""
    labels = [label for label in domain.split(".") if label]
    for pattern in patterns:
        normalized = pattern.strip(".")
        if normalized and normalized in labels:
            return pattern
    return None


def match_path_patterns(path_with_query: str, patterns: list[str]) -> str | None:
    """Match tracking paths with segment boundaries instead of raw substrings."""
    stems_to_suffixes: dict[str, set[str]] = {}
    for pattern in patterns:
        if not pattern.startswith("/") or len(pattern) < 3:
            continue
        stems_to_suffixes.setdefault(pattern[1:-1], set()).add(pattern[-1])

    if not stems_to_suffixes:
        return None

    pattern_re = re.compile(
        rf"(?:^|/)(?P<stem>{'|'.join(map(re.escape, stems_to_suffixes))})(?P<suffix>[./?])"
    )
    match = pattern_re.search(path_with_query)
    if match is None:
        return None

    stem = match.group("stem")
    suffix = match.group("suffix")
    if suffix in stems_to_suffixes.get(stem, set()):
        return f"/{stem}{suffix}"
    return None
