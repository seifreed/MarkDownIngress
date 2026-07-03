"""HTTP error mapping helpers for API server handlers."""

from __future__ import annotations

import logging
from typing import NoReturn

from fastapi import HTTPException

from markdown_ingress.core.policy import PolicyBlockedError
from markdown_ingress.core.runtime_error_policy import (
    is_queue_full_error as _is_queue_full_error,
)
from markdown_ingress.core.runtime_error_policy import (
    is_queue_unavailable_error as _is_queue_unavailable_error,
)
from markdown_ingress.core.runtime_error_policy import (
    map_runtime_exception_to_http,
)

INTERNAL_ERROR_DETAIL = "Internal server error"

_logger = logging.getLogger(__name__)


def raise_runtime_http_error(exc: Exception) -> NoReturn:
    """Map expected runtime denials and environment errors to stable HTTP responses."""
    mapped = map_runtime_exception_to_http(exc)
    if mapped is None:
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL) from exc
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
    raise HTTPException(status_code=status_code, detail=detail) from exc


def log_runtime_error(message: str, exc: Exception, *args: object) -> None:
    """Keep expected HTTP-mapped failures out of server error logs."""
    mapped = map_runtime_exception_to_http(exc)
    if mapped is not None or isinstance(exc, PolicyBlockedError):
        _logger.debug(message, *args, exc_info=True)
        return
    _logger.exception(message, *args)


def is_queue_full_error(exc: Exception) -> bool:
    """Backward-compatible adapter for queue-capacity checks."""
    return _is_queue_full_error(exc)


def is_queue_unavailable_error(exc: Exception) -> bool:
    """Backward-compatible adapter for queue availability checks."""
    return _is_queue_unavailable_error(exc)
