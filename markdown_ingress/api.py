"""Main public API for MarkDownIngress."""

from collections.abc import Callable, Sequence

from markdown_ingress.adapters.rendering.playwright_renderer import (
    PLAYWRIGHT_INSTALLED as PLAYWRIGHT_AVAILABLE,
)
from markdown_ingress.api_facade import (
    UNSET,
    RetryIngestRequest,
    generate_security_report_impl,
    ingest_async_impl,
    ingest_impl,
    ingest_many_async_impl,
    ingest_many_sync_impl,
    ingest_resolved,
    retry_ingest_impl,
)
from markdown_ingress.api_runtime import resolve_batch_api_options
from markdown_ingress.config_models import IngestConfig
from markdown_ingress.core.config import Config as FileConfig
from markdown_ingress.models import SafeDocument, SecurityReport
from markdown_ingress.shared_results import BatchResult

_ingest_resolved = ingest_resolved


def ingest(
    url: str,
    config: IngestConfig | FileConfig | None = None,
    **kwargs,
) -> SafeDocument:
    """
    Ingest web content and convert to safe, sanitized Markdown.

    This is the main entry point for MarkDownIngress. It fetches content from a URL,
    extracts the main content, converts to Markdown, and analyzes for security risks.

    **NEW in v0.4.1:** Auto mode automatically detects if render mode is needed.

    Args:
        url: Target URL to ingest
        config: IngestConfig object with all settings (recommended)
        **kwargs: Deprecated individual parameters — pass an IngestConfig instead:
            mode, strict, allow_local_urls, model, timeout, auto_render_threshold,
            stealth, disable_http2, extreme_mode, screenshot, extract_metadata,
            extract_links, advanced_security, use_llm, cache, cache_ttl,
            policy_name, custom_patterns, plugin_dirs, output_format,
            output_profile, output_formats, extract_blocks, chunking_strategy,
            chunk_size, chunk_overlap, detect_language, normalize_multilingual,
            include_security_explanation, include_observability, save_reports,
            reports_dir, fetcher_user_agent, domain_request_interval,
            circuit_breaker_threshold, circuit_breaker_open_seconds,
            render_cost_budget, domain_policies.

    Returns:
        SafeDocument with markdown content, metadata, and security analysis

    Raises:
        ImportError: If mode is 'render' and Playwright is not installed
        httpx.HTTPError: On network/HTTP errors

    Examples:
        >>> # Auto mode (recommended) - automatically detects SPAs
        >>> doc = ingest("https://youtube.com", mode="auto")

        >>> # Force fast mode (HTTP only)
        >>> doc = ingest("https://example.com", mode="fast")

        >>> # Force render mode (with JS)
        >>> doc = ingest("https://spa-app.com", mode="render")

        >>> # Stealth mode to avoid bot detection
        >>> doc = ingest("https://protected-site.com", mode="render", stealth=True)

        >>> # Extreme mode for very slow sites
        >>> doc = ingest("https://slow-site.com", mode="render", extreme_mode=True)

        >>> # Using config object (new, recommended way)
        >>> config = IngestConfig(mode="auto", stealth=True, timeout=60.0)
        >>> doc = ingest("https://example.com", config=config)
    """
    return ingest_impl(url, playwright_available=PLAYWRIGHT_AVAILABLE, config=config, **kwargs)


async def ingest_async(
    url: str,
    config: IngestConfig | FileConfig | None = None,
    **kwargs,
) -> SafeDocument:
    """Async wrapper around ingest() for use inside asyncio applications.

    Accepts the same arguments as ingest(). Cancelling the returned task
    interrupts the await, but does not guarantee abortion of sync ingestion
    work already dispatched in a background thread.
    """
    return await ingest_async_impl(
        url, playwright_available=PLAYWRIGHT_AVAILABLE, config=config, **kwargs
    )


async def ingest_many_async(
    urls: Sequence[str],
    config: IngestConfig | FileConfig | None = None,
    *,
    max_concurrent=UNSET,
    on_progress: Callable[[int, int, str], None] | None = None,
    **kwargs,
) -> BatchResult:
    """
    Ingest multiple URLs concurrently using the same public contract as ingest().

    Returns a BatchResult that preserves the input order in `documents`.
    Failed URLs leave `None` in the matching position and populate `errors`.
    """
    timeout = kwargs.pop("timeout", UNSET)
    resolved_timeout, resolved_max_concurrent = resolve_batch_api_options(
        config, timeout=timeout, max_concurrent=max_concurrent
    )
    return await ingest_many_async_impl(
        urls,
        playwright_available=PLAYWRIGHT_AVAILABLE,
        max_concurrent=resolved_max_concurrent,
        on_progress=on_progress,
        config=config,
        timeout=resolved_timeout,
        **kwargs,
    )


