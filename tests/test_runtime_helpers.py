"""Tests for shared runtime helper utilities."""

from __future__ import annotations

import pytest

from markdown_ingress import runtime_helpers
from markdown_ingress.runtime_helpers import (
    is_dependency_available,
    load_optional_module,
    load_optional_object,
)


def test_load_optional_module_returns_module() -> None:
    json_module = load_optional_module("json", purpose="json parsing")
    assert json_module.__name__ == "json"


def test_load_optional_module_missing_dependency_has_helpful_error() -> None:
    with pytest.raises(
        ImportError,
        match="does_not_exist_module_xyz",
    ):
        load_optional_module(
            "does_not_exist_module_xyz",
            pip_name="dummy-missing-pkg",
            purpose="missing feature",
        )


def test_load_optional_object_returns_named_object() -> None:
    loads = load_optional_object("json", "loads", purpose="json parsing")
    assert callable(loads)


def test_load_optional_object_missing_object_has_helpful_error() -> None:
    with pytest.raises(
        ImportError,
        match="does not export 'definitely_not_there'",
    ):
        load_optional_object("json", "definitely_not_there", purpose="json parsing")


def test_is_dependency_available_true_for_stdlib_module() -> None:
    assert is_dependency_available("json") is True


def test_is_dependency_available_returns_false_when_missing(monkeypatch) -> None:
    runtime_helpers._is_dependency_available_cached.cache_clear()
    monkeypatch.setattr(runtime_helpers, "find_spec", lambda name: None)
    assert is_dependency_available("does_not_exist_module_xyz") is False


def test_is_dependency_available_uses_cached_result(monkeypatch) -> None:
    calls = 0

    def fake_find_spec(name: str):
        nonlocal calls
        calls += 1
        return None

    runtime_helpers._is_dependency_available_cached.cache_clear()
    monkeypatch.setattr(runtime_helpers, "find_spec", fake_find_spec)

    assert is_dependency_available("does_not_exist_module_xyz") is False
    assert is_dependency_available("does_not_exist_module_xyz") is False
    assert calls == 1
