"""
Playwright-based renderer for SPA/JavaScript-heavy sites
"""

import asyncio
from typing import Optional
from markdown_ingress.models import FetchResult
import time


class Renderer:
    """Headless browser renderer using Playwright for JavaScript-heavy sites"""
    
    DEFAULT_TIMEOUT = 30000  # milliseconds
    DEFAULT_WAIT_UNTIL = "networkidle"  # or "load", "domcontentloaded"
    
    def __init__(
        self,
        timeout: float = 30.0,
        wait_until: str = "networkidle",
        headless: bool = True,
        user_agent: Optional[str] = None
    ):
        """
        Initialize Playwright renderer.
        
        Args:
            timeout: Navigation timeout in seconds
            wait_until: When to consider navigation complete ('networkidle', 'load', 'domcontentloaded')
            headless: Run browser in headless mode
            user_agent: Custom user agent (optional)
        """
        self.timeout = int(timeout * 1000)  # Convert to milliseconds
        self.wait_until = wait_until
        self.headless = headless
        self.user_agent = user_agent or "Mozilla/5.0 (compatible; MarkDownIngress/0.2; +https://github.com/markdowningress)"
    
    async def render(self, url: str) -> FetchResult:
        """
        Render URL using Playwright and return HTML after JavaScript execution.
        
        Args:
            url: Target URL to render
            
        Returns:
            FetchResult with rendered HTML and metadata
            
        Raises:
            ImportError: If playwright is not installed
            playwright._impl._errors.TimeoutError: On timeout
            playwright._impl._errors.Error: On navigation errors
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
            # Launch browser
            browser = await p.chromium.launch(headless=self.headless)
            
            # Create context with custom user agent
            context = await browser.new_context(
                user_agent=self.user_agent,
                viewport={'width': 1920, 'height': 1080}
            )
            
            # Create page
            page = await context.new_page()
            
            try:
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
                
                return FetchResult(
                    html=html,
                    url=url,
                    status_code=status_code,
                    final_url=final_url,
                    headers=headers,
                    timing_ms=elapsed_ms
                )
            
            finally:
                await context.close()
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
