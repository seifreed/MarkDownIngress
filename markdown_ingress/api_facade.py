"""Shared implementation helpers behind the public API facade."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from typing import Literal

from markdown_ingress.adapters.rendering.playwright_renderer import PLAYWRIGHT_AVAILABLE
from markdown_ingress.api_runtime import UNSET, build_runtime_config
from markdown_ingress.application.use_cases import (
    BatchIngestUseCase,
    GenerateSecurityReportUseCase,
    IngestUseCase,
)
from markdown_ingress.config_models import DomainPolicy, IngestConfig
from markdown_ingress.core.config import Config as FileConfig
from markdown_ingress.core.policy import PolicyBlockedError
from markdown_ingress.models import SafeDocument, SecurityReport
from markdown_ingress.reporting import (
    persist_report_for_document as _persist_report_for_document,
)
from markdown_ingress.reporting import (
    persist_security_report as _persist_security_report,
)
from markdown_ingress.shared_results import BatchResult

logger = logging.getLogger(__name__)


def ingest_resolved(
    url: str,
    config: IngestConfig,
    *,
    playwright_available: bool = PLAYWRIGHT_AVAILABLE,
) -> SafeDocument:
    """Execute ingestion using a fully resolved runtime config."""
    use_case = IngestUseCase(playwright_available=playwright_available)
    try:
        return use_case.execute(url, config)
    finally:
        use_case.close()


def build_runtime_kwargs(
    *,
    config: IngestConfig | FileConfig | None = None,
    mode: Literal["fast", "render", "auto"] | None = None,
    strict: bool | None = None,
    allow_local_urls=UNSET,
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
    output_format: Literal["text", "json", "markdown"] | None = None,
    output_profile: str | None = None,
    output_formats: list[str] | None = None,
    extract_blocks: bool | None = None,
    chunking_strategy: Literal["none", "heading", "size"] | None = None,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    detect_language: bool | None = None,
    normalize_multilingual: bool | None = None,
    include_security_explanation: bool | None = None,
    include_observability: bool | None = None,
    save_reports: bool | None = None,
    reports_dir: str | None = None,
    fetcher_user_agent: str | None = None,
    domain_request_interval: float | None = None,
    circuit_breaker_threshold: int | None = None,
    circuit_breaker_open_seconds: float | None = None,
    render_cost_budget=UNSET,
    domain_policies: list[dict] | list[DomainPolicy] | None = None,
) -> dict:
    """Collect the full public runtime config argument set into one dict."""
    return {
        "config": config,
        "mode": mode,
        "strict": strict,
        "allow_local_urls": allow_local_urls,
        "model": model,
        "timeout": timeout,
        "auto_render_threshold": auto_render_threshold,
        "stealth": stealth,
        "disable_http2": disable_http2,
        "extreme_mode": extreme_mode,
        "screenshot": screenshot,
        "extract_metadata": extract_metadata,
        "extract_links": extract_links,
        "advanced_security": advanced_security,
        "use_llm": use_llm,
        "cache": cache,
        "cache_ttl": cache_ttl,
        "policy_name": policy_name,
        "custom_patterns": custom_patterns,
        "plugin_dirs": plugin_dirs,
        "output_format": output_format,
        "output_profile": output_profile,
        "output_formats": output_formats,
        "extract_blocks": extract_blocks,
        "chunking_strategy": chunking_strategy,
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
        "detect_language": detect_language,
        "normalize_multilingual": normalize_multilingual,
        "include_security_explanation": include_security_explanation,
        "include_observability": include_observability,
        "save_reports": save_reports,
        "reports_dir": reports_dir,
        "fetcher_user_agent": fetcher_user_agent,
        "domain_request_interval": domain_request_interval,
        "circuit_breaker_threshold": circuit_breaker_threshold,
        "circuit_breaker_open_seconds": circuit_breaker_open_seconds,
        "render_cost_budget": render_cost_budget,
        "domain_policies": domain_policies,
    }


def ingest_impl(
    url: str, *, playwright_available: bool = PLAYWRIGHT_AVAILABLE, **runtime_kwargs
) -> SafeDocument:
    """Shared implementation for the synchronous ingest facade."""
    runtime_config = build_runtime_config(**runtime_kwargs)
    try:
        doc = ingest_resolved(url, runtime_config, playwright_available=playwright_available)
    except PolicyBlockedError as exc:
        if runtime_config.save_reports and exc.document is not None:
            try:
                _persist_report_for_document(exc.document, runtime_config.reports_dir)
            except OSError as persist_exc:
                logger.warning("Failed to persist security report for %s: %s", url, persist_exc)
        raise
    if runtime_config.save_reports:
        try:
            _persist_report_for_document(doc, runtime_config.reports_dir)
        except OSError as exc:
            logger.warning("Failed to persist security report for %s: %s", url, exc)
    return doc


async def ingest_async_impl(
    url: str,
    *,
    playwright_available: bool = PLAYWRIGHT_AVAILABLE,
    **runtime_kwargs,
) -> SafeDocument:
    """Shared implementation for the async ingest facade.

    Cancellation stops waiting on the async wrapper, but does not abort work
    already dispatched to the background thread.
    """
    runtime_config = build_runtime_config(**runtime_kwargs)
    try:
        doc = await asyncio.to_thread(
            ingest_resolved,
            url,
            runtime_config,
            playwright_available=playwright_available,
        )
    except PolicyBlockedError as exc:
        if runtime_config.save_reports and exc.document is not None:
            try:
                await asyncio.to_thread(
                    _persist_report_for_document,
                    exc.document,
                    runtime_config.reports_dir,
                )
            except OSError as persist_exc:
                logger.warning("Failed to persist security report for %s: %s", url, persist_exc)
        raise
    if runtime_config.save_reports:
        try:
            await asyncio.to_thread(
                _persist_report_for_document,
                doc,
                runtime_config.reports_dir,
            )
        except OSError as exc:
            logger.warning("Failed to persist security report for %s: %s", url, exc)
    return doc


async def ingest_many_async_impl(
    urls: Sequence[str],
    *,
    playwright_available: bool = PLAYWRIGHT_AVAILABLE,
    max_concurrent: int,
    on_progress: Callable[[int, int, str], None] | None,
    **runtime_kwargs,
) -> BatchResult:
    """Concurrent batch ingestion using the same runtime contract as ingest()."""
    if max_concurrent < 1:
        raise ValueError("max_concurrent must be >= 1")

    url_list = list(urls)
    runtime_config = build_runtime_config(**runtime_kwargs)
    use_case = IngestUseCase(playwright_available=playwright_available)
    try:
        batch_use_case = BatchIngestUseCase(ingest_use_case=use_case)
        result = await batch_use_case.execute(
            url_list,
            config_builder=runtime_config.clone,
            max_concurrent=max_concurrent,
            on_progress=on_progress,
        )
        if runtime_config.save_reports:
            for index, doc in enumerate(result.documents):
                if doc is None:
                    continue
                try:
                    await asyncio.to_thread(
                        _persist_report_for_document,
                        doc,
                        runtime_config.reports_dir,
                    )
                except OSError as exc:
                    target_url = url_list[index] if index < len(url_list) else "<unknown>"
                    logger.warning(
                        "Failed to persist security report for %s: %s",
                        target_url,
                        exc,
                    )
        return result
    finally:
        use_case.close()


def ingest_many_sync_impl(
    urls: Sequence[str],
    *,
    playwright_available: bool = PLAYWRIGHT_AVAILABLE,
    max_concurrent: int,
    on_progress: Callable[[int, int, str], None] | None,
    **runtime_kwargs,
) -> BatchResult:
    """Synchronous wrapper for concurrent batch ingestion from normal Python code."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            ingest_many_async_impl(
                urls,
                playwright_available=playwright_available,
                max_concurrent=max_concurrent,
                on_progress=on_progress,
                **runtime_kwargs,
            )
        )

    raise RuntimeError(
        "ingest_many() cannot run inside an active event loop; use ingest_many_async() instead"
    )


