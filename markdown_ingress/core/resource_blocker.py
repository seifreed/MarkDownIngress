"""
Resource blocker for optimizing page load performance.

Blocks unnecessary resources like images, fonts, ads, and trackers
to speed up rendering and reduce bandwidth usage.
"""

import logging
import threading
from urllib.parse import unquote, urlsplit

logger = logging.getLogger(__name__)


def _decode_url_fully(url: str) -> str:
    """Decode URL recursively to handle multi-level encoding.

    This prevents bypasses where URLs are double or triple encoded
    to evade detection (e.g., %252e -> %2e -> . bypasses patterns
    looking for dots).

    Args:
        url: URL string that may contain percent-encoded characters.

    Returns:
        Fully decoded URL string (lowercase for consistent matching).
    """
    decoded = unquote(url)
    # Keep decoding until no change (handles double/triple encoding)
    # Limit iterations to prevent infinite loops on malicious input
    max_iterations = 10
    iterations = 0
    while decoded != unquote(decoded) and iterations < max_iterations:
        decoded = unquote(decoded)
        iterations += 1
    return decoded.lower()


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

BLOCKED_DOMAINS = _TRACKER_DOMAINS + [f"{host}{path}" for host, path in _TRACKER_HOST_PATH_PATTERNS] + _AD_DOMAINS

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


