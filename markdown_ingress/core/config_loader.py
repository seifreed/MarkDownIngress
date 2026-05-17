"""ConfigLoader: load MarkDownIngress configuration from files and environment variables."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import yaml  # type: ignore[import-untyped]

from markdown_ingress.core.config import Config
from markdown_ingress.core.config_env import (
    EnvVarMapping,
    build_env_var_mapping,
    str_to_bool,
    str_to_bool_or_string,
)
from markdown_ingress.core.config_env_overrides import (
    FieldRestoreContext,
    apply_env_overrides,
    apply_scalar_list_and_custom_overrides,
    restore_field,
    validate_categorical_fields,
    validate_numeric_fields,
)


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
        config = self._apply_env_overrides(config)

        return config

    def _load_from_file(self, filepath: str) -> Config:
        """Load config from file (JSON or YAML)"""
        path = Path(filepath).expanduser()

        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {filepath}")

        content = path.read_text()

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

    def _restore_field(
        self,
        context: FieldRestoreContext,
        attr_name: str,
        message: str,
        *args: object,
    ) -> None:
        """Revert a field to its pre-env-override value and update explicit tracking."""
        restore_field(context, attr_name, message, *args)

    def _build_env_var_mapping(self) -> EnvVarMapping:
        """Return the mapping of env var name → (config attr, converter)."""
        return build_env_var_mapping(
            bool_converter=self._str_to_bool,
            bool_or_string_converter=self._str_to_bool_or_string,
        )

    def _apply_scalar_list_and_custom_overrides(
        self,
        config: Config,
        env_mapping: dict,
        explicit: set,
        previous_values: dict,
    ) -> None:
        """Apply scalar, list, and custom-pattern env var overrides to config in-place."""
        apply_scalar_list_and_custom_overrides(config, env_mapping, explicit, previous_values)

    def _validate_categorical_fields(
        self,
        config: Config,
        previous_values: dict,
        previous_explicit: dict,
        explicit: set,
    ) -> None:
        """Validate and restore categorical/enum fields that may have been set to invalid values."""
        validate_categorical_fields(config, previous_values, previous_explicit, explicit)

    def _validate_numeric_fields(
        self,
        config: Config,
        previous_values: dict,
        previous_explicit: dict,
        explicit: set,
    ) -> None:
        """Validate and restore numeric fields that fall outside their allowed bounds."""
        validate_numeric_fields(config, previous_values, previous_explicit, explicit)

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

        path.write_text(content)
