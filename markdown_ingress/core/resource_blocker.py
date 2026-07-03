"""
Resource blocker for optimizing page load performance.

Blocks unnecessary resources like images, fonts, ads, and trackers
to speed up rendering and reduce bandwidth usage.
"""

import logging
import threading
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from markdown_ingress.core.resource_block_patterns import (
    _AD_DOMAIN_ONLY_PATTERNS,
    _AD_DOMAINS,
    _TRACKER_DOMAIN_ONLY_PATTERNS,
    _TRACKER_DOMAINS,
    _TRACKER_HOST_PATH_PATTERNS,
    _TRACKER_PATH_PATTERNS,
    extract_resource_url_parts,
    match_domain_only_patterns,
    match_host_path_patterns,
    match_host_patterns,
    match_path_patterns,
)
from markdown_ingress.core.resource_block_patterns import (
    _DOMAIN_ONLY_PATTERNS as _DOMAIN_ONLY_PATTERNS,
)
from markdown_ingress.core.resource_block_patterns import (
    _PATH_PATTERNS as _PATH_PATTERNS,
)
from markdown_ingress.core.resource_block_patterns import (
    BLOCKED_DOMAINS as BLOCKED_DOMAINS,
)
from markdown_ingress.core.resource_block_patterns import (
    BLOCKED_RESOURCE_TYPES as BLOCKED_RESOURCE_TYPES,
)
from markdown_ingress.core.resource_block_patterns import (
    _decode_url_fully as _decode_url_fully,
)
from markdown_ingress.core.ssrf import (
    dns_pin_for_validated_http_url,
    dns_pin_matches_hostname,
    normalize_domain_pattern,
    resolve_allow_local_urls,
    validate_http_url_no_ssrf_with_dns_check,
)

logger = logging.getLogger(__name__)
_BROWSER_INTERNAL_SCHEMES = frozenset({"about", "blob", "data"})
_SSRF_BLOCK_REASON = "ssrf_protection"
RESOURCE_ROUTE_ERRORS: tuple[type[Exception], ...] = (
    AttributeError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)


