"""Import checks for example scripts that are safe at module import time."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


@pytest.mark.parametrize(
    "example_path",
    [
        Path("examples/advanced_stealth_example.py"),
        Path("examples/demo_resource_blocking.py"),
    ],
)
def test_import_safe_examples_load(example_path: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        f"markdown_ingress_example_{example_path.stem}", example_path
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    assert isinstance(module, ModuleType)

    original_path = list(sys.path)
    try:
        spec.loader.exec_module(module)
        assert sys.path == original_path
    finally:
        sys.path[:] = original_path
