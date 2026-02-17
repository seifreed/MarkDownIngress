"""
Main API for MarkDownIngress
"""

import time
from typing import Literal, Optional, Union

from markdown_ingress.config_models import IngestConfig
from markdown_ingress.core.orchestrator import IngestOrchestrator, PLAYWRIGHT_AVAILABLE
from markdown_ingress.models import SafeDocument, SecurityReport


def ingest(
    url: str,
    config: Optional[IngestConfig] = None,
    # Backward compatibility: accept individual parameters
    mode: Optional[Literal["fast", "render", "auto"]] = None,
    strict: Optional[bool] = None,
    model: Optional[str] = None,
    timeout: Optional[float] = None,
    auto_render_threshold: Optional[int] = None,
    stealth: Optional[bool] = None,
    disable_http2: Optional[bool] = None,
    extreme_mode: Optional[bool] = None,
    screenshot: Optional[Union[bool, str]] = None,
    extract_metadata: Optional[bool] = None,
    extract_links: Optional[bool] = None,
    advanced_security: Optional[bool] = None,
    use_llm: Optional[bool] = None,
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
    # If no config provided, create from individual parameters or defaults
    if config is None:
        config = IngestConfig(
            mode=mode if mode is not None else "auto",
            strict=strict if strict is not None else True,
            model=model if model is not None else "gpt-4",
            timeout=timeout if timeout is not None else 30.0,
            auto_render_threshold=auto_render_threshold if auto_render_threshold is not None else 50,
            stealth=stealth if stealth is not None else False,
            disable_http2=disable_http2 if disable_http2 is not None else False,
            extreme_mode=extreme_mode if extreme_mode is not None else False,
            screenshot=screenshot,
            extract_metadata=extract_metadata if extract_metadata is not None else True,
            extract_links=extract_links if extract_links is not None else True,
            advanced_security=advanced_security if advanced_security is not None else False,
            use_llm=use_llm if use_llm is not None else False,
        )
    else:
        # Config provided - override with any explicit parameters
        if mode is not None:
            config.mode = mode
        if strict is not None:
            config.strict = strict
        if model is not None:
            config.model = model
        if timeout is not None:
            config.timeout = timeout
        if auto_render_threshold is not None:
            config.auto_render_threshold = auto_render_threshold
        if stealth is not None:
            config.stealth = stealth
        if disable_http2 is not None:
            config.disable_http2 = disable_http2
        if extreme_mode is not None:
            config.extreme_mode = extreme_mode
        if screenshot is not None:
            config.screenshot = screenshot
        if extract_metadata is not None:
            config.extract_metadata = extract_metadata
        if extract_links is not None:
            config.extract_links = extract_links
        if advanced_security is not None:
            config.advanced_security = advanced_security
        if use_llm is not None:
            config.use_llm = use_llm
    
    # Auto mode: try fast first, fallback to render if needed
    if config.mode == "auto":
        try:
            # Try fast mode first
            doc = _ingest_with_config(url, IngestConfig(**{**config.__dict__, "mode": "fast"}))

            # Check if we got meaningful content
            if doc.token_estimate < config.auto_render_threshold:
                # Content is minimal, likely a SPA - try render mode
                if PLAYWRIGHT_AVAILABLE:
                    doc_render = _ingest_with_config(url, IngestConfig(**{**config.__dict__, "mode": "render"}))
                    # Use render result if it has more content
                    if doc_render.token_estimate > doc.token_estimate:
                        doc_render.metadata["auto_mode_used"] = "render"
                        doc_render.metadata["fast_mode_tokens"] = doc.token_estimate
                        return doc_render

            # Fast mode result is good enough
            doc.metadata["auto_mode_used"] = "fast"
            return doc

        except Exception as e:
            # If fast fails, try render as fallback
            if PLAYWRIGHT_AVAILABLE:
                doc = _ingest_with_config(url, IngestConfig(**{**config.__dict__, "mode": "render"}))
                doc.metadata["auto_mode_used"] = "render"
                doc.metadata["auto_mode_reason"] = "fast_failed"
                return doc
            else:
                raise e

    # Regular mode (fast or render explicitly specified)
    return _ingest_with_config(url, config)


def _ingest_with_config(url: str, config: IngestConfig) -> SafeDocument:
    """
    Internal function to perform ingestion with a config object.
    """
    # Create orchestrator and execute pipeline
    orchestrator = IngestOrchestrator()
    
    return orchestrator.execute(url=url, config=config)


def retry_ingest(
    url: str,
    mode: Literal["fast", "render", "auto"] = "auto",
    strict: bool = True,
    model: str = "gpt-4",
    max_retries: int = 3,
    enable_stealth: bool = True,
    initial_timeout: float = 60.0,
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
    # Validate parameters
    if max_retries < 1:
        raise ValueError("max_retries must be >= 1")
    
    last_exception: Optional[Exception] = None

    for attempt in range(max_retries):
        try:
            # Calculate escalating timeout: 60s, 90s, 120s, 150s...
            timeout = initial_timeout + (attempt * 30.0)

            # Enable stealth mode on retry attempts (attempt >= 1)
            use_stealth = enable_stealth and (attempt >= 1)

            # Enable extreme mode on last attempt for ultimate patience
            use_extreme = attempt == max_retries - 1

            # Log retry attempt
            if attempt > 0:
                print(f"[MarkDownIngress] Retry attempt {attempt + 1}/{max_retries} for {url}")
                print(
                    f"[MarkDownIngress] Timeout: {timeout}s, Stealth: {use_stealth}, Extreme: {use_extreme}"
                )

            # For stealth mode, configure renderer with stealth settings
            doc = ingest(
                url=url,
                mode=mode,
                strict=strict,
                model=model,
                timeout=timeout,
                stealth=use_stealth,
                extreme_mode=use_extreme,
            )

            # Success! Add retry metadata
            doc.metadata["retry_attempts"] = attempt + 1
            doc.metadata["retry_enabled"] = use_stealth
            doc.metadata["extreme_mode_enabled"] = use_extreme
            doc.metadata["final_timeout"] = timeout

            if attempt > 0:
                print(f"[MarkDownIngress] Success on attempt {attempt + 1}")

            return doc

        except Exception as e:
            last_exception = e
            error_type = type(e).__name__

            # Check if this is a retryable error
            retryable_errors = (
                "TimeoutError",
                "Timeout",
                "ConnectTimeout",
                "ReadTimeout",
                "HTTPError",
                "ConnectionError",
                "ConnectError",  # SSL/connection errors
                "TargetClosedError",  # Playwright error
            )

            is_retryable = any(err in error_type for err in retryable_errors)

            if attempt < max_retries - 1:
                if is_retryable:
                    wait_time = 2**attempt  # Exponential backoff: 1s, 2s, 4s
                    print(f"[MarkDownIngress] {error_type} on attempt {attempt + 1}: {str(e)}")
                    print(f"[MarkDownIngress] Waiting {wait_time}s before retry...")
                    time.sleep(wait_time)
                else:
                    # Non-retryable error, fail fast
                    print(f"[MarkDownIngress] Non-retryable error {error_type}: {str(e)}")
                    raise e
            else:
                # Last attempt failed
                print(f"[MarkDownIngress] All {max_retries} attempts failed for {url}")
                print(f"[MarkDownIngress] Final error: {error_type}: {str(e)}")

    # If we get here, all retries failed
    raise last_exception


def generate_security_report(
    url: str,
    mode: Literal["fast", "render", "auto"] = "auto",
    strict: bool = True,
    model: str = "gpt-4",
    timeout: float = 30.0,
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
    # Get safe document first
    doc = ingest(url=url, mode=mode, strict=strict, model=model, timeout=timeout)

    # Calculate additional metrics
    original_size = len(doc.metadata.get("url", "").encode("utf-8"))
    cleaned_size = len(doc.markdown.encode("utf-8"))

    # Build comprehensive report
    report = SecurityReport(
        injection_score=doc.injection_score,
        risk_level=doc.metadata.get("risk_level", "UNKNOWN"),
        flags=doc.flags,
        hidden_content_detected="hidden_content" in doc.flags,
        hidden_elements_count=doc.removed_elements.get("hidden_elements", 0),
        url=doc.metadata.get("url", ""),
        title=doc.metadata.get("title", ""),
        token_estimate=doc.token_estimate,
        token_reduction_percent=doc.metadata.get("token_savings", {}).get("percentage_saved", 0.0),
        original_size_bytes=original_size,
        cleaned_size_bytes=cleaned_size,
        content_hash=doc.content_hash,
        structural_hash=doc.metadata.get("structural_hash", ""),
        removed_elements=doc.removed_elements,
    )

    return report
