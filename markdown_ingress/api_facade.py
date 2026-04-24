"""Shared implementation helpers behind the public API facade."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from typing import Literal

from markdown_ingress.adapters.rendering.playwright_renderer import (
    PLAYWRIGHT_INSTALLED as PLAYWRIGHT_AVAILABLE,
)
from markdown_ingress.api_runtime import UNSET, build_runtime_config
from markdown_ingress.application.use_cases import (
    BatchIngestUseCase,
    GenerateSecurityReportUseCase,
    IngestUseCase,
)
from markdown_ingress.config_models import IngestConfig
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

_RETRY_TIMEOUT_INCREMENT_S: float = 30.0


def _maybe_persist(doc: SafeDocument | None, config: IngestConfig, url: str) -> None:
    """Persist a security report to disk if save_reports is enabled."""
    if doc is None or not config.save_reports:
        return
    try:
        _persist_report_for_document(doc, config.reports_dir)
    except Exception as exc:
        logger.warning("Failed to persist security report for %s: %s", url, exc)


_RETRYABLE_HTTP_STATUSES: frozenset[int] = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


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


def ingest_impl(
    url: str, *, playwright_available: bool = PLAYWRIGHT_AVAILABLE, **runtime_kwargs
) -> SafeDocument:
    """Shared implementation for the synchronous ingest facade."""
    runtime_config = build_runtime_config(**runtime_kwargs)
    try:
        doc = ingest_resolved(url, runtime_config, playwright_available=playwright_available)
    except PolicyBlockedError as exc:
        _maybe_persist(exc.document, runtime_config, url)
        raise
    _maybe_persist(doc, runtime_config, url)
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
        await asyncio.to_thread(_maybe_persist, exc.document, runtime_config, url)
        raise
    await asyncio.to_thread(_maybe_persist, doc, runtime_config, url)
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
                except Exception as exc:
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


def _compute_retry_attempt_params(
    attempt: int,
    max_retries: int,
    initial_timeout: float,
    max_timeout: float | None,
    enable_stealth: bool,
) -> tuple[float, bool, bool]:
    """Return (timeout, use_stealth, use_extreme) for a given retry attempt."""
    timeout = initial_timeout + attempt * _RETRY_TIMEOUT_INCREMENT_S
    if max_timeout is not None:
        timeout = min(timeout, max_timeout)
    return timeout, enable_stealth and attempt >= 1, attempt == max_retries - 1


def _is_retryable_error(exc: Exception) -> bool:
    """Return True if the exception warrants another retry attempt."""
    import httpx

    if isinstance(exc, PolicyBlockedError):
        return False
    if isinstance(exc, (TimeoutError, httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_HTTP_STATUSES
    retryable_names = ("ConnectTimeout", "ReadTimeout", "ConnectionError", "TargetClosedError")
    return any(type(exc).__name__.startswith(name) for name in retryable_names)


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

            timeout, use_stealth, use_extreme = _compute_retry_attempt_params(
                attempt, max_retries, initial_timeout, max_timeout, enable_stealth
            )
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
            is_retryable = _is_retryable_error(exc)
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
        try:
            _persist_security_report(report, config.reports_dir)
        except Exception as exc:
            logger.warning("Failed to persist security report for %s: %s", url, exc)
    return report
