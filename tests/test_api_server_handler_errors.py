"""Unit tests for API server runtime error mapping."""

from __future__ import annotations

import sqlite3

import httpx
import pytest
from fastapi import HTTPException

from markdown_ingress.api_server_handler_errors import (
    raise_runtime_http_error,
)
from markdown_ingress.core.policy import (
    DomainCircuitOpenError,
    PolicyBlockedError,
    UnsupportedContentTypeError,
)
from markdown_ingress.core.runtime_error_policy import (
    is_playwright_runtime_import_error,
    is_queue_full_error,
    is_queue_unavailable_error,
    is_retryable_runtime_exception,
    map_runtime_exception_to_http,
)
from markdown_ingress.models import SafeDocument


def _safe_document() -> SafeDocument:
    return SafeDocument(
        markdown="",
        metadata={"policy_action": "block"},
        token_estimate=0,
        content_hash="hash",
        injection_score=1.0,
        flags=["policy_blocked"],
    )


def test_is_playwright_runtime_import_error_messages() -> None:
    assert is_playwright_runtime_import_error(ImportError("Playwright is not installed."))
    assert is_playwright_runtime_import_error(ImportError("Render mode requires Playwright"))
    assert not is_playwright_runtime_import_error(ImportError("other import"))


@pytest.mark.parametrize(
    "exc,expected_code,expected_detail",
    [
        (ImportError("Playwright is not installed."), 400, "Render mode requires Playwright"),
        (UnsupportedContentTypeError("text/plain"), 415, "text/plain"),
        (DomainCircuitOpenError("Circuit open"), 429, "Circuit open"),
        (httpx.InvalidURL("bad"), 400, "bad"),
        (httpx.TimeoutException("timeout"), 504, "Upstream fetch timed out"),
        (httpx.RequestError("down"), 502, "Upstream fetch failed"),
        (ValueError("bad value"), 400, "Invalid request"),
    ],
)
def test_raise_runtime_http_error_mappings(
    exc: Exception,
    expected_code: int,
    expected_detail: str,
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        raise_runtime_http_error(exc)

    assert exc_info.value.status_code == expected_code
    assert exc_info.value.detail == expected_detail


def test_raise_runtime_http_error_http_status_error_mapping() -> None:
    request = httpx.Request("GET", "https://example.com")
    upstream_error = httpx.HTTPStatusError(
        "upstream",
        request=request,
        response=httpx.Response(status_code=502, request=request),
    )

    with pytest.raises(HTTPException) as exc_info:
        raise_runtime_http_error(upstream_error)

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "Upstream fetch returned an HTTP error"


def test_raise_runtime_http_error_policy_blocked_error() -> None:
    with pytest.raises(HTTPException) as exc_info:
        raise_runtime_http_error(PolicyBlockedError("blocked", document=_safe_document()))

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == {
        "type": "policy_blocked",
        "message": "Content blocked by security policy",
    }


def test_raise_runtime_http_error_falls_back_to_internal_error() -> None:
    with pytest.raises(HTTPException) as exc_info:
        raise_runtime_http_error(RuntimeError("boom"))

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Internal server error"


def test_is_retryable_runtime_exception_matches_existing_retry_policy() -> None:
    request = httpx.Request("GET", "https://example.com")
    assert is_retryable_runtime_exception(httpx.TimeoutException("timeout"))
    assert is_retryable_runtime_exception(
        httpx.HTTPStatusError(
            "status",
            request=request,
            response=httpx.Response(status_code=503, request=request),
        )
    )
    assert not is_retryable_runtime_exception(PolicyBlockedError("blocked"))


def test_map_runtime_exception_to_http_returns_none_for_unknown_errors() -> None:
    assert map_runtime_exception_to_http(RuntimeError("boom")) is None


@pytest.mark.parametrize(
    ("exception, expected_status"),
    [
        (RuntimeError("Job queue is full"), 429),
        (RuntimeError("Job queue is unavailable"), 503),
        (RuntimeError("Job queue is closed"), 503),
        (sqlite3.OperationalError("database is locked"), 503),
    ],
)
def test_map_runtime_exception_to_http_includes_job_queue_errors(
    exception: Exception,
    expected_status: int,
) -> None:
    assert map_runtime_exception_to_http(exception) == (
        expected_status,
        str(exception),
    )


def test_queue_error_predicates_are_consistent_with_mapper() -> None:
    unavailable_error = RuntimeError("Job queue backend read failed: malformed database schema")
    assert is_queue_unavailable_error(unavailable_error)
    assert map_runtime_exception_to_http(unavailable_error) is not None

    assert is_queue_full_error(RuntimeError("Job queue is full"))
    assert not is_queue_full_error(RuntimeError("not full"))
