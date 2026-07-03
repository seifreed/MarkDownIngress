"""Architecture boundary tests."""

from __future__ import annotations

from pathlib import Path

from markdown_ingress import __file__ as package_init


def _iter_python_files(root: Path) -> list[Path]:
    return [
        path for path in root.rglob("*.py") if path.is_file() and "__pycache__" not in path.parts
    ]


def test_core_layer_does_not_import_adapter_implementations():
    core_dir = Path(package_init).resolve().parent / "core"
    violations: list[tuple[Path, str]] = []
    for path in _iter_python_files(core_dir):
        for line in path.read_text().splitlines():
            if line.startswith("from markdown_ingress.adapters.") or line.startswith(
                "import markdown_ingress.adapters."
            ):
                violations.append((path, line.strip()))
    assert not violations, f"Core layer imports adapters: {violations}"


def test_core_layer_does_not_import_infrastructure_servers():
    core_dir = Path(package_init).resolve().parent / "core"
    violations: list[tuple[Path, str]] = []
    for path in _iter_python_files(core_dir):
        for line in path.read_text().splitlines():
            if line.startswith("from markdown_ingress.cli.") or line.startswith(
                "from markdown_ingress.api_server."
            ):
                violations.append((path, line.strip()))
    assert not violations, f"Core layer imports presentation/infrastructure modules: {violations}"
