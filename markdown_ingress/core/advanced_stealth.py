"""
Advanced stealth techniques for Playwright to bypass sophisticated bot detection.

This module provides comprehensive stealth capabilities to evade:
- Cloudflare bot detection
- Browser fingerprinting
- WebDriver detection
- Canvas/WebGL fingerprinting
- Behavioral analysis systems

Key Features:
- JavaScript injection to patch detection vectors
- Randomized browser fingerprints
- Advanced context options with realistic behavior
- Ultra-stealth browser launch arguments
- AdvancedStealthRenderer for production use

Note: This module now imports from stealth submodules for better organization.
All public APIs are re-exported here for backward compatibility.
"""

import asyncio
import logging
import time
from typing import Any, Literal, cast

# Import all public APIs from stealth submodules
from markdown_ingress.core import stealth as _stealth
from markdown_ingress.core.stealth import (
    AdvancedStealthConfig,
    get_advanced_context_options,
    get_advanced_stealth_config,
    inject_stealth_post_nav,
    inject_stealth_pre_nav,
)
from markdown_ingress.models import FetchResult

# Backward-compatible re-exports expected by tests and older integrations.
ADVANCED_USER_AGENTS = _stealth.ADVANCED_USER_AGENTS
ADVANCED_VIEWPORT_SIZES = _stealth.ADVANCED_VIEWPORT_SIZES
REALISTIC_HEADERS = _stealth.REALISTIC_HEADERS
STEALTH_JS_INJECTION = _stealth.STEALTH_JS_INJECTION
STEALTH_JS_POST_LOAD = _stealth.STEALTH_JS_POST_LOAD
WaitUntil = Literal["commit", "domcontentloaded", "load", "networkidle"]
TIMEZONES = _stealth.TIMEZONES
ULTRA_STEALTH_ARGS = _stealth.ULTRA_STEALTH_ARGS
logger = logging.getLogger(__name__)


async def _close_async_resource(resource: Any | None, label: str) -> None:
    """Close an async Playwright resource without masking earlier failures."""
    if resource is None:
        return
    try:
        await resource.close()
    except Exception as exc:  # pragma: no cover - defensive cleanup path
        logger.warning("Failed to close %s cleanly: %s", label, exc)


# ============================================================================
# ADVANCED STEALTH RENDERER
# ============================================================================


