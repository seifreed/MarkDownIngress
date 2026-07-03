"""HTTP error mapping helpers for API server handlers."""

from __future__ import annotations

import logging
import sqlite3
from typing import NoReturn

from fastapi import HTTPException

from markdown_ingress.core.policy import PolicyBlockedError
from markdown_ingress.core.runtime_error_policy import map_runtime_exception_to_http

INTERNAL_ERROR_DETAIL = "Internal server error"

_logger = logging.getLogger(__name__)


def raise_runtime_http_error(exc: Exception) -> NoReturn:
    """Map expected runtime denials and environment errors to stable HTTP responses."""
    mapped = map_runtime_exception_to_http(exc)
    if mapped is None:
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL)
    status_code, detail = mapped
    if isinstance(exc, PolicyBlockedError):
        if exc.document is not None:
            _logger.info(
                "PolicyBlockedError flags=%s action=%s",
                exc.document.flags,
                exc.document.metadata.get("policy_action"),
            )
    if isinstance(exc, ValueError):
        _logger.warning("ValueError mapped to 400 Bad Request: %s", exc)
    raise HTTPException(status_code=status_code, detail=detail)


def log_runtime_error(message: str, exc: Exception, *args: object) -> None:
    """Keep expected HTTP-mapped failures out of server error logs."""
    mapped = map_runtime_exception_to_http(exc)
    if mapped is not None or isinstance(exc, PolicyBlockedError):
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
