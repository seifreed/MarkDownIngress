"""Architecture safety checks for layer dependency direction."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_PREFIX = "markdown_ingress"
PACKAGE_ROOT = Path(__file__).resolve().parents[1] / PACKAGE_PREFIX
FORBIDDEN_IMPORTS = {
    "markdown_ingress.core": {
        "markdown_ingress.adapters",
        "markdown_ingress.application",
        "markdown_ingress.api_server",
        "markdown_ingress.cli",
    },
    "markdown_ingress.adapters": {
        "markdown_ingress.application",
        "markdown_ingress.api_server",
        "markdown_ingress.cli",
    },
    "markdown_ingress.application": {
        "markdown_ingress.api_server",
        "markdown_ingress.cli",
        "markdown_ingress.api",
        "markdown_ingress.api_runtime",
    },
    "markdown_ingress.cli": {
        "markdown_ingress.api_runtime",
    },
}


def _imported_modules(node: ast.AST, path: Path) -> list[str]:
    modules: list[str] = []
    if isinstance(node, ast.ImportFrom) and node.module:
        if node.level:
            base_parts = _module_of(path).split(".")
            if node.level <= len(base_parts):
                module_parts = base_parts[: len(base_parts) - node.level]
                if node.module:
                    module_parts.extend(node.module.split("."))
                modules.append(f"{PACKAGE_PREFIX}.{'.'.join(module_parts)}")
        else:
            modules.append(node.module)
    elif isinstance(node, ast.ImportFrom) and node.level:
        base_parts = _module_of(path).split(".")
        if node.level <= len(base_parts):
            modules.append(
                f"{PACKAGE_PREFIX}.{'.'.join(base_parts[: len(base_parts) - node.level])}"
            )
    elif isinstance(node, ast.Import):
        for alias in node.names:
            modules.append(alias.name)
    return modules


def _iter_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    imports: list[str] = []
    for node in ast.walk(tree):
        imports.extend(_imported_modules(node, path))
    return imports


def _module_of(path: Path) -> str:
    return path.with_suffix("").relative_to(PACKAGE_ROOT).as_posix().replace("/", ".")


def test_layer_import_rules_are_honored() -> None:
    root = PACKAGE_ROOT
    violations: list[tuple[str, str, str]] = []

    for path in sorted(root.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        module = f"{PACKAGE_PREFIX}.{_module_of(path)}"
        layer = next((key for key in FORBIDDEN_IMPORTS if module.startswith(key)), None)
        if not layer:
            continue

        for imported in _iter_imports(path):
            target = imported
            if target == "markdown_ingress":
                continue
            for bad_prefix in FORBIDDEN_IMPORTS[layer]:
                if target.startswith(bad_prefix):
                    violations.append((module, imported, bad_prefix))

    assert not violations
