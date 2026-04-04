"""Main public API for MarkDownIngress."""

from collections.abc import Callable, Sequence
from typing import Literal

from markdown_ingress.adapters.rendering.playwright_renderer import PLAYWRIGHT_AVAILABLE
from markdown_ingress.adapters.extractors.comparison import compare_extractors
from markdown_ingress.api_facade import (
    UNSET,
    build_runtime_kwargs,
    generate_security_report_impl,
    ingest_async_impl,
    ingest_impl,
    ingest_many_async_impl,
    ingest_many_sync_impl,
    ingest_resolved,
    retry_ingest_impl,
)
from markdown_ingress.config_models import DomainPolicy, IngestConfig
from markdown_ingress.core.config import Config as FileConfig
from markdown_ingress.models import SafeDocument, SecurityReport
from markdown_ingress.shared_results import BatchResult

_ingest_resolved = ingest_resolved


def ingest(
    url: str,
    config: IngestConfig | FileConfig | None = None,
    # Backward compatibility: accept individual parameters
    mode: Literal["fast", "render", "auto"] | None = None,
    strict: bool | None = None,
    model: str | None = None,
    timeout: float | None = None,
    auto_render_threshold: int | None = None,
    stealth: bool | None = None,
    disable_http2: bool | None = None,
    extreme_mode: bool | None = None,
    screenshot=UNSET,
    extract_metadata: bool | None = None,
    extract_links: bool | None = None,
    advanced_security: bool | None = None,
    use_llm: bool | None = None,
    cache=UNSET,
    cache_ttl=UNSET,
    policy_name: str | None = None,
    custom_patterns: list[str] | None = None,
    plugin_dirs: list[str] | None = None,
    output_profile: str | None = None,
    extract_blocks: bool | None = None,
    chunking_strategy: Literal["none", "heading", "size"] | None = None,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    detect_language: bool | None = None,
    normalize_multilingual: bool | None = None,
    include_security_explanation: bool | None = None,
    include_observability: bool | None = None,
    render_cost_budget: int | None = None,
    domain_policies: list[dict] | list[DomainPolicy] | None = None,
) -> SafeDocument:
    """
    Ingest web content and convert to safe, sanitized Markdown.

    This is the main entry point for MarkDownIngress. It fetches content from a URL,
    extracts the main content, converts to Markdown, and analyzes for security risks.

    **NEW in v0.4.1:** Auto mode automatically detects if render mode is needed.

    Args:
        url: Target URL to ingest
        config: IngestConfig object with all settings (recommended)
        mode: Fetching mode (deprecated, use config):
            - 'fast': HTTP only (no JS execution)
            - 'render': Playwright with JS rendering
            - 'auto': Try fast first, use render if content is minimal (default)
        strict: Enable strict security mode (deprecated, use config)
        model: LLM model name for token estimation (deprecated, use config)
        timeout: Request timeout in seconds (deprecated, use config)
        auto_render_threshold: Token threshold for auto mode (deprecated, use config)
        stealth: Enable stealth mode (deprecated, use config)
        disable_http2: Disable HTTP/2 protocol (deprecated, use config)
        extreme_mode: Enable extreme timeouts (deprecated, use config)
        screenshot: Screenshot path or True for temp (deprecated, use config)
        extract_metadata: Extract enriched metadata (deprecated, use config)
        extract_links: Extract and analyze links (deprecated, use config)
        advanced_security: Enable Nova-tracer detection (deprecated, use config)
        use_llm: Enable LLM-based detection (deprecated, use config)

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
    return ingest_impl(
        url,
        playwright_available=PLAYWRIGHT_AVAILABLE,
        **build_runtime_kwargs(
            config=config,
            mode=mode,
            strict=strict,
            model=model,
            timeout=timeout,
            auto_render_threshold=auto_render_threshold,
            stealth=stealth,
            disable_http2=disable_http2,
            extreme_mode=extreme_mode,
            screenshot=screenshot,
            extract_metadata=extract_metadata,
            extract_links=extract_links,
            advanced_security=advanced_security,
            use_llm=use_llm,
            cache=cache,
            cache_ttl=cache_ttl,
            policy_name=policy_name,
            custom_patterns=custom_patterns,
            plugin_dirs=plugin_dirs,
            output_profile=output_profile,
            extract_blocks=extract_blocks,
            chunking_strategy=chunking_strategy,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            detect_language=detect_language,
            normalize_multilingual=normalize_multilingual,
            include_security_explanation=include_security_explanation,
            include_observability=include_observability,
            render_cost_budget=render_cost_budget,
            domain_policies=domain_policies,
        ),
    )


async def ingest_async(
    url: str,
    config: IngestConfig | FileConfig | None = None,
    mode: Literal["fast", "render", "auto"] | None = None,
    strict: bool | None = None,
    model: str | None = None,
    timeout: float | None = None,
    auto_render_threshold: int | None = None,
    stealth: bool | None = None,
    disable_http2: bool | None = None,
    extreme_mode: bool | None = None,
    screenshot=UNSET,
    extract_metadata: bool | None = None,
    extract_links: bool | None = None,
    advanced_security: bool | None = None,
    use_llm: bool | None = None,
    cache=UNSET,
    cache_ttl=UNSET,
    policy_name: str | None = None,
    custom_patterns: list[str] | None = None,
    plugin_dirs: list[str] | None = None,
    output_profile: str | None = None,
    extract_blocks: bool | None = None,
    chunking_strategy: Literal["none", "heading", "size"] | None = None,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    detect_language: bool | None = None,
    normalize_multilingual: bool | None = None,
    include_security_explanation: bool | None = None,
    include_observability: bool | None = None,
    render_cost_budget: int | None = None,
    domain_policies: list[dict] | list[DomainPolicy] | None = None,
) -> SafeDocument:
    """Async wrapper around ingest() for use inside asyncio applications.

    Cancelling the returned task interrupts the await, but does not guarantee
    abortion of sync ingestion work already dispatched in a background thread.
    """
    return await ingest_async_impl(
        url,
        playwright_available=PLAYWRIGHT_AVAILABLE,
        **build_runtime_kwargs(
            config=config,
            mode=mode,
            strict=strict,
            model=model,
            timeout=timeout,
            auto_render_threshold=auto_render_threshold,
            stealth=stealth,
            disable_http2=disable_http2,
            extreme_mode=extreme_mode,
            screenshot=screenshot,
            extract_metadata=extract_metadata,
            extract_links=extract_links,
            advanced_security=advanced_security,
            use_llm=use_llm,
            cache=cache,
            cache_ttl=cache_ttl,
            policy_name=policy_name,
            custom_patterns=custom_patterns,
            plugin_dirs=plugin_dirs,
            output_profile=output_profile,
            extract_blocks=extract_blocks,
            chunking_strategy=chunking_strategy,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            detect_language=detect_language,
            normalize_multilingual=normalize_multilingual,
            include_security_explanation=include_security_explanation,
            include_observability=include_observability,
            render_cost_budget=render_cost_budget,
            domain_policies=domain_policies,
        ),
    )


async def ingest_many_async(
    urls: Sequence[str],
    config: IngestConfig | FileConfig | None = None,
    mode: Literal["fast", "render", "auto"] | None = None,
    strict: bool | None = None,
    model: str | None = None,
    timeout: float | None = None,
    auto_render_threshold: int | None = None,
    stealth: bool | None = None,
    disable_http2: bool | None = None,
    extreme_mode: bool | None = None,
    screenshot=UNSET,
    extract_metadata: bool | None = None,
    extract_links: bool | None = None,
    advanced_security: bool | None = None,
    use_llm: bool | None = None,
    cache=UNSET,
    cache_ttl=UNSET,
    policy_name: str | None = None,
    custom_patterns: list[str] | None = None,
    plugin_dirs: list[str] | None = None,
    output_profile: str | None = None,
    extract_blocks: bool | None = None,
    chunking_strategy: Literal["none", "heading", "size"] | None = None,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    detect_language: bool | None = None,
    normalize_multilingual: bool | None = None,
    include_security_explanation: bool | None = None,
    include_observability: bool | None = None,
    render_cost_budget: int | None = None,
    domain_policies: list[dict] | list[DomainPolicy] | None = None,
    max_concurrent: int = 5,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> BatchResult:
    """
    Ingest multiple URLs concurrently using the same public contract as ingest().

    Returns a BatchResult that preserves the input order in `documents`.
    Failed URLs leave `None` in the matching position and populate `errors`.
    """
    return await ingest_many_async_impl(
        urls,
        playwright_available=PLAYWRIGHT_AVAILABLE,
        max_concurrent=max_concurrent,
        on_progress=on_progress,
        **build_runtime_kwargs(
            config=config,
            mode=mode,
            strict=strict,
            model=model,
            timeout=timeout,
            auto_render_threshold=auto_render_threshold,
            stealth=stealth,
            disable_http2=disable_http2,
            extreme_mode=extreme_mode,
            screenshot=screenshot,
            extract_metadata=extract_metadata,
            extract_links=extract_links,
            advanced_security=advanced_security,
            use_llm=use_llm,
            cache=cache,
            cache_ttl=cache_ttl,
            policy_name=policy_name,
            custom_patterns=custom_patterns,
            plugin_dirs=plugin_dirs,
            output_profile=output_profile,
            extract_blocks=extract_blocks,
            chunking_strategy=chunking_strategy,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            detect_language=detect_language,
            normalize_multilingual=normalize_multilingual,
            include_security_explanation=include_security_explanation,
            include_observability=include_observability,
            render_cost_budget=render_cost_budget,
            domain_policies=domain_policies,
        ),
    )


def ingest_many(
    urls: Sequence[str],
    config: IngestConfig | FileConfig | None = None,
    mode: Literal["fast", "render", "auto"] | None = None,
    strict: bool | None = None,
    model: str | None = None,
    timeout: float | None = None,
    auto_render_threshold: int | None = None,
    stealth: bool | None = None,
    disable_http2: bool | None = None,
    extreme_mode: bool | None = None,
    screenshot=UNSET,
    extract_metadata: bool | None = None,
    extract_links: bool | None = None,
    advanced_security: bool | None = None,
    use_llm: bool | None = None,
    cache=UNSET,
    cache_ttl=UNSET,
    policy_name: str | None = None,
    custom_patterns: list[str] | None = None,
    plugin_dirs: list[str] | None = None,
    output_profile: str | None = None,
    extract_blocks: bool | None = None,
    chunking_strategy: Literal["none", "heading", "size"] | None = None,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    detect_language: bool | None = None,
    normalize_multilingual: bool | None = None,
    include_security_explanation: bool | None = None,
    include_observability: bool | None = None,
    render_cost_budget: int | None = None,
    domain_policies: list[dict] | list[DomainPolicy] | None = None,
    max_concurrent: int = 5,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> BatchResult:
    """Synchronous wrapper for concurrent batch ingestion from normal Python code."""
    return ingest_many_sync_impl(
        urls,
        playwright_available=PLAYWRIGHT_AVAILABLE,
        max_concurrent=max_concurrent,
        on_progress=on_progress,
        **build_runtime_kwargs(
            config=config,
            mode=mode,
            strict=strict,
            model=model,
            timeout=timeout,
            auto_render_threshold=auto_render_threshold,
            stealth=stealth,
            disable_http2=disable_http2,
            extreme_mode=extreme_mode,
            screenshot=screenshot,
            extract_metadata=extract_metadata,
            extract_links=extract_links,
            advanced_security=advanced_security,
            use_llm=use_llm,
            cache=cache,
            cache_ttl=cache_ttl,
            policy_name=policy_name,
            custom_patterns=custom_patterns,
            plugin_dirs=plugin_dirs,
            output_profile=output_profile,
            extract_blocks=extract_blocks,
            chunking_strategy=chunking_strategy,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            detect_language=detect_language,
            normalize_multilingual=normalize_multilingual,
            include_security_explanation=include_security_explanation,
            include_observability=include_observability,
            render_cost_budget=render_cost_budget,
            domain_policies=domain_policies,
        ),
    )


def _ingest_with_config(url: str, config: IngestConfig) -> SafeDocument:
    """Internal function to perform ingestion with a config object."""
    return ingest_resolved(url, config, playwright_available=PLAYWRIGHT_AVAILABLE)


def retry_ingest(
    url: str,
    mode: Literal["fast", "render", "auto"] = "auto",
    strict: bool = True,
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
        url=url,
        mode=mode,
        strict=strict,
        model=model,
        max_retries=max_retries,
        enable_stealth=enable_stealth,
        initial_timeout=initial_timeout,
        max_timeout=max_timeout,
    )


def generate_security_report(
    url: str,
    config: IngestConfig | FileConfig | None = None,
    mode: Literal["fast", "render", "auto"] | None = None,
    strict: bool | None = None,
    model: str | None = None,
    timeout: float | None = None,
    auto_render_threshold: int | None = None,
    stealth: bool | None = None,
    disable_http2: bool | None = None,
    extreme_mode: bool | None = None,
    screenshot=UNSET,
    extract_metadata: bool | None = None,
    extract_links: bool | None = None,
    advanced_security: bool | None = None,
    use_llm: bool | None = None,
    cache=UNSET,
    cache_ttl=UNSET,
    policy_name: str | None = None,
    custom_patterns: list[str] | None = None,
    plugin_dirs: list[str] | None = None,
    output_profile: str | None = None,
    extract_blocks: bool | None = None,
    chunking_strategy: Literal["none", "heading", "size"] | None = None,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    detect_language: bool | None = None,
    normalize_multilingual: bool | None = None,
    include_security_explanation: bool | None = None,
    include_observability: bool | None = None,
    render_cost_budget: int | None = None,
    domain_policies: list[dict] | list[DomainPolicy] | None = None,
) -> SecurityReport:
    """
    Generate comprehensive security report for a URL.

    Similar to ingest() but returns detailed SecurityReport instead of SafeDocument.
    Useful for security auditing and analysis workflows.

    Args:
        url: Target URL to analyze
        mode: Fetching mode - 'fast' or 'render'
        strict: Enable strict security mode
        model: LLM model name for token estimation
        timeout: Request timeout in seconds

    Returns:
        SecurityReport with detailed security analysis
    """
    return generate_security_report_impl(
        url=url,
        **build_runtime_kwargs(
            config=config,
            mode=mode,
            strict=strict,
            model=model,
            timeout=timeout,
            auto_render_threshold=auto_render_threshold,
            stealth=stealth,
            disable_http2=disable_http2,
            extreme_mode=extreme_mode,
            screenshot=screenshot,
            extract_metadata=extract_metadata,
            extract_links=extract_links,
            advanced_security=advanced_security,
            use_llm=use_llm,
            cache=cache,
            cache_ttl=cache_ttl,
            policy_name=policy_name,
            custom_patterns=custom_patterns,
            plugin_dirs=plugin_dirs,
            output_profile=output_profile,
            extract_blocks=extract_blocks,
            chunking_strategy=chunking_strategy,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            detect_language=detect_language,
            normalize_multilingual=normalize_multilingual,
            include_security_explanation=include_security_explanation,
            include_observability=include_observability,
            render_cost_budget=render_cost_budget,
            domain_policies=domain_policies,
        ),
    )
