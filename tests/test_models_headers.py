"""Tests for header handling on FetchResult."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from markdown_ingress.models import CaseInsensitiveHeaders, FetchResult


def test_fetch_result_headers_are_case_insensitive_from_plain_dict():
    result = FetchResult(
        html="<html></html>",
        url="https://unit.test/plain",
        status_code=200,
        final_url="https://unit.test/plain",
        headers={"Content-Type": "text/html", "X-Test": "1"},
        timing_ms=1.0,
        metadata={},
    )

    assert isinstance(result.headers, CaseInsensitiveHeaders)
    assert result.headers["content-type"] == "text/html"
    assert result.headers["CONTENT-TYPE"] == "text/html"
    assert result.headers.get("x-test") == "1"


def test_fetch_result_headers_are_case_insensitive_from_httpx_headers():
    result = FetchResult(
        html="<html></html>",
        url="https://unit.test/httpx",
        status_code=200,
        final_url="https://unit.test/httpx",
        headers=httpx.Headers({"Content-Type": "text/html; charset=utf-8"}),
        timing_ms=1.0,
        metadata={},
    )

    assert result.headers["content-type"] == "text/html; charset=utf-8"


def test_case_insensitive_headers_pop_with_none_default_matches_dict():
    headers = CaseInsensitiveHeaders({"Content-Type": "text/html"})

    assert headers.pop("missing", None) is None


def test_case_insensitive_headers_tolerate_non_string_lookup_defaults():
    headers = CaseInsensitiveHeaders({"Content-Type": "text/html"})

    assert headers.get(None, "fallback") == "fallback"
    assert headers.pop(None, "fallback") == "fallback"


def test_case_insensitive_headers_copy_preserves_case_insensitive_lookup():
    headers = CaseInsensitiveHeaders({"Content-Type": "text/html"})

    copied = headers.copy()

    assert isinstance(copied, CaseInsensitiveHeaders)
    assert copied["CONTENT-TYPE"] == "text/html"
    copied["X-Test"] = "1"
    assert copied["x-test"] == "1"


def test_case_insensitive_headers_reject_non_string_items():
    headers = CaseInsensitiveHeaders()
    unsafe_headers: Any = headers

    with pytest.raises(TypeError, match="header key must be a string"):
        unsafe_headers[None] = "value"

    with pytest.raises(TypeError, match="header value must be a string"):
        unsafe_headers["X-Test"] = 1


def test_case_insensitive_headers_setdefault_rejects_non_string_items():
    headers = CaseInsensitiveHeaders()
    unsafe_headers: Any = headers

    with pytest.raises(TypeError, match="header key must be a string"):
        unsafe_headers.setdefault(None, "value")

    with pytest.raises(TypeError, match="header value must be a string"):
        unsafe_headers.setdefault("X-Test", 1)
