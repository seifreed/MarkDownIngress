"""
Protocol interfaces for core components to improve testability and decoupling.

These interfaces define contracts for core components, making it easier to:
- Mock components in tests
- Swap implementations
- Ensure consistent APIs
- Get better IDE support
"""

from typing import TYPE_CHECKING, Any, Callable, Protocol

from markdown_ingress.models import ExtractionResult, FetchResult, SafeDocument

if TYPE_CHECKING:
    from markdown_ingress.adapters.jobs.sqlite_job_queue import JobRecord


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
        ...  # pragma: no cover

    def fetch_sync(self, url: str) -> FetchResult:
        """
        Synchronous fetch wrapper.

        Args:
            url: Target URL to fetch

        Returns:
            FetchResult with HTML content and metadata
        """
        ...  # pragma: no cover


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
        ...  # pragma: no cover

    def render_sync(self, url: str) -> FetchResult:
        """
        Synchronous wrapper for render().

        Args:
            url: Target URL to render

        Returns:
            FetchResult with rendered HTML
        """
        ...  # pragma: no cover


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
        ...  # pragma: no cover


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
        ...  # pragma: no cover

    def normalize_unicode(self, text: str) -> str:
        """
        Normalize Unicode to NFC form for consistency.

        Args:
            text: Input text

        Returns:
            NFC-normalized text
        """
        ...  # pragma: no cover

    def normalize_whitespace(self, text: str) -> str:
        """
        Normalize whitespace: collapse multiple spaces, normalize line breaks.

        Args:
            text: Input text

        Returns:
            Text with normalized whitespace
        """
        ...  # pragma: no cover

    def normalize_url(self, url: str) -> str:
        """
        Normalize URL by removing tracking parameters.

        Args:
            url: Input URL

        Returns:
            Normalized URL without tracking params
        """
        ...  # pragma: no cover


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
        ...  # pragma: no cover

    def set(self, key: str, document: SafeDocument, ttl: int | None = None) -> None:
        """
        Store document in cache.

        Args:
            key: Cache key
            document: SafeDocument to store
            ttl: Time-to-live in seconds (None = use default)
        """
        ...  # pragma: no cover

    def delete(self, key: str) -> None:
        """
        Delete document from cache.

        Args:
            key: Cache key
        """
        ...  # pragma: no cover

    def clear(self) -> None:
        """
        Clear entire cache.
        """
        ...  # pragma: no cover

    def exists(self, key: str) -> bool:
        """
        Check if key exists in cache.

        Args:
            key: Cache key

        Returns:
            True if key exists and is not expired
        """
        ...  # pragma: no cover


class IWebhookNotifier(Protocol):
    """Protocol for webhook delivery adapters."""

    def notify(self, webhook_url: str, payload: dict[str, Any]) -> None:
        """Deliver a webhook payload."""
        ...  # pragma: no cover


class IJobQueue(Protocol):
    """Protocol for persistent or in-memory job queue implementations."""

    def submit(
        self,
        task: Callable[[], dict[str, Any]],
        webhook_url: str | None = None,
        start_immediately: bool = False,
    ) -> "JobRecord":
        """Submit a job for background execution.

        Args:
            task: Callable that returns a result dictionary.
            webhook_url: Optional URL to notify on completion.
            start_immediately: If True, start the job immediately.

        Returns:
            JobRecord with job_id and status.
        """
        ...  # pragma: no cover

    def get(self, job_id: str) -> "JobRecord | None":
        """Retrieve a job record by id.

        Args:
            job_id: The unique job identifier.

        Returns:
            JobRecord if found, None otherwise.
        """
        ...  # pragma: no cover

    def cleanup_expired(self) -> None:
        """Remove expired jobs."""
        ...  # pragma: no cover

    def pending_count(self) -> int:
        """Return count of queued/running jobs."""
        ...  # pragma: no cover
