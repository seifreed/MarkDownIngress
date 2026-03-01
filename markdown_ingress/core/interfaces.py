"""
Protocol interfaces for core components to improve testability and decoupling.

These interfaces define contracts for core components, making it easier to:
- Mock components in tests
- Swap implementations
- Ensure consistent APIs
- Get better IDE support
"""

from typing import Protocol

from markdown_ingress.models import ExtractionResult, FetchResult, SafeDocument


class IFetcher(Protocol):
    """Protocol for HTTP fetcher components."""

    async def fetch(self, url: str) -> FetchResult:
        """
        Fetch HTML content from URL.

        Args:
            url: Target URL to fetch

        Returns:
            FetchResult with HTML content and metadata

        Raises:
            httpx.HTTPError: On network/HTTP errors
        """
        ...

    def fetch_sync(self, url: str) -> FetchResult:
        """
        Synchronous fetch wrapper.

        Args:
            url: Target URL to fetch

        Returns:
            FetchResult with HTML content and metadata
        """
        ...


class IRenderer(Protocol):
    """Protocol for JavaScript-capable renderer components."""

    async def render(self, url: str) -> FetchResult:
        """
        Render JavaScript-heavy pages and return HTML after JS execution.

        Args:
            url: Target URL to render

        Returns:
            FetchResult with rendered HTML and metadata

        Raises:
            ImportError: If playwright is not installed
            playwright._impl._errors.TimeoutError: On timeout
            playwright._impl._errors.Error: On navigation errors
        """
        ...

    def render_sync(self, url: str) -> FetchResult:
        """
        Synchronous wrapper for render().

        Args:
            url: Target URL to render

        Returns:
            FetchResult with rendered HTML
        """
        ...


class IExtractor(Protocol):
    """Protocol for HTML content extractor components."""

    def extract(self, html: str, url: str) -> ExtractionResult:
        """
        Extract main content from HTML and clean DOM.

        Args:
            html: Raw HTML content
            url: Source URL (for readability context)

        Returns:
            ExtractionResult with cleaned HTML and metadata
        """
        ...


class INormalizer(Protocol):
    """Protocol for content normalization components."""

    def normalize(self, text: str) -> str:
        """
        Apply all normalization steps to text.

        Args:
            text: Input text to normalize

        Returns:
            Normalized text
        """
        ...

    def normalize_unicode(self, text: str) -> str:
        """
        Normalize Unicode to NFC form for consistency.

        Args:
            text: Input text

        Returns:
            NFC-normalized text
        """
        ...

    def normalize_whitespace(self, text: str) -> str:
        """
        Normalize whitespace: collapse multiple spaces, normalize line breaks.

        Args:
            text: Input text

        Returns:
            Text with normalized whitespace
        """
        ...

    def normalize_url(self, url: str) -> str:
        """
        Normalize URL by removing tracking parameters.

        Args:
            url: Input URL

        Returns:
            Normalized URL without tracking params
        """
        ...


class ICacheBackend(Protocol):
    """Protocol for cache backend implementations."""

    def get(self, key: str) -> SafeDocument | None:
        """
        Get document from cache.

        Args:
            key: Cache key

        Returns:
            SafeDocument if found and not expired, None otherwise
        """
        ...

    def set(self, key: str, document: SafeDocument, ttl: int | None = None) -> None:
        """
        Store document in cache.

        Args:
            key: Cache key
            document: SafeDocument to store
            ttl: Time-to-live in seconds (None = use default)
        """
        ...

    def delete(self, key: str) -> None:
        """
        Delete document from cache.

        Args:
            key: Cache key
        """
        ...

    def clear(self) -> None:
        """
        Clear entire cache.
        """
        ...

    def exists(self, key: str) -> bool:
        """
        Check if key exists in cache.

        Args:
            key: Cache key

        Returns:
            True if key exists and is not expired
        """
        ...