class AdvancedStealthRenderer:
    """
    Advanced Playwright renderer with maximum bot detection evasion.

    Features:
    - Ultra stealth browser arguments
    - Comprehensive JavaScript injection
    - Randomized browser fingerprints
    - Realistic HTTP headers
    - Smart waiting strategies
    - Automatic retry logic
    - HTTP/2 fallback support

    Example:
        >>> renderer = AdvancedStealthRenderer()
        >>> result = await renderer.render("https://example.com")
        >>> print(result.html)
    """

    DEFAULT_TIMEOUT = 30000  # milliseconds
    DEFAULT_WAIT_UNTIL = "networkidle"

    def __init__(
        self,
        timeout: float = 30.0,
        wait_until: str = "networkidle",
        headless: bool = True,
        randomize_fingerprint: bool = True,
        disable_http2: bool = False,
        stealth_config: AdvancedStealthConfig | None = None,
    ):
        """
        Initialize advanced stealth renderer.

        Args:
            timeout: Navigation timeout in seconds (default: 30.0)
            wait_until: When to consider navigation complete
                       ('networkidle', 'load', 'domcontentloaded')
            headless: Run browser in headless mode (default: True)
            randomize_fingerprint: Randomize user agent, viewport, etc.
            disable_http2: Disable HTTP/2 protocol (for compatibility)
            stealth_config: Optional custom stealth configuration
        """
        self.timeout = int(timeout * 1000)  # Convert to milliseconds
        self.wait_until = wait_until
        self.headless = headless
        self.randomize_fingerprint = randomize_fingerprint
        self.disable_http2 = disable_http2

        # Generate or use provided stealth config
        if stealth_config is None:
            self.stealth_config = get_advanced_stealth_config(randomize=randomize_fingerprint)
        else:
            self.stealth_config = stealth_config

    async def render(self, url: str) -> FetchResult:
        """
        Render URL using advanced stealth techniques.

        Includes automatic retry logic and HTTP/2 fallback.

        Args:
            url: Target URL to render

        Returns:
            FetchResult with rendered HTML and metadata

        Raises:
            ImportError: If playwright is not installed
            Exception: On unrecoverable errors
        """
        try:
            result = await self._render_with_browser(url)
            return result
        except Exception as e:
            error_str = str(e)
            # Check for HTTP/2 protocol error
            if (
                "ERR_HTTP2_PROTOCOL_ERROR" in error_str and not self.disable_http2
            ):  # pragma: no cover
                # Retry with HTTP/2 disabled
                retry_renderer = AdvancedStealthRenderer(  # pragma: no cover
                    timeout=self.timeout / 1000.0,
                    wait_until=self.wait_until,
                    headless=self.headless,
                    randomize_fingerprint=self.randomize_fingerprint,
                    disable_http2=True,
                    stealth_config=self.stealth_config,
                )
                result = await retry_renderer._render_with_browser(url)
                result.metadata["http2_fallback"] = True
                result.metadata["original_error"] = "ERR_HTTP2_PROTOCOL_ERROR"
                return result
            raise

    async def _render_with_browser(self, url: str) -> FetchResult:
        """
        Internal method to render URL with browser.

        Args:
            url: Target URL to render

        Returns:
            FetchResult with rendered HTML and metadata
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:  # pragma: no cover
            raise ImportError(  # pragma: no cover
                "Playwright is not installed. Install with: "
                "pip install 'markdown-ingress[render]' or "
                "pip install playwright && playwright install"
            )  # pragma: no cover

        start_time = time.perf_counter()

        async with async_playwright() as p:
            # Prepare browser arguments
            browser_args = self.stealth_config.browser_args.copy()

            # Add HTTP/2 disable flag if needed
            if self.disable_http2:
                browser_args.append("--disable-http2")

            # Launch browser with ultra stealth args
            launch_options: dict[str, object] = {
                "headless": self.headless,
                "args": browser_args,
                "ignore_default_args": ["--enable-automation"],
            }

            browser = None
            browser = await p.chromium.launch(**cast(dict[str, Any], launch_options))
            context = None
            page = None

            try:
                # Create context with advanced stealth options
                context_options = get_advanced_context_options(self.stealth_config)
                context = await browser.new_context(**context_options)

                try:
                    # Create page
                    page = await context.new_page()

                    # Inject stealth init scripts (before navigation)
                    await inject_stealth_pre_nav(page)

                    # Navigate to URL
                    response = await page.goto(
                        url, timeout=self.timeout, wait_until=cast(WaitUntil, self.wait_until)
                    )

                    # Run post-navigation stealth cleanup on the loaded page
                    await inject_stealth_post_nav(page)

                    # Additional wait for dynamic content
                    await page.wait_for_timeout(500)

                    # Get final URL (after redirects)
                    final_url = page.url

                    # Get status code
                    status_code = response.status if response else 200

                    # Get rendered HTML
                    html = await page.content()

                    # Get headers
                    headers = response.headers if response else {}

                    elapsed_ms = (time.perf_counter() - start_time) * 1000

                    # Build metadata
                    metadata = {
                        "renderer": "advanced_stealth_playwright",
                        "user_agent": self.stealth_config.user_agent,
                        "viewport": f"{self.stealth_config.viewport_width}x{self.stealth_config.viewport_height}",
                        "device_scale_factor": self.stealth_config.device_scale_factor,
                        "timezone": self.stealth_config.timezone,
                        "http2_disabled": self.disable_http2,
                        "stealth_injected": True,
                    }

                    return FetchResult(
                        html=html,
                        url=url,
                        status_code=status_code,
                        final_url=final_url,
                        headers=headers,
                        timing_ms=elapsed_ms,
                        metadata=metadata,
                    )

                finally:
                    await _close_async_resource(page, "page")
                    await _close_async_resource(context, "browser context")

            finally:
                await _close_async_resource(browser, "browser")

    def render_sync(self, url: str) -> FetchResult:
        """
        Synchronous wrapper for render().

        Args:
            url: Target URL to render

        Returns:
            FetchResult with rendered HTML
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.render(url))

        raise RuntimeError(
            "render_sync() cannot run inside an active event loop; await render() instead"
        )


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================


async def render_with_advanced_stealth(
    url: str,
    timeout: float = 30.0,
    headless: bool = True,
) -> FetchResult:
    """
    Convenience function to render a URL with advanced stealth.

    Args:
        url: Target URL to render
        timeout: Navigation timeout in seconds
        headless: Run browser in headless mode

    Returns:
        FetchResult with rendered HTML

    Example:
        >>> result = await render_with_advanced_stealth("https://example.com")
        >>> print(result.status_code)
        200
    """
    renderer = AdvancedStealthRenderer(
        timeout=timeout,
        headless=headless,
    )
    return await renderer.render(url)


def render_with_advanced_stealth_sync(
    url: str,
    timeout: float = 30.0,
    headless: bool = True,
) -> FetchResult:
    """
    Synchronous convenience function for advanced stealth rendering.

    Args:
        url: Target URL to render
        timeout: Navigation timeout in seconds
        headless: Run browser in headless mode

    Returns:
        FetchResult with rendered HTML

    Example:
        >>> result = render_with_advanced_stealth_sync("https://example.com")
        >>> print(result.html[:100])
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(render_with_advanced_stealth(url, timeout, headless))

    raise RuntimeError(
        "render_with_advanced_stealth_sync() cannot run inside an active event loop; "
        "await render_with_advanced_stealth() instead"
    )
