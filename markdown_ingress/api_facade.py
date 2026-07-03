"""Shared implementation helpers behind the public API facade."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib.util import find_spec
from typing import Any, cast

from markdown_ingress.api_runtime import (
    UNSET,
    _validate_batch_max_concurrent,
    build_runtime_config,
    run_ingest_many_blocking,
)
from markdown_ingress.config_models import IngestConfig
from markdown_ingress.config_validation import Mode, validate_positive_int
from markdown_ingress.core.policy import PolicyBlockedError
from markdown_ingress.core.runtime_error_policy import is_retryable_runtime_exception
from markdown_ingress.models import SafeDocument, SecurityReport
from markdown_ingress.reporting import (
    persist_report_for_document as _persist_report_for_document,
)
from markdown_ingress.reporting import (
    persist_security_report as _persist_security_report,
)
from markdown_ingress.shared_results import BatchResult

logger = logging.getLogger(__name__)

PLAYWRIGHT_AVAILABLE = find_spec("playwright") is not None
_RETRY_TIMEOUT_INCREMENT_S: float = 30.0
_REPORT_PERSIST_ERRORS = (OSError, TypeError, ValueError)
_LAZY_EXPORTS = {
    "BatchIngestUseCase": (
        "markdown_ingress.application.batch_ingest_use_case",
        "BatchIngestUseCase",
    ),
    "GenerateSecurityReportUseCase": (
        "markdown_ingress.application.use_cases",
        "GenerateSecurityReportUseCase",
    ),
    "IngestUseCase": ("markdown_ingress.application.use_cases", "IngestUseCase"),
}


def __getattr__(name: str) -> object:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    from importlib import import_module

    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


def _lazy_class(name: str) -> Any:
    return globals().get(name) or __getattr__(name)


@dataclass(frozen=True)
class RetryIngestRequest:
    url: str
    mode: Mode = "auto"
    strict: bool = True
    allow_local_urls: object = UNSET
    model: str = "gpt-4"
    max_retries: int = 3
    enable_stealth: bool = True
    initial_timeout: float = 60.0
    max_timeout: float | None = None


def _maybe_persist(doc: SafeDocument | None, config: IngestConfig, url: str) -> None:
    """Persist a security report to disk if save_reports is enabled."""
    if doc is None or not config.save_reports:
        return
    try:
        _persist_report_for_document(doc, config.reports_dir)
    except _REPORT_PERSIST_ERRORS as exc:
        logger.warning("Failed to persist security report for %s: %s", url, exc)


def _validate_retry_timeout(field_name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number, got {type(value).__name__}")
    timeout = float(value)
    if not math.isfinite(timeout):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
    if timeout <= 0:
        raise ValueError(f"{field_name} must be > 0")
    return timeout


def ingest_resolved(
    url: str,
    config: IngestConfig,
    *,
    playwright_available: bool = PLAYWRIGHT_AVAILABLE,
) -> SafeDocument:
    """Execute ingestion using a fully resolved runtime config."""
    use_case = _lazy_class("IngestUseCase")(playwright_available=playwright_available)
    try:
        return cast(SafeDocument, use_case.execute(url, config))
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
    max_concurrent = _validate_batch_max_concurrent(max_concurrent)

    url_list = list(urls)
    runtime_config = build_runtime_config(**runtime_kwargs)
    use_case = _lazy_class("IngestUseCase")(playwright_available=playwright_available)
    try:
        batch_use_case = _lazy_class("BatchIngestUseCase")(ingest_use_case=use_case)
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
                except _REPORT_PERSIST_ERRORS as exc:
                    target_url = url_list[index] if index < len(url_list) else "<unknown>"
                    logger.warning(
                        "Failed to persist security report for %s: %s",
                        target_url,
                        exc,
                    )
        return cast(BatchResult, result)
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
    return run_ingest_many_blocking(
        lambda: ingest_many_async_impl(
            urls,
            playwright_available=playwright_available,
            max_concurrent=max_concurrent,
            on_progress=on_progress,
            **runtime_kwargs,
        )
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
    # Extreme mode fires on the last retry attempt, but at least attempt 1
    # so it isn't deferred unreasonably when max_retries is set high.
    extreme_attempt = max(1, max_retries - 2)
    return timeout, enable_stealth and attempt >= 1, attempt > 0 and attempt >= extreme_attempt


def _is_retryable_error(exc: Exception) -> bool:
    """Return True if the exception warrants another retry attempt."""
    return is_retryable_runtime_exception(exc)


def retry_ingest_impl(request: RetryIngestRequest) -> SafeDocument:
    """Implementation for retrying ingestion with escalating timeout and stealth."""
    validated_max_retries = validate_positive_int("max_retries", request.max_retries)
    validated_initial_timeout = _validate_retry_timeout("initial_timeout", request.initial_timeout)
    validated_max_timeout = (
        None
        if request.max_timeout is None
        else _validate_retry_timeout("max_timeout", request.max_timeout)
    )
    if validated_max_timeout is not None and validated_max_timeout < validated_initial_timeout:
        raise ValueError("max_timeout must be greater than or equal to initial_timeout")

    last_exception: Exception | None = None
    for attempt in range(validated_max_retries):
        try:
            from markdown_ingress.api import ingest as public_ingest

            timeout, use_stealth, use_extreme = _compute_retry_attempt_params(
                attempt,
                validated_max_retries,
                validated_initial_timeout,
                validated_max_timeout,
                request.enable_stealth,
            )
            if attempt > 0:
                logger.info(
                    "Retry attempt %d/%d for %s",
                    attempt + 1,
                    validated_max_retries,
                    request.url,
                )
                logger.info(
                    "Timeout: %ss, Stealth: %s, Extreme: %s", timeout, use_stealth, use_extreme
                )

            doc = public_ingest(
                request.url,
                mode=request.mode,
                strict=request.strict,
                allow_local_urls=request.allow_local_urls,
                model=request.model,
                timeout=timeout,
                stealth=use_stealth,
                extreme_mode=use_extreme,
            )
            doc.metadata = {
                **doc.metadata,
                "retry_attempts": attempt + 1,
                "retry_enabled": use_stealth,
                "extreme_mode_enabled": use_extreme,
                "final_timeout": timeout,
            }
            if attempt > 0:
                logger.info("Success on attempt %d", attempt + 1)
        except Exception as exc:
            last_exception = exc
            error_type = type(exc).__name__
            is_retryable = _is_retryable_error(exc)
            if attempt < validated_max_retries - 1:
                if is_retryable:
                    wait_time = min(2**attempt, 60)
                    logger.warning("%s on attempt %d: %s", error_type, attempt + 1, exc)
                    logger.info("Waiting %ds before retry...", wait_time)
                    time.sleep(wait_time)
                else:
                    logger.exception("Non-retryable error %s", error_type)
                    raise
            else:
                logger.exception(
                    "All %d attempts failed for %s; final error: %s",
                    validated_max_retries,
                    request.url,
                    error_type,
                )
                raise
        else:
            return doc

    if last_exception is not None:
        raise last_exception
    raise RuntimeError("retry_ingest exhausted retries without capturing an exception")


def generate_security_report_impl(url: str, **runtime_kwargs) -> SecurityReport:
    """Generate a detailed security report through the dedicated use case."""
    config = build_runtime_config(**runtime_kwargs)
    use_case = _lazy_class("IngestUseCase")(playwright_available=PLAYWRIGHT_AVAILABLE)
    try:
        report = cast(
            SecurityReport,
            _lazy_class("GenerateSecurityReportUseCase")(ingest_use_case=use_case).execute(
                url, config
            ),
        )
    finally:
        use_case.close()
    if config.save_reports:
        try:
            _persist_security_report(report, config.reports_dir)
        except _REPORT_PERSIST_ERRORS as exc:
            logger.warning("Failed to persist security report for %s: %s", url, exc)
    return report