class ResourceBlocker:
    """
    Blocks unnecessary resources during page load to speed up rendering.

    This class intercepts network requests and blocks resources based on
    resource type (images, fonts, media) and domain patterns (ads, trackers).
    """

    def __init__(
        self,
        block_images: bool = True,
        block_fonts: bool = True,
        block_media: bool = True,
        block_css: bool = False,
        block_ads: bool = True,
        block_trackers: bool = True,
        custom_blocked_domains: list[str] | None = None,
    ):
        """
        Initialize the resource blocker.

        Args:
            block_images: Block image requests
            block_fonts: Block font requests
            block_media: Block media (video/audio) requests
            block_css: Block CSS stylesheets (may break layout)
            block_ads: Block advertising domains
            block_trackers: Block analytics and tracking domains
            custom_blocked_domains: Additional domain patterns to block
        """
        self.block_images = block_images
        self.block_fonts = block_fonts
        self.block_media = block_media
        self.block_css = block_css
        self.block_ads = block_ads
        self.block_trackers = block_trackers

        # Combine default and custom blocked domains
        self.blocked_domains = BLOCKED_DOMAINS.copy()
        if custom_blocked_domains:
            self.blocked_domains.extend(custom_blocked_domains)
        self._custom_blocked_domains = list(custom_blocked_domains or [])

        # Statistics
        self._stats_lock = threading.Lock()
        self.blocked_count = 0
        self.total_count = 0
        self.blocked_by_type: dict[str, int] = {}
        self.blocked_by_domain: dict[str, int] = {}

    async def setup_blocking(self, page):
        """
        Setup request interception on a Playwright page.

        Args:
            page: Playwright Page object
        """
        await page.route("**/*", self._handle_route)
        logger.debug("Resource blocking enabled on page")

    async def _handle_route(self, route):
        """
        Intercept and block requests based on configured rules.

        Args:
            route: Playwright Route object
        """
        should_block = False
        matched_domain: str | None = None
        try:
            request = route.request
            resource_type = request.resource_type
            url = request.url

            # BUG FIX: Calculate should_block BEFORE acquiring lock to avoid
            # thread contention. URL parsing and decoding can be slow.
            # Returns tuple of (should_block, matched_domain) to avoid race condition
            should_block, matched_domain = self._should_block(resource_type, url)

            # Update stats atomically
            with self._stats_lock:
                self.total_count += 1
                if should_block:
                    self.blocked_count += 1
                    self.blocked_by_type[resource_type] = self.blocked_by_type.get(resource_type, 0) + 1
                    # BUG FIX: Update blocked_by_domain under lock to prevent race condition
                    if matched_domain:
                        self.blocked_by_domain[matched_domain] = self.blocked_by_domain.get(matched_domain, 0) + 1

            if should_block:
                logger.debug(f"Blocked {resource_type}: {url[:100]}")
                await route.abort()
            else:
                await route.continue_()

        except Exception as e:
            # Security: On exception, default to blocking to prevent bypass attacks
            # where attacker crafts malformed URL to trigger exception and bypass blocking
            logger.warning(f"Error in route handler (defaulting to block): {e}")
            try:
                await route.abort()
            except Exception:
                # Route may already be handled
                pass

    def _should_block(self, resource_type: str, url: str) -> tuple[bool, str | None]:
        """
        Determine if a request should be blocked based on type and URL.

        Args:
            resource_type: Type of resource (image, font, media, etc.)
            url: Request URL

        Returns:
            Tuple of (should_block, matched_domain) where matched_domain is the
            domain pattern that triggered the block, or None if not blocked by domain.
        """
        # Block by resource type
        if self.block_images and resource_type == "image":
            return True, None

        if self.block_fonts and resource_type == "font":
            return True, None

        if self.block_media and resource_type == "media":
            return True, None

        if self.block_css and resource_type == "stylesheet":
            return True, None

        # Block by domain patterns (for ads and trackers)
        if self.block_ads or self.block_trackers:
            # Decode URL recursively to prevent encoding-based bypasses
            # (e.g., /tracking%252e bypasses /tracking. by double encoding)
            try:
                url_decoded = _decode_url_fully(url)
            except Exception:
                # Malformed URL encoding - block for security
                return True, None

            try:
                parts = urlsplit(url_decoded)
                domain = (parts.hostname or "").lower()
            except Exception:
                # Malformed URL - block for security
                return True, None

            # Security: Ensure domain was successfully extracted
            if not domain:
                # Empty or malformed domain - block for security
                return True, None

            path = parts.path or "/"

            if self.block_trackers:
                matched = self._match_host_patterns(domain, _TRACKER_DOMAINS + self._custom_blocked_domains)
                if matched is not None:
                    return True, matched
                matched = self._match_host_path_patterns(domain, path, _TRACKER_HOST_PATH_PATTERNS)
                if matched is not None:
                    return True, matched
                matched = self._match_domain_only_patterns(domain, _TRACKER_DOMAIN_ONLY_PATTERNS)
                if matched is not None:
                    return True, matched
                matched = self._match_path_patterns(url_decoded, _TRACKER_PATH_PATTERNS)
                if matched is not None:
                    return True, matched

            if self.block_ads:
                matched = self._match_host_patterns(domain, _AD_DOMAINS + self._custom_blocked_domains)
                if matched is not None:
                    return True, matched
                matched = self._match_domain_only_patterns(domain, _AD_DOMAIN_ONLY_PATTERNS)
                if matched is not None:
                    return True, matched

        return False, None

    @staticmethod
    def _match_host_patterns(domain: str, patterns: list[str]) -> str | None:
        """Match full-domain host patterns with subdomain boundary support."""
        for pattern in patterns:
            if "/" in pattern:
                continue
            if domain == pattern:
                return pattern
            if domain.endswith(f".{pattern}"):
                return pattern
            if domain.startswith(f"{pattern}."):
                return pattern
        return None

    @staticmethod
    def _match_host_path_patterns(domain: str, path: str, patterns: list[tuple[str, str]]) -> str | None:
        """Match known tracker endpoints that depend on both host and path."""
        for host_pattern, path_pattern in patterns:
            if not (domain == host_pattern or domain.endswith(f".{host_pattern}")):
                continue
            if path == path_pattern or path.startswith(f"{path_pattern}/"):
                return f"{host_pattern}{path_pattern}"
        return None

    @staticmethod
    def _match_domain_only_patterns(domain: str, patterns: list[str]) -> str | None:
        """Match boundary-aware domain fragments."""
        for pattern in patterns:
            if domain.startswith(pattern) or f".{pattern.lstrip('.')}" in f".{domain}":
                return pattern
        return None

    @staticmethod
    def _match_path_patterns(url_decoded: str, patterns: list[str]) -> str | None:
        """Match boundary-aware tracking paths against the fully decoded URL."""
        for pattern in patterns:
            if pattern in url_decoded:
                return pattern
        return None

    def get_stats(self) -> dict:
        """
        Get blocking statistics.

        Returns:
            Dictionary with blocking statistics
        """
        block_rate = (self.blocked_count / self.total_count * 100) if self.total_count > 0 else 0

        return {
            "blocked_requests": self.blocked_count,
            "total_requests": self.total_count,
            "allowed_requests": self.total_count - self.blocked_count,
            "block_rate_pct": round(block_rate, 2),
            "blocked_by_type": dict(self.blocked_by_type),
            "blocked_by_domain": dict(self.blocked_by_domain),
        }

    def reset_stats(self):
        """Reset blocking statistics."""
        with self._stats_lock:
            self.blocked_count = 0
            self.total_count = 0
            self.blocked_by_type = {}
            self.blocked_by_domain = {}
