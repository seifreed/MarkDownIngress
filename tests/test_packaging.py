"""Packaging contract tests for runtime resources."""

from __future__ import annotations

import tomllib
from pathlib import Path


def test_bundled_nova_rules_are_declared_as_package_data() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    package_data = pyproject["tool"]["setuptools"]["package-data"]

    assert "markdown_ingress.rules" in package_data
    assert "*.nova" in package_data["markdown_ingress.rules"]


def test_public_docs_do_not_contain_local_machine_paths() -> None:
    docs = [
        Path("README.md"),
        Path("CHANGELOG.md"),
        Path("BUG_SUMMARY.md"),
    ]

    for path in docs:
        text = path.read_text(encoding="utf-8")
        assert "/Users/" not in text, path
        assert "file://" not in text, path
