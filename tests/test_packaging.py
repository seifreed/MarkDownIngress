"""Packaging contract tests for runtime resources."""

from __future__ import annotations

import ast
import re
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


def test_readme_python_code_blocks_parse() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    failures: list[str] = []
    for index, match in enumerate(re.finditer(r"```python\n(.*?)\n```", readme, re.S), start=1):
        snippet = match.group(1)
        try:
            ast.parse(snippet)
        except SyntaxError as exc:
            failures.append(f"block {index}: line {exc.lineno}: {exc.msg}")

    assert failures == []


def test_public_examples_do_not_index_batch_errors_by_url() -> None:
    public_examples = [
        Path("README.md"),
        Path("examples/library_batch_async.py"),
    ]

    for path in public_examples:
        text = path.read_text(encoding="utf-8")
        assert ".errors[" not in text, path


def test_runtime_text_file_io_uses_explicit_utf8_encoding() -> None:
    runtime_files = sorted(Path("markdown_ingress").rglob("*.py"))
    violations: list[str] = []

    def has_encoding(call: ast.Call) -> bool:
        return any(keyword.arg == "encoding" for keyword in call.keywords)

    def call_mode(call: ast.Call, mode_arg_index: int) -> str:
        for keyword in call.keywords:
            if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                return str(keyword.value.value)
        if len(call.args) > mode_arg_index:
            mode_arg = call.args[mode_arg_index]
            if isinstance(mode_arg, ast.Constant):
                return str(mode_arg.value)
        return "r"

    for path in runtime_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute) and node.func.attr in {
                "read_text",
                "write_text",
            }:
                if not has_encoding(node):
                    violations.append(f"{path}:{node.lineno}: {node.func.attr} missing encoding")
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "open":
                if "b" not in call_mode(node, 0) and not has_encoding(node):
                    violations.append(f"{path}:{node.lineno}: open missing encoding")
            elif isinstance(node.func, ast.Name) and node.func.id == "open":
                if "b" not in call_mode(node, 1) and not has_encoding(node):
                    violations.append(f"{path}:{node.lineno}: open missing encoding")

    assert violations == []


def test_ci_workflow_covers_public_docs_examples_and_local_gate() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "paths-ignore" not in workflow
    assert "black --check ." in workflow
    assert "mypy markdown_ingress tests" in workflow
    assert '-m "not baseline and not campaign"' in workflow


def test_ci_security_job_audits_project_dependencies() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    project_install = 'pip install -e ".[api,render]" bandit[toml] pip-audit'
    dependency_audit = "run: pip-audit . --skip-editable --progress-spinner off"

    assert project_install in workflow
    assert 'pip install -e ".[all]" bandit[toml] pip-audit' not in workflow
    assert "pip install bandit[toml] safety" not in workflow
    assert "safety check" not in workflow
    assert "bandit -q -r markdown_ingress" in workflow
    assert dependency_audit in workflow
    assert workflow.index(project_install) < workflow.index(dependency_audit)


def test_project_metadata_contains_public_repository_and_author_identity() -> None:
    pyproject_text = Path("pyproject.toml").read_text(encoding="utf-8")
    pyproject = tomllib.loads(pyproject_text)

    project = pyproject["project"]
    expected_person = {"name": "Marc Rivero Lopez", "email": "mriverolopez@gmail.com"}

    assert project["authors"] == [expected_person]
    assert project["maintainers"] == [expected_person]
    assert project["urls"]["Homepage"] == "https://github.com/seifreed/MarkDownIngress"
    assert project["urls"]["Repository"] == "https://github.com/seifreed/MarkDownIngress"
    assert project["urls"]["Author GitHub (@seifreed)"] == "https://github.com/seifreed"
    assert "@seifreed" in pyproject_text


def test_dev_extra_does_not_install_optional_nova_stack_by_default() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    optional_dependencies = pyproject["project"]["optional-dependencies"]

    assert "nova-hunting>=0.1.0" not in optional_dependencies["dev"]
    assert "nova-hunting>=0.1.0" in optional_dependencies["security"]
    assert "nova-hunting>=0.1.0" in optional_dependencies["all"]


def test_optional_nova_dependency_is_not_statically_imported() -> None:
    tree = ast.parse(Path("markdown_ingress/core/nova_guard.py").read_text(encoding="utf-8"))

    static_nova_imports = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "nova"
    ]

    assert static_nova_imports == []


def test_publish_workflow_verifies_before_upload() -> None:
    workflow = Path(".github/workflows/publish.yml").read_text(encoding="utf-8")
    upload_index = workflow.index("twine upload dist/*")

    required_steps = [
        'pip install -e ".[dev]" bandit[toml] build twine',
        "ruff check .",
        "black --check .",
        "mypy markdown_ingress tests",
        "bandit -q -r markdown_ingress",
        'python -m pytest -q -m "not baseline and not campaign"',
    ]
    for step in required_steps:
        assert step in workflow
        assert workflow.index(step) < upload_index


def test_publish_workflow_creates_github_release_assets_from_tag() -> None:
    workflow = Path(".github/workflows/publish.yml").read_text(encoding="utf-8")
    release_index = workflow.index('gh release create "$tag" dist/*')

    required_steps = [
        "tags:",
        "- 'v*'",
        "permissions:",
        "contents: write",
        "concurrency:",
        "GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}",
        'gh release view "$tag" --repo "$GITHUB_REPOSITORY"',
        'gh release upload "$tag" dist/* --repo "$GITHUB_REPOSITORY" --clobber',
        'gh release create "$tag" dist/*',
        "--verify-tag",
        "--generate-notes",
    ]
    for step in required_steps:
        assert step in workflow

    assert workflow.index("python -m build") < release_index
    assert workflow.index("twine check dist/*") < release_index


def test_dockerfile_quotes_versioned_pip_requirements() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    tokens = dockerfile.replace("\\\n", " ").split()
    bare_version_specs = [
        token for token in tokens if ">=" in token and not token.startswith(('"', "'"))
    ]

    assert bare_version_specs == []


def test_dockerignore_is_tracked_and_excludes_local_artifacts() -> None:
    dockerignore = Path(".dockerignore")
    assert dockerignore.exists()

    if Path(".git").exists():
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", ".dockerignore"],
            capture_output=True,
            text=True,
        )
        ignored = subprocess.run(
            ["git", "check-ignore", ".dockerignore"],
            capture_output=True,
            text=True,
        )
        assert tracked.returncode == 0
        assert ignored.returncode == 1

    patterns = set(dockerignore.read_text(encoding="utf-8").splitlines())
    required_patterns = {
        ".git/",
        "build/",
        "dist/",
        "venv/",
        ".venv",
        "htmlcov/",
        "artifacts/",
        ".env",
        ".env.*",
        "secrets.json",
    }

    assert required_patterns <= patterns


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
