"""Main public API for MarkDownIngress."""

from collections.abc import Callable, Sequence
from typing import Any, TypedDict, Unpack, cast

from markdown_ingress.api_facade import (
    UNSET,
    RetryIngestRequest,
    generate_security_report_impl,
    ingest_async_impl,
    ingest_impl,
    ingest_many_async_impl,
    ingest_many_sync_impl,
    retry_ingest_impl,
)
from markdown_ingress.api_runtime import resolve_batch_api_options
from markdown_ingress.config_models import IngestConfig
from markdown_ingress.config_validation import Mode, collect_option_values
from markdown_ingress.core.config import Config as FileConfig
from markdown_ingress.models import SafeDocument, SecurityReport
from markdown_ingress.runtime_helpers import is_dependency_available
from markdown_ingress.shared_results import BatchResult

PLAYWRIGHT_AVAILABLE = is_dependency_available("playwright")


class RetryIngestOptions(TypedDict, total=False):
    mode: Mode
    strict: bool
    allow_local_urls: object
    model: str
    max_retries: int
    enable_stealth: bool
    initial_timeout: float
    max_timeout: float | None


_RETRY_INGEST_OPTION_NAMES = (
    "mode",
    "strict",
    "allow_local_urls",
    "model",
    "max_retries",
    "enable_stealth",
    "initial_timeout",
    "max_timeout",
)


def _normalize_retry_ingest_options(
    args: tuple[object, ...],
    options: RetryIngestOptions,
) -> RetryIngestOptions:
    return cast(
        RetryIngestOptions,
        collect_option_values(
            "retry_ingest()",
            _RETRY_INGEST_OPTION_NAMES,
            args,
            options,
            positional_offset=1,
        ),
    )


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


# Public compatibility API; internals collapse these into RetryIngestRequest.
def retry_ingest(
    url: str,
    *args: object,
    **options: Unpack[RetryIngestOptions],
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
    parsed = _normalize_retry_ingest_options(args, options)
    return retry_ingest_impl(
        RetryIngestRequest(
            url=url,
            mode=parsed.get("mode", "auto"),
            strict=parsed.get("strict", True),
            allow_local_urls=parsed.get("allow_local_urls", UNSET),
            model=parsed.get("model", "gpt-4"),
            max_retries=parsed.get("max_retries", 3),
            enable_stealth=parsed.get("enable_stealth", True),
            initial_timeout=parsed.get("initial_timeout", 60.0),
            max_timeout=parsed.get("max_timeout"),
        ),
        ingest_fn=ingest,
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


def compare_extractors(html: str, model: str = "gpt-4") -> dict[str, dict[str, Any]]:
    """Public API for extractor comparison benchmark inputs."""
    from markdown_ingress.application.use_cases import CompareExtractorsUseCase

    return CompareExtractorsUseCase().execute(html, model=model)
