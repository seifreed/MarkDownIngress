"""
Playwright-based renderer for SPA/JavaScript-heavy sites
"""

import asyncio
import logging
import tempfile
import time

from markdown_ingress.core.resource_blocker import ResourceBlocker
from markdown_ingress.models import FetchResult

logger = logging.getLogger(__name__)

try:
    from markdown_ingress.core.stealth import (
        STEALTH_BROWSER_ARGS,
        get_context_options,
        get_stealth_config,
    )

    STEALTH_AVAILABLE = True
except ImportError:
    STEALTH_AVAILABLE = False


class Renderer:  # implements IRenderer protocol
    """Headless browser renderer using Playwright for JavaScript-heavy sites"""

    DEFAULT_TIMEOUT = 30000  # milliseconds
    DEFAULT_WAIT_UNTIL = "networkidle"  # or "load", "domcontentloaded"

    # Progressive timeout strategies (state, timeout_ms)
    LOAD_STRATEGIES = [
        ("networkidle", 90000),  # Wait for network to be idle
        ("domcontentloaded", 180000),  # Wait for DOM
        ("load", 300000),  # Wait for everything
    ]

    # Fallback selectors for content detection
    CONTENT_SELECTORS = [
        "article",
        "main",
        '[role="main"]',
        ".content",
        "#content",
        "body",
    ]

    def __init__(
        self,
        timeout: float = 30.0,
        wait_until: str = "networkidle",
        headless: bool = True,
        user_agent: str | None = None,
        stealth: bool = False,
        disable_http2: bool = False,
        extreme_mode: bool = False,
        block_resources: bool = True,
        block_images: bool = True,
        block_fonts: bool = True,
        block_media: bool = True,
        block_ads: bool = True,
        block_trackers: bool = True,
        screenshot: str | None = None,
    ):
        """
        Initialize Playwright renderer.

        Args:
            timeout: Navigation timeout in seconds
            wait_until: When to consider navigation complete ('networkidle', 'load', 'domcontentloaded')
            headless: Run browser in headless mode
            user_agent: Custom user agent (optional)
            stealth: Enable stealth mode to avoid bot detection
            disable_http2: Disable HTTP/2 protocol (used for fallback)
            extreme_mode: Enable extreme timeouts (up to 300s) and patient waiting
            block_resources: Enable resource blocking for faster loads
            block_images: Block images when resource blocking enabled
            block_fonts: Block fonts when resource blocking enabled
            block_media: Block media (video/audio) when resource blocking enabled
            block_ads: Block advertising domains when resource blocking enabled
            block_trackers: Block analytics/tracking domains when resource blocking enabled
            screenshot: Screenshot path (str) or True for temp file, None to disable
        """
        self.timeout = int(timeout * 1000)  # Convert to milliseconds
        self.wait_until = wait_until
        self.headless = headless
        self.stealth = stealth
        self.disable_http2 = disable_http2
        self.extreme_mode = extreme_mode
        self.user_agent = (
            user_agent
            or "Mozilla/5.0 (compatible; MarkDownIngress/0.2; +https://github.com/markdowningress)"
        )
        self.block_resources = block_resources
        self.block_images = block_images
        self.block_fonts = block_fonts
        self.block_media = block_media
        self.block_ads = block_ads
        self.block_trackers = block_trackers
        self.screenshot = screenshot

    async def render(self, url: str) -> FetchResult:
        """
        Render URL using Playwright and return HTML after JavaScript execution.
        Includes automatic HTTP/2 fallback on protocol errors and progressive timeout strategies.

        Args:
            url: Target URL to render

        Returns:
            FetchResult with rendered HTML and metadata

        Raises:
            ImportError: If playwright is not installed
            playwright._impl._errors.TimeoutError: On timeout
            playwright._impl._errors.Error: On navigation errors (except HTTP/2 errors which trigger fallback)
        """
        # Use progressive timeout strategy if extreme mode is enabled
        if self.extreme_mode:
            return await self._render_with_progressive_timeout(url)

        try:
            result = await self._render_with_browser(url)
            return result
        except Exception as e:
            error_str = str(e)
            # Check for HTTP/2 protocol error
            if "ERR_HTTP2_PROTOCOL_ERROR" in error_str and not self.disable_http2:
                # Retry with HTTP/2 disabled
                retry_renderer = Renderer(
                    timeout=self.timeout / 1000.0,  # Convert back to seconds
                    wait_until=self.wait_until,
                    headless=self.headless,
                    user_agent=self.user_agent,
                    stealth=self.stealth,
                    disable_http2=True,
                    extreme_mode=self.extreme_mode,
                    block_resources=self.block_resources,
                    block_images=self.block_images,
                    block_fonts=self.block_fonts,
                    block_media=self.block_media,
                    block_ads=self.block_ads,
                    block_trackers=self.block_trackers,
                    screenshot=self.screenshot,
                )
                result = await retry_renderer._render_with_browser(url)
                # Mark as HTTP/2 fallback
                result.metadata["http2_fallback"] = True
                result.metadata["original_error"] = "ERR_HTTP2_PROTOCOL_ERROR"
                return result
            # Re-raise if not HTTP/2 error or if already retried
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
        except ImportError:
            raise ImportError(
                "Playwright is not installed. Install with: "
                "pip install 'markdown-ingress[render]' or pip install playwright && playwright install"
            )

        start_time = time.perf_counter()

        async with async_playwright() as p:
            # Prepare browser arguments
            browser_args = []

            # Add stealth mode arguments if enabled
            if self.stealth and STEALTH_AVAILABLE:
                browser_args.extend(STEALTH_BROWSER_ARGS)

            # Add HTTP/2 disable flag if needed
            if self.disable_http2:
                browser_args.append("--disable-http2")

            # Launch browser
            launch_options = {"headless": self.headless}
            if browser_args:
                launch_options["args"] = browser_args
                # Remove automation indicators
                launch_options["ignore_default_args"] = ["--enable-automation"]

            browser = await p.chromium.launch(**launch_options)

            try:
                # Prepare context options
                if self.stealth and STEALTH_AVAILABLE:
                    # Use stealth context options
                    stealth_config = get_stealth_config()
                    context_options = get_context_options(stealth_config)
                    # Override user_agent if explicitly provided
                    if self.user_agent:
                        context_options["user_agent"] = self.user_agent
                else:
                    # Standard context options
                    context_options = {
                        "user_agent": self.user_agent,
                        "viewport": {"width": 1920, "height": 1080},
                        "bypass_csp": True,
                        "ignore_https_errors": True,
                    }

                # Create context
                context = await browser.new_context(**context_options)

                try:
                    # Create page
                    page = await context.new_page()

                    # Setup resource blocking if enabled
                    blocker = None
                    if self.block_resources:
                        blocker = ResourceBlocker(
                            block_images=self.block_images,
                            block_fonts=self.block_fonts,
                            block_media=self.block_media,
                            block_ads=self.block_ads,
                            block_trackers=self.block_trackers,
                        )
                        await blocker.setup_blocking(page)

                    # Navigate to URL
                    response = await page.goto(
                        url, timeout=self.timeout, wait_until=self.wait_until
                    )

                    # Get final URL (after redirects)
                    final_url = page.url

                    # Get status code
                    status_code = response.status if response else 200

                    # Get rendered HTML
                    html = await page.content()

                    # Capture screenshot if requested
                    screenshot_path = None
                    if self.screenshot:
                        if self.screenshot is True:
                            # Create temporary file
                            temp_file = tempfile.NamedTemporaryFile(
                                suffix=".png", delete=False, prefix="mdingress_screenshot_"
                            )
                            screenshot_path = temp_file.name
                            temp_file.close()
                        else:
                            # Use provided path
                            screenshot_path = self.screenshot

                        # Take screenshot
                        await page.screenshot(path=screenshot_path, full_page=True)

                    # Get headers (convert to dict)
                    headers = dict(response.headers) if response else {}

                    elapsed_ms = (time.perf_counter() - start_time) * 1000

                    # Build metadata
                    metadata = {
                        "renderer": "playwright",
                        "stealth_mode": self.stealth,
                        "http2_disabled": self.disable_http2,
                    }

                    # Add screenshot path to metadata
                    if screenshot_path:
                        metadata["screenshot_path"] = screenshot_path

                    # Add resource blocking stats if enabled
                    if blocker:
                        stats = blocker.get_stats()
                        metadata.update(
                            {
                                "resource_blocking": True,
                                "blocked_requests": stats["blocked_requests"],
                                "total_requests": stats["total_requests"],
                                "block_rate_pct": stats["block_rate_pct"],
                                "blocked_by_type": stats["blocked_by_type"],
                            }
                        )

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
                    await context.close()

            finally:
                await browser.close()

    async def _render_with_progressive_timeout(self, url: str) -> FetchResult:
        """
        Try rendering with progressively longer timeouts.

        Strategy:
        1. Try 90s with 'networkidle'
        2. Try 180s with 'domcontentloaded'
        3. Try 300s with 'load'

        Args:
            url: Target URL to render

        Returns:
            FetchResult with rendered HTML and metadata
        """
        last_exception = None

        for strategy_index, (wait_state, timeout_ms) in enumerate(self.LOAD_STRATEGIES):
            try:
                logger.info(
                    f"[Extreme Mode] Attempt {strategy_index + 1}/3: {wait_state} ({timeout_ms/1000}s)"
                )

                # Create temporary renderer with this strategy
                temp_renderer = Renderer(
                    timeout=timeout_ms / 1000.0,
                    wait_until=wait_state,
                    headless=self.headless,
                    user_agent=self.user_agent,
                    stealth=self.stealth,
                    disable_http2=self.disable_http2,
                    extreme_mode=False,  # Disable to avoid recursion
                    block_resources=self.block_resources,
                    block_images=self.block_images,
                    block_fonts=self.block_fonts,
                    block_media=self.block_media,
                    block_ads=self.block_ads,
                    block_trackers=self.block_trackers,
                    screenshot=self.screenshot,
                )

                # Try rendering with smart waiting
                result = await temp_renderer._render_with_smart_wait(url, timeout_ms)

                # Add metadata about which strategy worked
                result.metadata["extreme_mode"] = True
                result.metadata["strategy_used"] = wait_state
                result.metadata["strategy_attempt"] = strategy_index + 1
                result.metadata["timeout_used_ms"] = timeout_ms

                logger.info(f"[Extreme Mode] Success with {wait_state} strategy")
                return result

            except Exception as e:
                last_exception = e
                error_msg = str(e)
                logger.warning(f"[Extreme Mode] {wait_state} strategy failed: {error_msg[:100]}")

                # If this isn't the last strategy, continue to next
                if strategy_index < len(self.LOAD_STRATEGIES) - 1:
                    continue

        # All strategies failed
        logger.error("[Extreme Mode] All progressive timeout strategies failed")
        raise last_exception

    async def _render_with_smart_wait(self, url: str, timeout_ms: int) -> FetchResult:
        """
        Render with smart content waiting strategies.

        Args:
            url: Target URL to render
            timeout_ms: Timeout in milliseconds

        Returns:
            FetchResult with rendered HTML and metadata
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise ImportError(
                "Playwright is not installed. Install with: "
                "pip install 'markdown-ingress[render]' or pip install playwright && playwright install"
            )

        start_time = time.perf_counter()

        async with async_playwright() as p:
            # Prepare browser arguments
            browser_args = []

            # Add stealth mode arguments if enabled
            if self.stealth and STEALTH_AVAILABLE:
                browser_args.extend(STEALTH_BROWSER_ARGS)

            # Add HTTP/2 disable flag if needed
            if self.disable_http2:
                browser_args.append("--disable-http2")

            # Launch browser
            launch_options = {"headless": self.headless}
            if browser_args:
                launch_options["args"] = browser_args
                launch_options["ignore_default_args"] = ["--enable-automation"]

            browser = await p.chromium.launch(**launch_options)

            try:
                # Prepare context options
                if self.stealth and STEALTH_AVAILABLE:
                    stealth_config = get_stealth_config()
                    context_options = get_context_options(stealth_config)
                    if self.user_agent:
                        context_options["user_agent"] = self.user_agent
                else:
                    context_options = {
                        "user_agent": self.user_agent,
                        "viewport": {"width": 1920, "height": 1080},
                        "bypass_csp": True,
                        "ignore_https_errors": True,
                    }

                context = await browser.new_context(**context_options)

                try:
                    page = await context.new_page()

                    # Setup resource blocking if enabled
                    blocker = None
                    if self.block_resources:
                        blocker = ResourceBlocker(
                            block_images=self.block_images,
                            block_fonts=self.block_fonts,
                            block_media=self.block_media,
                            block_ads=self.block_ads,
                            block_trackers=self.block_trackers,
                        )
                        await blocker.setup_blocking(page)

                    # Navigate to URL
                    response = await page.goto(url, timeout=timeout_ms, wait_until=self.wait_until)

                    # Smart content waiting
                    await self._wait_for_content(page, max_wait=min(30, timeout_ms // 1000))

                    # Get final URL and status
                    final_url = page.url
                    status_code = response.status if response else 200

                    # Get rendered HTML
                    html = await page.content()

                    # Capture screenshot if requested
                    screenshot_path = None
                    if self.screenshot:
                        if self.screenshot is True:
                            # Create temporary file
                            temp_file = tempfile.NamedTemporaryFile(
                                suffix=".png", delete=False, prefix="mdingress_screenshot_"
                            )
                            screenshot_path = temp_file.name
                            temp_file.close()
                        else:
                            # Use provided path
                            screenshot_path = self.screenshot

                        # Take screenshot
                        await page.screenshot(path=screenshot_path, full_page=True)

                    # Get headers
                    headers = dict(response.headers) if response else {}

                    elapsed_ms = (time.perf_counter() - start_time) * 1000

                    # Build metadata
                    metadata = {
                        "renderer": "playwright",
                        "stealth_mode": self.stealth,
                        "http2_disabled": self.disable_http2,
                        "smart_wait_used": True,
                    }

                    # Add screenshot path to metadata
                    if screenshot_path:
                        metadata["screenshot_path"] = screenshot_path

                    # Add resource blocking stats if enabled
                    if blocker:
                        stats = blocker.get_stats()
                        metadata.update(
                            {
                                "resource_blocking": True,
                                "blocked_requests": stats["blocked_requests"],
                                "total_requests": stats["total_requests"],
                                "block_rate_pct": stats["block_rate_pct"],
                                "blocked_by_type": stats["blocked_by_type"],
                            }
                        )

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
                    await context.close()

            finally:
                await browser.close()

    async def _wait_for_content(self, page, max_wait: int = 30):
        """
        Wait for meaningful content to appear on page.

        Checks for:
        - Body has text content
        - At least one paragraph or content element
        - No "loading" indicators

        Args:
            page: Playwright page object
            max_wait: Maximum seconds to wait
        """
        wait_start = time.time()

        # Try waiting for any content selector
        for selector in self.CONTENT_SELECTORS:
            try:
                await page.wait_for_selector(selector, timeout=min(10000, max_wait * 1000))
                logger.info(f"[Smart Wait] Found content selector: {selector}")
                break
            except Exception:
                continue

        # Wait for body to have meaningful text content
        try:
            await page.wait_for_function(
                """
                () => {
                    const body = document.body;
                    if (!body) return false;
                    
                    // Check for meaningful text content
                    const text = body.innerText || '';
                    if (text.trim().length < 50) return false;
                    
                    // Check for at least one content element
                    const hasContent = document.querySelector('p, article, main, [role="main"]');
                    if (!hasContent) return false;
                    
                    // Check for loading indicators (common patterns)
                    const loadingIndicators = document.querySelectorAll(
                        '[class*="loading"], [class*="spinner"], [id*="loading"]'
                    );
                    for (const indicator of loadingIndicators) {
                        const style = window.getComputedStyle(indicator);
                        if (style.display !== 'none' && style.visibility !== 'hidden') {
                            return false;  // Still loading
                        }
                    }
                    
                    return true;
                }
                """,
                timeout=max(5000, (max_wait - (time.time() - wait_start)) * 1000),
            )
            logger.info("[Smart Wait] Content verification passed")
        except Exception as e:
            # If content check times out, continue anyway
            logger.warning(f"[Smart Wait] Content verification timed out: {e}")
            pass

    def render_sync(self, url: str) -> FetchResult:
        """
        Synchronous wrapper for render().

        Args:
            url: Target URL to render

        Returns:
            FetchResult with rendered HTML
        """
        return asyncio.run(self.render(url))
