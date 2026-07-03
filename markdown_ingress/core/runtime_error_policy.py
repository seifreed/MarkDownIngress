"""Runtime exception policy used by API and server transports."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

import httpx

from markdown_ingress.core.policy import (
    DomainCircuitOpenError,
    PolicyBlockedError,
    UnsupportedContentTypeError,
)

ErrorDetailFactory = Callable[[Exception], object]
ErrorMappingEntry = tuple[tuple[type[Exception], ...], int, ErrorDetailFactory]

RETRYABLE_HTTP_STATUSES: frozenset[int] = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
RETRYABLE_EXCEPTION_NAMES = {
    "ConnectTimeout",
    "ReadTimeout",
    "ConnectionError",
    "TargetClosedError",
}

_RUNTIME_ERROR_MAP: tuple[ErrorMappingEntry, ...] = (
    ((UnsupportedContentTypeError,), 415, lambda exc: str(exc)),
    ((DomainCircuitOpenError,), 429, lambda exc: str(exc)),
    ((httpx.InvalidURL, httpx.UnsupportedProtocol), 400, lambda exc: str(exc)),
    ((httpx.TimeoutException,), 504, lambda _exc: "Upstream fetch timed out"),
    ((httpx.RequestError,), 502, lambda _exc: "Upstream fetch failed"),
    ((ValueError,), 400, lambda _exc: "Invalid request"),
)


def is_playwright_runtime_import_error(exc: ImportError) -> bool:
    """Return whether ImportError comes from missing Playwright render dependency."""
    message = str(exc)
    return message.startswith("Render mode requires Playwright") or message.startswith(
        "Playwright is not installed."
    )


def is_queue_full_error(exc: Exception) -> bool:
    """Return whether queue capacity has been reached."""
    return str(exc) == "Job queue is full"


def is_queue_unavailable_error(exc: Exception) -> bool:
    """Return whether queue failures are operational and should be retriable."""
    message = str(exc)
    return (
        isinstance(exc, sqlite3.Error)
        or message
        in {
            "Job queue is unavailable",
            "Job queue is closing",
            "Job queue is closed",
            "Job queue lease was lost; this instance can no longer accept or execute jobs",
            "Job queue is unavailable because the DB is owned by another active instance",
            "Job queue backend is temporarily unavailable because the current owner is busy",
        }
        or message.startswith("Job queue backend read failed:")
    )


def map_runtime_exception_to_http(exc: Exception) -> tuple[int, object] | None:
    """Map runtime ingestion exceptions to HTTP status and detail.

    Returns ``None`` when the error should be treated as an internal server error.
    """
    if isinstance(exc, ImportError) and is_playwright_runtime_import_error(exc):
        return 400, "Render mode requires Playwright"

    for match_types, status_code, detail in _RUNTIME_ERROR_MAP:
        if isinstance(exc, match_types):
            return status_code, detail(exc)

    if is_queue_full_error(exc):
        return 429, str(exc)
    if is_queue_unavailable_error(exc):
        return 503, str(exc)

    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        mapped_status = status_code if 400 <= status_code < 500 else 502
        return mapped_status, "Upstream fetch returned an HTTP error"

    if isinstance(exc, PolicyBlockedError):
        return 403, {
            "type": "policy_blocked",
            "message": "Content blocked by security policy",
        }

    return None


def is_retryable_runtime_exception(exc: Exception) -> bool:
    """Return True for errors where retry attempts can improve outcomes."""
    if isinstance(exc, PolicyBlockedError):
        return False
    if isinstance(
        exc, (TimeoutError, httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError)
    ):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_HTTP_STATUSES
    return type(exc).__name__ in RETRYABLE_EXCEPTION_NAMES
