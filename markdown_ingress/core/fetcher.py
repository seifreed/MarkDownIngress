"""
HTTP fetcher module - Fast mode implementation
"""

import time
from typing import Optional
import httpx
from markdown_ingress.models import FetchResult


class Fetcher:
    """HTTP fetcher for fast mode (no JS rendering)"""
    
    DEFAULT_TIMEOUT = 30.0
    DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; MarkDownIngress/0.1; +https://github.com/markdowningress)"
    
    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT,
        user_agent: Optional[str] = None,
        follow_redirects: bool = True,
        max_redirects: int = 10
    ):
        self.timeout = timeout
        self.user_agent = user_agent or self.DEFAULT_USER_AGENT
        self.follow_redirects = follow_redirects
        self.max_redirects = max_redirects
    
    async def fetch(self, url: str) -> FetchResult:
        """
        Fetch HTML content from URL using httpx (async).
        
        Args:
            url: Target URL to fetch
            
        Returns:
            FetchResult with HTML content and metadata
            
        Raises:
            httpx.HTTPError: On network/HTTP errors
        """
        start_time = time.perf_counter()
        
        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=self.follow_redirects,
            max_redirects=self.max_redirects,
            headers={"User-Agent": self.user_agent}
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            
            return FetchResult(
                html=response.text,
                url=url,
                status_code=response.status_code,
                final_url=str(response.url),
                headers=dict(response.headers),
                timing_ms=elapsed_ms,
                metadata={'fetcher': 'httpx'}
            )
    
    def fetch_sync(self, url: str) -> FetchResult:
        """
        Synchronous fetch wrapper.
        
        Args:
            url: Target URL to fetch
            
        Returns:
            FetchResult with HTML content and metadata
        """
        start_time = time.perf_counter()
        
        with httpx.Client(
            timeout=self.timeout,
            follow_redirects=self.follow_redirects,
            max_redirects=self.max_redirects,
            headers={"User-Agent": self.user_agent}
        ) as client:
            response = client.get(url)
            response.raise_for_status()
            
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            
            return FetchResult(
                html=response.text,
                url=url,
                status_code=response.status_code,
                final_url=str(response.url),
                headers=dict(response.headers),
                timing_ms=elapsed_ms,
                metadata={'fetcher': 'httpx'}
            )
