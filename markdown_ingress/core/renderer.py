"""
Playwright-based renderer for SPA/JavaScript-heavy sites
"""

import asyncio
from typing import Optional
from markdown_ingress.models import FetchResult
import time

try:
    from markdown_ingress.core.stealth import (
        get_stealth_config,
        get_context_options,
        STEALTH_BROWSER_ARGS
    )
    STEALTH_AVAILABLE = True
except ImportError:
    STEALTH_AVAILABLE = False


class Renderer:
    """Headless browser renderer using Playwright for JavaScript-heavy sites"""
    
    DEFAULT_TIMEOUT = 30000  # milliseconds
    DEFAULT_WAIT_UNTIL = "networkidle"  # or "load", "domcontentloaded"
    
    def __init__(
        self,
        timeout: float = 30.0,
        wait_until: str = "networkidle",
        headless: bool = True,
        user_agent: Optional[str] = None,
        stealth: bool = False,
        disable_http2: bool = False
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
        """
        self.timeout = int(timeout * 1000)  # Convert to milliseconds
        self.wait_until = wait_until
        self.headless = headless
        self.stealth = stealth
        self.disable_http2 = disable_http2
        self.user_agent = user_agent or "Mozilla/5.0 (compatible; MarkDownIngress/0.2; +https://github.com/markdowningress)"
    
    async def render(self, url: str) -> FetchResult:
        """
        Render URL using Playwright and return HTML after JavaScript execution.
        Includes automatic HTTP/2 fallback on protocol errors.
        
        Args:
            url: Target URL to render
            
        Returns:
            FetchResult with rendered HTML and metadata
            
        Raises:
            ImportError: If playwright is not installed
            playwright._impl._errors.TimeoutError: On timeout
            playwright._impl._errors.Error: On navigation errors (except HTTP/2 errors which trigger fallback)
        """
        try:
            result = await self._render_with_browser(url)
            return result
        except Exception as e:
            error_str = str(e)
            # Check for HTTP/2 protocol error
            if 'ERR_HTTP2_PROTOCOL_ERROR' in error_str and not self.disable_http2:
                # Retry with HTTP/2 disabled
                retry_renderer = Renderer(
                    timeout=self.timeout / 1000.0,  # Convert back to seconds
                    wait_until=self.wait_until,
                    headless=self.headless,
                    user_agent=self.user_agent,
                    stealth=self.stealth,
                    disable_http2=True
                )
                result = await retry_renderer._render_with_browser(url)
                # Mark as HTTP/2 fallback
                result.metadata['http2_fallback'] = True
                result.metadata['original_error'] = 'ERR_HTTP2_PROTOCOL_ERROR'
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
                browser_args.append('--disable-http2')
            
            # Launch browser
            launch_options = {'headless': self.headless}
            if browser_args:
                launch_options['args'] = browser_args
                # Remove automation indicators
                launch_options['ignore_default_args'] = ['--enable-automation']
            
            browser = await p.chromium.launch(**launch_options)
            
            try:
                # Prepare context options
                if self.stealth and STEALTH_AVAILABLE:
                    # Use stealth context options
                    stealth_config = get_stealth_config()
                    context_options = get_context_options(stealth_config)
                    # Override user_agent if explicitly provided
                    if self.user_agent:
                        context_options['user_agent'] = self.user_agent
                else:
                    # Standard context options
                    context_options = {
                        'user_agent': self.user_agent,
                        'viewport': {'width': 1920, 'height': 1080},
                        'bypass_csp': True,
                        'ignore_https_errors': True,
                    }
                
                # Create context
                context = await browser.new_context(**context_options)
                
                try:
                    # Create page
                    page = await context.new_page()
                    
                    # Navigate to URL
                    response = await page.goto(
                        url,
                        timeout=self.timeout,
                        wait_until=self.wait_until
                    )
                    
                    # Get final URL (after redirects)
                    final_url = page.url
                    
                    # Get status code
                    status_code = response.status if response else 200
                    
                    # Get rendered HTML
                    html = await page.content()
                    
                    # Get headers (convert to dict)
                    headers = dict(response.headers) if response else {}
                    
                    elapsed_ms = (time.perf_counter() - start_time) * 1000
                    
                    # Build metadata
                    metadata = {
                        'renderer': 'playwright',
                        'stealth_mode': self.stealth,
                        'http2_disabled': self.disable_http2,
                    }
                    
                    return FetchResult(
                        html=html,
                        url=url,
                        status_code=status_code,
                        final_url=final_url,
                        headers=headers,
                        timing_ms=elapsed_ms,
                        metadata=metadata
                    )
                
                finally:
                    await context.close()
            
            finally:
                await browser.close()
    
    def render_sync(self, url: str) -> FetchResult:
        """
        Synchronous wrapper for render().
        
        Args:
            url: Target URL to render
            
        Returns:
            FetchResult with rendered HTML
        """
        return asyncio.run(self.render(url))
