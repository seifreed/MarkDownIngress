"""ConfigLoader: load MarkDownIngress configuration from files and environment variables."""

from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path
from typing import Any, cast

from markdown_ingress.core.config import Config
from markdown_ingress.core.config_env import (
    EnvVarMapping,
    build_env_var_mapping,
    str_to_bool,
    str_to_bool_or_string,
)
from markdown_ingress.core.config_env_overrides import (
    apply_env_overrides,
)

yaml = cast(Any, import_module("yaml"))


class ConfigLoader:
    """Load configuration from files and environment variables"""

    DEFAULT_LOCATIONS = (
        ".markdowningress.yaml",
        ".markdowningress.yml",
        ".markdowningress.json",
        "~/.config/markdowningress/config.yaml",
        "~/.config/markdowningress/config.yml",
        "~/.config/markdowningress/config.json",
    )

    def __init__(self, config_path: str | None = None):
        """
        Initialize config loader.

        Args:
            config_path: Explicit config file path (optional)
        """
        self.config_path = config_path

    def load(self) -> Config:
        """
        Load configuration with priority:
        1. Explicit config file path (if provided)
        2. Default locations (in order)
        3. Environment variables
        4. Defaults

        Returns:
            Loaded Config object
        """
        config = Config()  # Start with defaults

        # Try to load from file
        if self.config_path:
            # Explicit path provided
            config = self._load_from_file(self.config_path)
        else:
            # Try default locations
            for location in self.DEFAULT_LOCATIONS:
                expanded_path = Path(location).expanduser()
                if expanded_path.exists():
                    config = self._load_from_file(str(expanded_path))
                    break

        # Override with environment variables
        return self._apply_env_overrides(config)

    def _load_from_file(self, filepath: str) -> Config:
        """Load config from file (JSON or YAML)"""
        path = Path(filepath).expanduser()

        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {filepath}")

        content = path.read_text(encoding="utf-8")

        # Determine format from extension
        if filepath.endswith(".json"):
            return Config.from_json(content)
        elif filepath.endswith((".yaml", ".yml")):
            return Config.from_yaml(content)
        else:
            # Try to detect format
            try:
                return Config.from_json(content)
            except json.JSONDecodeError:
                try:
                    return Config.from_yaml(content)
                except yaml.YAMLError as exc:
                    raise ValueError(f"Unable to parse config file: {filepath}") from exc

    def _build_env_var_mapping(self) -> EnvVarMapping:
        """Return the mapping of env var name → (config attr, converter)."""
        return build_env_var_mapping(
            bool_converter=self._str_to_bool,
            bool_or_string_converter=self._str_to_bool_or_string,
        )

    def _apply_env_overrides(self, config: Config) -> Config:
        """Apply environment variable overrides."""
        return cast(Config, apply_env_overrides(config, self._build_env_var_mapping()))

    @staticmethod
    def _str_to_bool(value: str) -> bool:
        """Convert string to boolean.

        Handles common truthy and falsy string representations.
        Raises ValueError for unrecognized values so callers can preserve the
        previous config value instead of silently degrading behavior.
        """
        return str_to_bool(value)

    @classmethod
    def _str_to_bool_or_string(cls, value: str) -> bool | str:
        """Convert env values like true/false into bool, otherwise keep explicit paths."""
        return str_to_bool_or_string(value)

    def save(self, config: Config, filepath: str):
        """
        Save configuration to file.

        Args:
            config: Config object to save
            filepath: Output file path (.json or .yaml)
        """
        path = Path(filepath).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)

        if filepath.endswith(".json"):
            content = config.to_json()
        elif filepath.endswith((".yaml", ".yml")):
            content = config.to_yaml()
        else:
            raise ValueError("Config file must be .json, .yaml, or .yml")

        path.write_text(content, encoding="utf-8")
