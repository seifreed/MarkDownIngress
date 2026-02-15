"""
Resource blocker for optimizing page load performance.

Blocks unnecessary resources like images, fonts, ads, and trackers
to speed up rendering and reduce bandwidth usage.
"""

import logging

logger = logging.getLogger(__name__)


# Resource types that can be blocked
BLOCKED_RESOURCE_TYPES = [
    "image",  # Block images (we only need text)
    "font",  # Block fonts (faster)
    "media",  # Block videos/audio
    "stylesheet",  # Optionally block CSS
]

# Domain patterns commonly used for ads and tracking
BLOCKED_DOMAINS = [
    "google-analytics.com",
    "googletagmanager.com",
    "facebook.com/tr",
    "doubleclick.net",
    "googlesyndication.com",
    "adservice.google",
    "facebook.net",
    "scorecardresearch.com",
    "quantserve.com",
    "hotjar.com",
    "mouseflow.com",
    "fullstory.com",
    "clarity.ms",
    "segment.com",
    "mixpanel.com",
    "amplitude.com",
    "ads",
    "analytics",
    "tracking",
    "tracker",
    "pixel",
    "beacon",
    "telemetry",
]


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

        # Statistics
        self.blocked_count = 0
        self.total_count = 0
        self.blocked_by_type = {}
        self.blocked_by_domain = {}

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
        try:
            request = route.request
            resource_type = request.resource_type
            url = request.url

            self.total_count += 1

            # Check if should block
            if self._should_block(resource_type, url):
                self.blocked_count += 1

                # Track statistics
                self.blocked_by_type[resource_type] = self.blocked_by_type.get(resource_type, 0) + 1

                logger.debug(f"Blocked {resource_type}: {url[:100]}")
                await route.abort()
            else:
                await route.continue_()

        except Exception as e:
            # If there's an error, allow the request to continue
            logger.warning(f"Error in route handler: {e}")
            try:
                await route.continue_()
            except Exception:
                # Route may already be handled
                pass

    def _should_block(self, resource_type: str, url: str) -> bool:
        """
        Determine if a request should be blocked based on type and URL.

        Args:
            resource_type: Type of resource (image, font, media, etc.)
            url: Request URL

        Returns:
            True if the request should be blocked, False otherwise
        """
        # Block by resource type
        if self.block_images and resource_type == "image":
            return True

        if self.block_fonts and resource_type == "font":
            return True

        if self.block_media and resource_type == "media":
            return True

        if self.block_css and resource_type == "stylesheet":
            return True

        # Block by domain patterns (for ads and trackers)
        if self.block_ads or self.block_trackers:
            url_lower = url.lower()
            for pattern in self.blocked_domains:
                if pattern in url_lower:
                    # Track which domain pattern matched
                    self.blocked_by_domain[pattern] = self.blocked_by_domain.get(pattern, 0) + 1
                    return True

        return False

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
        self.blocked_count = 0
        self.total_count = 0
        self.blocked_by_type = {}
        self.blocked_by_domain = {}
