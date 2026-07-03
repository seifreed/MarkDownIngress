"""
Protocol interfaces for core components to improve testability and decoupling.

These interfaces define contracts for core components, making it easier to:
- Mock components in tests
- Swap implementations
- Ensure consistent APIs
- Get better IDE support
"""

from collections.abc import Callable, Sequence
from typing import Any, Protocol

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


class CompareExtractorsFn(Protocol):
    def __call__(self, html: str, *, model: str = "gpt-4") -> dict[str, dict[str, object]]: ...


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

    def notify(
        self,
        webhook_url: str,
        payload: dict[str, Any],
        *,
        validated_ip: str | None = None,
    ) -> None:
        """Deliver a webhook payload.

        Args:
            webhook_url: Target URL for webhook delivery
            payload: JSON payload to deliver
            validated_ip: Optional pre-validated IP address for DNS pinning
        """
        ...  # pragma: no cover


class IJobRecord(Protocol):
    """Protocol for job record types returned by IJobQueue."""

    job_id: str
    status: str
    created_at: str
    started_at: str | None
    completed_at: str | None
    result: dict[str, Any] | None
    error: str | None
    webhook_url: str | None
    ttl_seconds: int | None
    legacy_expires_at: str | None


class IJobQueue(Protocol):
    """Protocol for persistent or in-memory job queue implementations."""

    def submit(
        self,
        task: Callable[[], dict[str, Any]],
        webhook_url: str | None = None,
        start_immediately: bool = False,
    ) -> IJobRecord:
        """Submit a job for background execution.

        Args:
            task: Callable that returns a result dictionary.
            webhook_url: Optional URL to notify on completion.
            start_immediately: If True, start the job immediately.

        Returns:
            IJobRecord with job_id and status.
        """
        ...  # pragma: no cover

    def get(self, job_id: str) -> IJobRecord | None:
        """Retrieve a job record by id.

        Args:
            job_id: The unique job identifier.

        Returns:
            IJobRecord if found, None otherwise.
        """
        ...  # pragma: no cover

    def cleanup_expired(self) -> None:
        """Remove expired jobs."""
        ...  # pragma: no cover

    def pending_count(self) -> int:
        """Return count of queued/running jobs."""
        ...  # pragma: no cover


class IMarkdownConverter(Protocol):
    """Protocol for HTML-to-Markdown converter components."""

    def convert(self, html: str) -> str:
        """Convert cleaned HTML to Markdown string."""
        ...  # pragma: no cover


class ITokenEstimator(Protocol):
    """Protocol for LLM token estimation components."""

    def estimate(self, text: str) -> int:
        """Return estimated token count for text."""
        ...  # pragma: no cover

    def estimate_savings(self, original_html: str, markdown: str) -> dict[str, Any]:
        """Return token savings metadata between source HTML and Markdown."""
        ...  # pragma: no cover


class IBatchIngestUseCase(Protocol):
    """Protocol for batch ingestion use cases."""

    async def execute(
        self,
        urls: Sequence[str],
        config_builder: Callable[[], Any],
        *,
        max_concurrent: int = 5,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> Any: ...  # pragma: no cover


class IIngestOrchestrator(Protocol):
    """Surface of IngestOrchestrator consumed by application-layer use cases.

    Defines only the methods IngestUseCase needs, breaking the circular
    import that arose from orchestrator.py lazily importing IngestUseCase.
    """

    def make_request_key(
        self, url: str, config: Any, matched_domain_policy: Any = None
    ) -> str: ...  # pragma: no cover

    def acquire_inflight(self, request_key: str) -> Any: ...  # pragma: no cover

    def await_inflight(self, entry: Any, request_key: str) -> Any: ...  # pragma: no cover

    def release_inflight(
        self, request_key: str, *, document: Any = None, error: Any = None
    ) -> int: ...  # pragma: no cover

    @staticmethod
    def clone_cached_document(document: Any) -> Any: ...  # pragma: no cover

    def process_fetched_content(
        self,
        fetch_result: Any,
        config: Any,
        matched_domain_policy: Any = None,
        operational_flags: Any = None,
    ) -> Any: ...  # pragma: no cover

    def close(self) -> None: ...  # pragma: no cover

    def uses_default_runtime_dependencies(self) -> bool: ...  # pragma: no cover

    def has_active_cleanup_thread(self) -> bool: ...  # pragma: no cover

    def wait_for_cleanup_thread_stop(self, timeout: float = 5.0) -> bool: ...  # pragma: no cover