class ResourceBlocker:
    """
    Blocks unnecessary resources during page load to speed up rendering.

    This class intercepts network requests and blocks resources based on
    resource type (images, fonts, media) and domain patterns (ads, trackers).
    """

    # Policy constructor keeps resource and SSRF controls explicit for callers.
    def __init__(  # noqa: PLR0913
        self,
        block_images: bool = True,
        block_fonts: bool = True,
        block_media: bool = True,
        block_css: bool = False,
        block_ads: bool = True,
        block_trackers: bool = True,
        custom_blocked_domains: list[str] | None = None,
        allow_local_urls: bool | None = None,
        validate_ssrf: bool = True,
        dns_pins: Mapping[str, str] | None = None,
        enforce_dns_pinning: bool = True,
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
            allow_local_urls: Opt-in override for SSRF checks on local/private URLs
            validate_ssrf: Validate allowed HTTP(S) requests against SSRF destinations
            dns_pins: Browser DNS pins already installed for this page
            enforce_dns_pinning: Block DNS-backed requests that cannot use an installed pin
        """
        self.block_images = block_images
        self.block_fonts = block_fonts
        self.block_media = block_media
        self.block_css = block_css
        self.block_ads = block_ads
        self.block_trackers = block_trackers
        self.allow_local_urls = resolve_allow_local_urls(allow_local_urls)
        self.validate_ssrf = validate_ssrf
        self.dns_pins = {
            normalize_domain_pattern(hostname): str(address)
            for hostname, address in (dns_pins or {}).items()
            if normalize_domain_pattern(hostname) and address
        }
        self.enforce_dns_pinning = enforce_dns_pinning

        self._custom_blocked_domains = [
            normalized
            for domain in (custom_blocked_domains or [])
            if (normalized := normalize_domain_pattern(domain))
        ]

        # Statistics
        self._stats_lock = threading.Lock()
        self.blocked_count = 0
        self.total_count = 0
        self.blocked_by_type: dict[str, int] = {}
        self.blocked_by_domain: dict[str, int] = {}

    async def setup_blocking(self, page: Any) -> None:
        """
        Setup request interception on a Playwright page.

        Args:
            page: Playwright Page object
        """
        await page.route("**/*", self._handle_route)
        logger.debug("Resource blocking enabled on page")

    async def _handle_route(self, route: Any) -> None:
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

            # Keep URL parsing/decoding outside the stats lock.
            should_block, matched_domain = self._should_block(resource_type, url)

            # Update stats atomically
            with self._stats_lock:
                self.total_count += 1
                if should_block:
                    self.blocked_count += 1
                    self.blocked_by_type[resource_type] = (
                        self.blocked_by_type.get(resource_type, 0) + 1
                    )
                    if matched_domain:
                        self.blocked_by_domain[matched_domain] = (
                            self.blocked_by_domain.get(matched_domain, 0) + 1
                        )

            if should_block:
                logger.debug(f"Blocked {resource_type}: {url[:100]}")
                await route.abort()
            else:
                await route.continue_()

        except RESOURCE_ROUTE_ERRORS as e:
            # Security: On exception, default to blocking to prevent bypass attacks
            # where attacker crafts malformed URL to trigger exception and bypass blocking
            logger.warning(f"Error in route handler (defaulting to block): {e}")
            with self._stats_lock:
                if not should_block:
                    # route.continue_() threw — request was aborted despite being classified
                    # as allowed; compensate so blocked_count stays consistent with actual actions
                    self.blocked_count += 1
            try:
                await route.abort()
            except RESOURCE_ROUTE_ERRORS as exc:
                # Route may already be handled
                logger.debug("Route abort already handled: %s", exc)

    def _should_block_resource_type(self, resource_type: str) -> tuple[bool, str | None] | None:
        if self.block_images and resource_type == "image":
            return True, None
        if self.block_fonts and resource_type == "font":
            return True, None
        if self.block_media and resource_type == "media":
            return True, None
        if self.block_css and resource_type == "stylesheet":
            return True, None
        return None

    def _should_check_domain_patterns(self) -> bool:
        return self.block_ads or self.block_trackers or bool(self._custom_blocked_domains)

    def _should_block_by_domain_patterns(self, url: str) -> tuple[bool, str | None] | None:
        if not self._should_check_domain_patterns():
            return None
        try:
            url_parts = extract_resource_url_parts(url)
        except ValueError:
            return True, None

        matched = self._match_host_patterns(url_parts.domain, self._custom_blocked_domains)
        if matched is not None:
            return True, matched

        tracker_decision = self._should_block_tracker_url(url_parts)
        if tracker_decision is not None:
            return tracker_decision
        return self._should_block_ad_url(url_parts)

    def _should_block_tracker_url(self, url_parts) -> tuple[bool, str | None] | None:
        if not self.block_trackers:
            return None
        matched = self._match_host_patterns(url_parts.domain, _TRACKER_DOMAINS)
        if matched is not None:
            return True, matched
        matched = self._match_host_path_patterns(
            url_parts.domain,
            url_parts.path,
            _TRACKER_HOST_PATH_PATTERNS,
        )
        if matched is not None:
            return True, matched
        matched = self._match_domain_only_patterns(
            url_parts.domain,
            _TRACKER_DOMAIN_ONLY_PATTERNS,
        )
        if matched is not None:
            return True, matched
        matched = self._match_path_patterns(
            url_parts.path_with_query,
            _TRACKER_PATH_PATTERNS,
        )
        if matched is not None:
            return True, matched
        return None

    def _should_block_ad_url(self, url_parts) -> tuple[bool, str | None] | None:
        if not self.block_ads:
            return None
        matched = self._match_host_patterns(url_parts.domain, _AD_DOMAINS)
        if matched is not None:
            return True, matched
        matched = self._match_domain_only_patterns(
            url_parts.domain,
            _AD_DOMAIN_ONLY_PATTERNS,
        )
        if matched is not None:
            return True, matched
        return None

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
        scheme_decision = self._should_block_non_http_scheme(url)
        if scheme_decision is not None:
            return scheme_decision

        resource_type_decision = self._should_block_resource_type(resource_type)
        if resource_type_decision is not None:
            return resource_type_decision

        domain_decision = self._should_block_by_domain_patterns(url)
        if domain_decision is not None:
            return domain_decision

        ssrf_decision = self._should_block_http_for_ssrf(url)
        if ssrf_decision is not None:
            return ssrf_decision

        return False, None

    def _should_block_non_http_scheme(self, url: str) -> tuple[bool, str | None] | None:
        """Classify schemes that Playwright may route before HTTP(S) validation."""
        if not self.validate_ssrf:
            return None
        try:
            scheme = urlsplit(url).scheme.lower()
        except ValueError:
            return True, _SSRF_BLOCK_REASON
        if scheme in _BROWSER_INTERNAL_SCHEMES:
            return False, None
        if scheme not in {"http", "https"}:
            return True, _SSRF_BLOCK_REASON
        return None

    def _should_block_http_for_ssrf(self, url: str) -> tuple[bool, str | None] | None:
        """Block HTTP(S) requests that fail SSRF validation."""
        if not self.validate_ssrf:
            return None
        try:
            validated_url = validate_http_url_no_ssrf_with_dns_check(
                url,
                allow_local=self.allow_local_urls,
            )
        except ValueError:
            return True, _SSRF_BLOCK_REASON
        pin = dns_pin_for_validated_http_url(url, validated_url)
        if pin is not None and self.enforce_dns_pinning:
            hostname, _pinned_address = pin
            installed_pin = self.dns_pins.get(hostname)
            if installed_pin is None:
                return True, _SSRF_BLOCK_REASON
            try:
                pin_is_valid = dns_pin_matches_hostname(
                    hostname,
                    installed_pin,
                    allow_local=self.allow_local_urls,
                )
            except ValueError:
                return True, _SSRF_BLOCK_REASON
            if not pin_is_valid:
                return True, _SSRF_BLOCK_REASON
        return None

    _match_host_patterns = staticmethod(match_host_patterns)
    _match_host_path_patterns = staticmethod(match_host_path_patterns)
    _match_domain_only_patterns = staticmethod(match_domain_only_patterns)
    _match_path_patterns = staticmethod(match_path_patterns)

    def get_stats(self) -> dict:
        """
        Get blocking statistics.

        Returns:
            Dictionary with blocking statistics
        """
        with self._stats_lock:
            blocked = self.blocked_count
            total = self.total_count
            by_type = dict(self.blocked_by_type)
            by_domain = dict(self.blocked_by_domain)

        block_rate = (blocked / total * 100) if total > 0 else 0

        return {
            "blocked_requests": blocked,
            "total_requests": total,
            "allowed_requests": total - blocked,
            "block_rate_pct": round(block_rate, 2),
            "blocked_by_type": by_type,
            "blocked_by_domain": by_domain,
        }

    def reset_stats(self):
        """Reset blocking statistics."""
        with self._stats_lock:
            self.blocked_count = 0
            self.total_count = 0
            self.blocked_by_type = {}
            self.blocked_by_domain = {}