def ingest_many(
    urls: Sequence[str],
    config: IngestConfig | FileConfig | None = None,
    *,
    max_concurrent=UNSET,
    on_progress: Callable[[int, int, str], None] | None = None,
    **kwargs,
) -> BatchResult:
    """Synchronous wrapper for concurrent batch ingestion from normal Python code."""
    timeout = kwargs.pop("timeout", UNSET)
    resolved_timeout, resolved_max_concurrent = resolve_batch_api_options(
        config, timeout=timeout, max_concurrent=max_concurrent
    )
    return ingest_many_sync_impl(
        urls,
        playwright_available=PLAYWRIGHT_AVAILABLE,
        max_concurrent=resolved_max_concurrent,
        on_progress=on_progress,
        config=config,
        timeout=resolved_timeout,
        **kwargs,
    )


def _ingest_with_config(url: str, config: IngestConfig) -> SafeDocument:
    """Internal function to perform ingestion with a config object."""
    return ingest_resolved(url, config, playwright_available=PLAYWRIGHT_AVAILABLE)


def retry_ingest(
    url: str,
    mode="auto",
    strict: bool = True,
    allow_local_urls=UNSET,
    model: str = "gpt-4",
    max_retries: int = 3,
    enable_stealth: bool = True,
    initial_timeout: float = 60.0,
    max_timeout: float | None = None,
) -> SafeDocument:
    """
    Ingest with automatic retry logic and timeout escalation.

    This function wraps ingest() with exponential backoff retry logic. It automatically
    increases timeout on each retry attempt and optionally enables stealth mode to
    bypass bot detection on retry attempts.

    Args:
        url: Target URL to ingest
        mode: Ingestion mode ('fast', 'render', or 'auto')
        strict: Enable strict security mode (blocks suspicious content)
        model: LLM model name for token estimation (default: 'gpt-4')
        max_retries: Maximum retry attempts (default: 3)
        enable_stealth: Enable stealth mode on retries (default: True)
        initial_timeout: Initial timeout in seconds (default: 60.0)
        max_timeout: Optional ceiling applied to escalated retry timeouts

    Returns:
        SafeDocument with markdown content, metadata, and security analysis
        Additional metadata fields added:
            - retry_attempts: Number of attempts made (1 = success on first try)
            - retry_enabled: Whether stealth was enabled
            - final_timeout: Final timeout value used

    Raises:
        Exception: If all retries fail, raises the last exception encountered

    Examples:
        >>> # Basic retry with defaults (3 attempts, stealth enabled)
        >>> doc = retry_ingest("https://example.com")

        >>> # Custom retry configuration
        >>> doc = retry_ingest(
        ...     "https://spa-app.com",
        ...     mode="render",
        ...     max_retries=5,
        ...     initial_timeout=90.0
        ... )

        >>> # Check retry metadata
        >>> print(f"Attempts: {doc.metadata['retry_attempts']}")
        >>> print(f"Final timeout: {doc.metadata['final_timeout']}s")
    """
    return retry_ingest_impl(
        RetryIngestRequest(
            url=url,
            mode=mode,
            strict=strict,
            allow_local_urls=allow_local_urls,
            model=model,
            max_retries=max_retries,
            enable_stealth=enable_stealth,
            initial_timeout=initial_timeout,
            max_timeout=max_timeout,
        )
    )


def generate_security_report(
    url: str,
    config: IngestConfig | FileConfig | None = None,
    **kwargs,
) -> SecurityReport:
    """
    Generate comprehensive security report for a URL.

    Similar to ingest() but returns detailed SecurityReport instead of SafeDocument.
    Accepts the same arguments as ingest().

    Args:
        url: Target URL to analyze
        config: IngestConfig object with all settings (recommended)
        **kwargs: Deprecated individual parameters — same as ingest()

    Returns:
        SecurityReport with detailed security analysis
    """
    return generate_security_report_impl(url, config=config, **kwargs)
