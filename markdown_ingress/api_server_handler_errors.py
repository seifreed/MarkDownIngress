"""HTTP error mapping helpers for API server handlers."""

from __future__ import annotations

import logging
import sqlite3
from typing import NoReturn

import httpx
from fastapi import HTTPException

from markdown_ingress.core.policy import (
    DomainCircuitOpenError,
    PolicyBlockedError,
    UnsupportedContentTypeError,
)

INTERNAL_ERROR_DETAIL = "Internal server error"

_logger = logging.getLogger(__name__)


def is_playwright_runtime_import_error(exc: ImportError) -> bool:
    """Return whether this ImportError is the explicit render-mode Playwright denial."""
    message = str(exc)
    return message.startswith("Render mode requires Playwright") or message.startswith(
        "Playwright is not installed."
    )


def raise_runtime_http_error(exc: Exception) -> NoReturn:
    """Map expected runtime denials and environment errors to stable HTTP responses."""
    if isinstance(exc, ImportError) and is_playwright_runtime_import_error(exc):
        raise HTTPException(status_code=400, detail="Render mode requires Playwright")
    if isinstance(exc, UnsupportedContentTypeError):
        raise HTTPException(status_code=415, detail=str(exc))
    if isinstance(exc, DomainCircuitOpenError):
        raise HTTPException(status_code=429, detail=str(exc))
    if isinstance(exc, (httpx.InvalidURL, httpx.UnsupportedProtocol)):
        raise HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        mapped_status = status_code if 400 <= status_code < 500 else 502
        raise HTTPException(
            status_code=mapped_status,
            detail="Upstream fetch returned an HTTP error",
        )
    if isinstance(exc, httpx.TimeoutException):
        raise HTTPException(status_code=504, detail="Upstream fetch timed out")
    if isinstance(exc, httpx.RequestError):
        raise HTTPException(status_code=502, detail="Upstream fetch failed")
    if isinstance(exc, ValueError):
        _logger.warning("ValueError mapped to 400 Bad Request: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid request")
    if isinstance(exc, PolicyBlockedError):
        if exc.document is not None:
            _logger.info(
                "PolicyBlockedError flags=%s action=%s",
                exc.document.flags,
                exc.document.metadata.get("policy_action"),
            )
        raise HTTPException(
            status_code=403,
            detail={"type": "policy_blocked", "message": "Content blocked by security policy"},
        )
    raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL)


def log_runtime_error(message: str, exc: Exception, *args: object) -> None:
    """Keep expected HTTP-mapped failures out of server error logs."""
    if isinstance(
        exc,
        (
            ImportError,
            UnsupportedContentTypeError,
            DomainCircuitOpenError,
            httpx.InvalidURL,
            httpx.UnsupportedProtocol,
            httpx.HTTPStatusError,
            httpx.RequestError,
            ValueError,
            PolicyBlockedError,
        ),
    ):
        _logger.debug(message, *args, exc_info=True)
        return
    _logger.exception(message, *args)


def is_queue_full_error(exc: Exception) -> bool:
    """Return whether a RuntimeError from the job queue is the expected capacity denial."""
    return str(exc) == "Job queue is full"


def is_queue_unavailable_error(exc: Exception) -> bool:
    """Return whether the queue is operationally unavailable but not internally broken."""
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
