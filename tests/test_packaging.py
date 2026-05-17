"""Packaging contract tests for runtime resources."""

from __future__ import annotations

import subprocess
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


def test_dockerfile_quotes_versioned_pip_requirements() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    tokens = dockerfile.replace("\\\n", " ").split()
    bare_version_specs = [
        token for token in tokens if ">=" in token and not token.startswith(('"', "'"))
    ]

    assert bare_version_specs == []


def test_distribution_artifacts_are_not_stale() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    current_version = pyproject["project"]["version"]

    distribution_dirs = [Path("dist_final"), Path("dist_fresh")]
    stale_artifacts = [
        path
        for directory in distribution_dirs
        if directory.exists()
        for path in directory.iterdir()
        if path.is_file() and current_version not in path.name
    ]

    assert stale_artifacts == []


def test_repository_does_not_track_ignored_files() -> None:
    if not Path(".git").exists():
        return

    result = subprocess.run(
        ["git", "ls-files", "-ci", "--exclude-standard"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.splitlines() == []
