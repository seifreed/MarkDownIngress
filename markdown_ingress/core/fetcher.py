"""
HTTP fetcher module - Fast mode implementation
"""

import asyncio
import random
import time

import httpx

from markdown_ingress.core.stealth.browser_config import ADVANCED_USER_AGENTS
from markdown_ingress.models import FetchResult

# Safe headers that don't require TLS fingerprint matching.
# NOTE: Accept-Encoding is intentionally omitted — let httpx manage it based
# on installed decompression libraries to avoid receiving unsupported encodings.
_SAFE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "max-age=0",
    "DNT": "1",
    "Upgrade-Insecure-Requests": "1",
}

# HTTP status codes that warrant a retry with a different User-Agent
_RETRYABLE_STATUS = {403, 429, 503}
_MAX_RETRIES = 2


def _decode_response(response: httpx.Response) -> str:
    """
    Decode response bytes to string with robust encoding fallback.
    Uses charset from Content-Type header, falls back to latin-1 (lossless).
    """
    encoding = response.charset_encoding or "utf-8"
    try:
        return response.content.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        # latin-1 can decode any byte sequence without errors
        return response.content.decode("latin-1")


class Fetcher:  # implements IFetcher protocol
    """HTTP fetcher for fast mode (no JS rendering)"""

    DEFAULT_TIMEOUT = 30.0

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT,
        user_agent: str | None = None,
        follow_redirects: bool = True,
        max_redirects: int = 10,
        rotate_ua: bool = True,
    ):
        self.timeout = timeout
        # If a specific UA is given, use it; otherwise rotate through ADVANCED_USER_AGENTS
        self._fixed_ua = user_agent
        self.rotate_ua = rotate_ua and user_agent is None
        self.follow_redirects = follow_redirects
        self.max_redirects = max_redirects

    @property
    def user_agent(self) -> str:
        """Return fixed UA or a random legitimate browser UA."""
        if self._fixed_ua:
            return self._fixed_ua
        return random.choice(ADVANCED_USER_AGENTS)

    def _build_headers(self, ua: str) -> dict:
        """Build safe browser-like headers for a given User-Agent."""
        headers = dict(_SAFE_HEADERS)
        headers["User-Agent"] = ua
        return headers

    async def fetch(self, url: str) -> FetchResult:
        """
        Fetch HTML content from URL using httpx (async).
        Retries up to _MAX_RETRIES times with a different User-Agent on 403/429/503.
        Falls back to verify=False on SSL errors.

        Args:
            url: Target URL to fetch

        Returns:
            FetchResult with HTML content and metadata

        Raises:
            httpx.HTTPError: On network/HTTP errors after all retries
        """
        last_exc: Exception | None = None
        verify: bool | str = True

        for attempt in range(_MAX_RETRIES):
            ua = self.user_agent
            start_time = time.perf_counter()

            try:
                async with httpx.AsyncClient(
                    timeout=self.timeout,
                    follow_redirects=self.follow_redirects,
                    max_redirects=self.max_redirects,
                    headers=self._build_headers(ua),
                    verify=verify,
                ) as client:
                    response = await client.get(url)

                    if response.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES - 1:
                        # Brief pause to avoid hammering — doesn't block concurrency significantly
                        await asyncio.sleep(0.5)
                        continue

                    response.raise_for_status()
                    elapsed_ms = (time.perf_counter() - start_time) * 1000

                    return FetchResult(
                        html=_decode_response(response),
                        url=url,
                        status_code=response.status_code,
                        final_url=str(response.url),
                        headers=dict(response.headers),
                        timing_ms=elapsed_ms,
                        metadata={"fetcher": "httpx", "user_agent": ua, "attempt": attempt + 1},
                    )

            except httpx.HTTPStatusError as exc:
                last_exc = exc
            except Exception as exc:
                last_exc = exc
                # On SSL errors, retry with certificate verification disabled
                if "SSL" in type(exc).__name__ or "certificate" in str(exc).lower():
                    verify = False  # pragma: no cover

        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"Fetch failed for {url} after {_MAX_RETRIES} attempts")  # pragma: no cover

    def fetch_sync(self, url: str) -> FetchResult:
        """
        Synchronous fetch wrapper with UA rotation and retry.

        Args:
            url: Target URL to fetch

        Returns:
            FetchResult with HTML content and metadata
        """
        last_exc: Exception | None = None
        verify: bool | str = True

        for attempt in range(_MAX_RETRIES):
            ua = self.user_agent
            start_time = time.perf_counter()

            try:
                with httpx.Client(
                    timeout=self.timeout,
                    follow_redirects=self.follow_redirects,
                    max_redirects=self.max_redirects,
                    headers=self._build_headers(ua),
                    verify=verify,
                ) as client:
                    response = client.get(url)

                    if response.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES - 1:
                        time.sleep(0.5)
                        continue

                    response.raise_for_status()
                    elapsed_ms = (time.perf_counter() - start_time) * 1000

                    return FetchResult(
                        html=_decode_response(response),
                        url=url,
                        status_code=response.status_code,
                        final_url=str(response.url),
                        headers=dict(response.headers),
                        timing_ms=elapsed_ms,
                        metadata={"fetcher": "httpx", "user_agent": ua, "attempt": attempt + 1},
                    )

            except httpx.HTTPStatusError as exc:
                last_exc = exc
            except Exception as exc:
                last_exc = exc
                if "SSL" in type(exc).__name__ or "certificate" in str(exc).lower():
                    verify = False  # pragma: no cover

        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"Fetch failed for {url} after {_MAX_RETRIES} attempts")  # pragma: no cover