def retry_ingest_impl(
    url: str,
    mode: Literal["fast", "render", "auto"] = "auto",
    strict: bool = True,
    allow_local_urls=UNSET,
    model: str = "gpt-4",
    max_retries: int = 3,
    enable_stealth: bool = True,
    initial_timeout: float = 60.0,
    max_timeout: float | None = None,
) -> SafeDocument:
    """Implementation for retrying ingestion with escalating timeout and stealth."""
    import httpx

    if max_retries < 1:
        raise ValueError("max_retries must be >= 1")
    if initial_timeout <= 0:
        raise ValueError("initial_timeout must be > 0")
    if max_timeout is not None and max_timeout < initial_timeout:
        raise ValueError("max_timeout must be greater than or equal to initial_timeout")

    last_exception: Exception | None = None
    for attempt in range(max_retries):
        try:
            from markdown_ingress.api import ingest as public_ingest

            timeout = initial_timeout + (attempt * 30.0)
            if max_timeout is not None:
                timeout = min(timeout, max_timeout)
            use_stealth = enable_stealth and (attempt >= 1)
            use_extreme = attempt == max_retries - 1
            if attempt > 0:
                logger.info("Retry attempt %d/%d for %s", attempt + 1, max_retries, url)
                logger.info(
                    "Timeout: %ss, Stealth: %s, Extreme: %s", timeout, use_stealth, use_extreme
                )

            doc = public_ingest(
                url,
                mode=mode,
                strict=strict,
                allow_local_urls=allow_local_urls,
                model=model,
                timeout=timeout,
                stealth=use_stealth,
                extreme_mode=use_extreme,
            )
            doc.metadata["retry_attempts"] = attempt + 1
            doc.metadata["retry_enabled"] = use_stealth
            doc.metadata["extreme_mode_enabled"] = use_extreme
            doc.metadata["final_timeout"] = timeout
            if attempt > 0:
                logger.info("Success on attempt %d", attempt + 1)
            return doc
        except Exception as exc:
            last_exception = exc
            error_type = type(exc).__name__
            if not isinstance(exc, PolicyBlockedError) and isinstance(
                exc,
                (
                    TimeoutError,
                    httpx.TimeoutException,
                    httpx.ConnectError,
                    httpx.NetworkError,
                ),
            ):
                is_retryable = True
            elif isinstance(exc, httpx.HTTPStatusError) and not isinstance(exc, PolicyBlockedError):
                is_retryable = exc.response.status_code in {408, 409, 425, 429, 500, 502, 503, 504}
            else:
                is_retryable = False
            if not is_retryable:
                retryable_error_names = (
                    "ConnectTimeout",
                    "ReadTimeout",
                    "ConnectionError",
                    "TargetClosedError",
                )
                is_retryable = any(error_type.startswith(name) for name in retryable_error_names)
            if attempt < max_retries - 1:
                if is_retryable:
                    wait_time = 2**attempt
                    logger.warning("%s on attempt %d: %s", error_type, attempt + 1, exc)
                    logger.info("Waiting %ds before retry...", wait_time)
                    time.sleep(wait_time)
                else:
                    logger.error("Non-retryable error %s: %s", error_type, exc)
                    raise exc
            else:
                logger.error("All %d attempts failed for %s", max_retries, url)
                logger.error("Final error: %s: %s", error_type, exc)

    if last_exception is not None:
        raise last_exception
    raise RuntimeError("retry_ingest exhausted retries without capturing an exception")


def generate_security_report_impl(url: str, **runtime_kwargs) -> SecurityReport:
    """Generate a detailed security report through the dedicated use case."""
    config = build_runtime_config(**runtime_kwargs)
    use_case = IngestUseCase(playwright_available=PLAYWRIGHT_AVAILABLE)
    try:
        report = GenerateSecurityReportUseCase(
            ingest_use_case=use_case
        ).execute(url, config)
    finally:
        use_case.close()
    if config.save_reports:
        _persist_security_report(report, config.reports_dir)
    return report
