"""Application-level configuration loading facade."""

from __future__ import annotations

from markdown_ingress.core.config import Config
from markdown_ingress.core.config_loader import ConfigLoader


def load_runtime_config(config_path: str | None = None) -> Config:
    """Load and return runtime config used by CLI and other adapters."""
    return ConfigLoader(config_path).load()


__all__ = ["load_runtime_config"]
