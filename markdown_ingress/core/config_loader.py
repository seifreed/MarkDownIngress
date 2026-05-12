"""ConfigLoader: load MarkDownIngress configuration from files and environment variables."""

from __future__ import annotations

import json
import logging
import math
import os
from collections.abc import Callable
from dataclasses import fields
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from markdown_ingress.config_models import (
    _validate_output_profile_name,
    _validate_output_representations,
)
from markdown_ingress.core.config import (
    VALID_CACHE_TYPES,
    VALID_CHUNKING_STRATEGIES,
    VALID_MODES,
    VALID_OUTPUT_FORMATS,
    VALID_POLICIES,
    Config,
    _validate_regex_patterns,
    _validate_string_list,
)

_logger = logging.getLogger(__name__)


def _parse_csv_string_list(field_name: str, value: str) -> list[str]:
    parsed = [item.strip() for item in value.split(",") if item.strip()]
    return _validate_string_list(field_name, parsed)


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
        config: Config,
        attr_name: str,
        previous_values: dict,
        previous_explicit: dict,
        explicit: set,
        message: str,
        *args: object,
    ) -> None:
        """Revert a field to its pre-env-override value and update explicit tracking."""
        _logger.warning(message, *args)
        setattr(config, attr_name, previous_values[attr_name])
        if previous_explicit.get(attr_name, False):
            explicit.add(attr_name)
        else:
            explicit.discard(attr_name)

    def _build_env_var_mapping(self) -> dict[str, tuple[str, Callable[[str], object]]]:
        """Return the mapping of env var name → (config attr, converter)."""
        return {
            "MDI_MODE": ("mode", str),
            "MDI_TIMEOUT": ("timeout", float),
            "MDI_AUTO_RENDER_THRESHOLD": ("auto_render_threshold", int),
            "MDI_STRICT": ("strict", self._str_to_bool),
            "MDI_ALLOW_LOCAL_URLS": ("allow_local_urls", self._str_to_bool),
            "MDI_MODEL": ("model", str),
            "MDI_CACHE_ENABLED": ("cache_enabled", self._str_to_bool),
            "MDI_CACHE_TYPE": ("cache_type", str),
            "MDI_CACHE_TTL": ("cache_ttl", int),
            "MDI_CACHE_PATH": ("cache_path", str),
            "MDI_BATCH_MAX_CONCURRENT": ("batch_max_concurrent", int),
            "MDI_BATCH_TIMEOUT": ("batch_timeout", float),
            "MDI_POLICY": ("policy", str),
            "MDI_POLICY_NAME": ("policy", str),
            "MDI_OUTPUT_FORMAT": ("output_format", str),
            "MDI_OUTPUT_PROFILE": ("output_profile", str),
            "MDI_EXTRACT_BLOCKS": ("extract_blocks", self._str_to_bool),
            "MDI_EXTRACT_METADATA": ("extract_metadata", self._str_to_bool),
            "MDI_EXTRACT_LINKS": ("extract_links", self._str_to_bool),
            "MDI_ADVANCED_SECURITY": ("advanced_security", self._str_to_bool),
            "MDI_USE_LLM": ("use_llm", self._str_to_bool),
            "MDI_DETECT_LANGUAGE": ("detect_language", self._str_to_bool),
            "MDI_NORMALIZE_MULTILINGUAL": ("normalize_multilingual", self._str_to_bool),
            "MDI_INCLUDE_SECURITY_EXPLANATION": (
                "include_security_explanation",
                self._str_to_bool,
            ),
            "MDI_CHUNKING_STRATEGY": ("chunking_strategy", str),
            "MDI_CHUNK_SIZE": ("chunk_size", int),
            "MDI_CHUNK_OVERLAP": ("chunk_overlap", int),
            "MDI_SAVE_REPORTS": ("save_reports", self._str_to_bool),
            "MDI_REPORTS_DIR": ("reports_dir", str),
            "MDI_RENDER_COST_BUDGET": ("render_cost_budget", int),
            "MDI_INCLUDE_OBSERVABILITY": ("include_observability", self._str_to_bool),
            "MDI_STEALTH": ("stealth", self._str_to_bool),
            "MDI_DISABLE_HTTP2": ("disable_http2", self._str_to_bool),
            "MDI_EXTREME_MODE": ("extreme_mode", self._str_to_bool),
            "MDI_SCREENSHOT": ("screenshot", self._str_to_bool_or_string),
            "MDI_FETCHER_USER_AGENT": ("fetcher_user_agent", str),
            "MDI_DOMAIN_REQUEST_INTERVAL": ("domain_request_interval", float),
            "MDI_CIRCUIT_BREAKER_THRESHOLD": ("circuit_breaker_threshold", int),
            "MDI_CIRCUIT_BREAKER_OPEN_SECONDS": ("circuit_breaker_open_seconds", float),
        }

    def _apply_scalar_list_and_custom_overrides(
        self,
        config: Config,
        env_mapping: dict,
        explicit: set,
        previous_values: dict,
    ) -> None:
        """Apply scalar, list, and custom-pattern env var overrides to config in-place."""
        for env_var, (attr_name, converter) in env_mapping.items():
            value = os.getenv(env_var)
            if value is not None:
                try:
                    setattr(config, attr_name, converter(value))
                    explicit.add(attr_name)
                except (ValueError, TypeError) as e:
                    _logger.warning(
                        "Invalid value for %s (%s=%s): %s. Keeping previous value.",
                        attr_name,
                        env_var,
                        value,
                        e,
                    )

        custom_patterns_env = os.getenv("MDI_CUSTOM_PATTERNS")
        if custom_patterns_env:
            patterns = _parse_csv_string_list("custom_patterns", custom_patterns_env)
            if patterns:
                try:
                    _validate_regex_patterns(patterns)
                except ValueError as e:
                    _logger.warning(
                        "Invalid value for custom_patterns (%s=%s): %s. Keeping previous value.",
                        "MDI_CUSTOM_PATTERNS",
                        custom_patterns_env,
                        e,
                    )
                else:
                    config.custom_patterns = patterns
                    explicit.add("custom_patterns")

        list_env_mapping: dict[str, str] = {
            "MDI_PLUGIN_DIRS": "plugin_dirs",
            "MDI_OUTPUT_FORMATS": "output_formats",
        }
        for env_var, attr_name in list_env_mapping.items():
            value = os.getenv(env_var)
            if value is None:
                continue
            try:
                parsed = _parse_csv_string_list(attr_name, value)
                if attr_name == "output_formats":
                    parsed = _validate_output_representations(parsed)
                setattr(config, attr_name, parsed)
                explicit.add(attr_name)
            except (ValueError, TypeError) as e:
                _logger.warning(
                    "Invalid value for %s (%s=%s): %s. Keeping previous value.",
                    attr_name,
                    env_var,
                    value,
                    e,
                )

    def _validate_categorical_fields(
        self,
        config: Config,
        previous_values: dict,
        previous_explicit: dict,
        explicit: set,
    ) -> None:
        """Validate and restore categorical/enum fields that may have been set to invalid values."""

        def restore(attr, message, *args) -> None:
            self._restore_field(
                config, attr, previous_values, previous_explicit, explicit, message, *args
            )

        if config.mode not in VALID_MODES:
            restore(
                "mode",
                "Invalid mode '%s' from environment, valid values: %s. Keeping previous value %r.",
                config.mode,
                VALID_MODES,
                previous_values["mode"],
            )
        if config.cache_type not in VALID_CACHE_TYPES:
            restore(
                "cache_type",
                "Invalid cache_type '%s' from environment, valid values: %s. "
                "Keeping previous value %r.",
                config.cache_type,
                VALID_CACHE_TYPES,
                previous_values["cache_type"],
            )
        if config.output_format not in VALID_OUTPUT_FORMATS:
            restore(
                "output_format",
                "Invalid output_format '%s' from environment, valid values: %s. "
                "Keeping previous value %r.",
                config.output_format,
                VALID_OUTPUT_FORMATS,
                previous_values["output_format"],
            )
        try:
            config.output_profile = (
                _validate_output_profile_name(config.output_profile) or "default"
            )
        except ValueError as exc:
            restore(
                "output_profile",
                "Invalid output_profile '%s' from environment: %s. Keeping previous value %r.",
                config.output_profile,
                exc,
                previous_values["output_profile"],
            )
        if config.auto_render_threshold < 1:
            restore(
                "auto_render_threshold",
                "Invalid auto_render_threshold %r from environment, must be >= 1. "
                "Keeping previous value %r.",
                config.auto_render_threshold,
                previous_values["auto_render_threshold"],
            )
        if config.render_cost_budget is not None and config.render_cost_budget < 1:
            restore(
                "render_cost_budget",
                "Invalid render_cost_budget %r from environment, must be >= 1. "
                "Keeping previous value %r.",
                config.render_cost_budget,
                previous_values["render_cost_budget"],
            )
        if not config.output_formats:
            restore(
                "output_formats",
                "Invalid output_formats from environment, list cannot be empty. "
                "Keeping previous value %r.",
                previous_values["output_formats"],
            )
        else:
            try:
                config.output_formats = _validate_output_representations(config.output_formats)
            except (ValueError, TypeError) as exc:
                restore(
                    "output_formats",
                    "Invalid output_formats from environment: %s. Keeping previous value %r.",
                    exc,
                    previous_values["output_formats"],
                )
        if config.chunking_strategy not in VALID_CHUNKING_STRATEGIES:
            restore(
                "chunking_strategy",
                "Invalid chunking_strategy '%s' from environment, valid values: %s. "
                "Keeping previous value %r.",
                config.chunking_strategy,
                VALID_CHUNKING_STRATEGIES,
                previous_values["chunking_strategy"],
            )
        if config.policy not in VALID_POLICIES:
            restore(
                "policy",
                "Invalid policy '%s' from environment, valid values: %s. "
                "Keeping previous value %r.",
                config.policy,
                VALID_POLICIES,
                previous_values["policy"],
            )

    def _validate_numeric_fields(
        self,
        config: Config,
        previous_values: dict,
        previous_explicit: dict,
        explicit: set,
    ) -> None:
        """Validate and restore numeric fields that fall outside their allowed bounds."""

        def restore(attr, message, *args) -> None:
            self._restore_field(
                config, attr, previous_values, previous_explicit, explicit, message, *args
            )

        if config.timeout <= 0 or config.timeout > 3600:
            restore(
                "timeout",
                "Invalid timeout '%s' from environment, must be > 0 and <= 3600. "
                "Keeping previous value %r.",
                config.timeout,
                previous_values["timeout"],
            )
        elif not math.isfinite(config.timeout):
            restore(
                "timeout",
                "Invalid timeout '%s' from environment, must be finite. Keeping previous value %r.",
                config.timeout,
                previous_values["timeout"],
            )
        if config.chunk_size < 100 or config.chunk_size > 50000:
            restore(
                "chunk_size",
                "Invalid chunk_size '%s' from environment, must be 100-50000. "
                "Keeping previous value %r.",
                config.chunk_size,
                previous_values["chunk_size"],
            )
        if config.chunk_overlap < 0 or config.chunk_overlap > 10000:
            restore(
                "chunk_overlap",
                "Invalid chunk_overlap '%s' from environment, must be 0-10000. "
                "Keeping previous value %r.",
                config.chunk_overlap,
                previous_values["chunk_overlap"],
            )
        if config.chunk_overlap >= config.chunk_size:
            restore(
                "chunk_overlap",
                "chunk_overlap (%s) must be less than chunk_size (%s) after "
                "env overrides. Keeping previous value %r.",
                config.chunk_overlap,
                config.chunk_size,
                previous_values["chunk_overlap"],
            )
        if config.domain_request_interval < 0.0:
            restore(
                "domain_request_interval",
                "Invalid domain_request_interval '%s' from environment, must be "
                ">= 0.0. Keeping previous value %r.",
                config.domain_request_interval,
                previous_values["domain_request_interval"],
            )
        elif not math.isfinite(config.domain_request_interval):
            restore(
                "domain_request_interval",
                "Invalid domain_request_interval '%s' from environment, must be "
                "finite. Keeping previous value %r.",
                config.domain_request_interval,
                previous_values["domain_request_interval"],
            )
        if config.circuit_breaker_threshold < 1:
            restore(
                "circuit_breaker_threshold",
                "Invalid circuit_breaker_threshold '%s' from environment, must be "
                ">= 1. Keeping previous value %r.",
                config.circuit_breaker_threshold,
                previous_values["circuit_breaker_threshold"],
            )
        if config.circuit_breaker_open_seconds <= 0.0:
            restore(
                "circuit_breaker_open_seconds",
                "Invalid circuit_breaker_open_seconds '%s' from environment, must "
                "be > 0.0. Keeping previous value %r.",
                config.circuit_breaker_open_seconds,
                previous_values["circuit_breaker_open_seconds"],
            )
        elif not math.isfinite(config.circuit_breaker_open_seconds):
            restore(
                "circuit_breaker_open_seconds",
                "Invalid circuit_breaker_open_seconds '%s' from environment, must "
                "be finite. Keeping previous value %r.",
                config.circuit_breaker_open_seconds,
                previous_values["circuit_breaker_open_seconds"],
            )
        if config.cache_ttl <= 0:
            restore(
                "cache_ttl",
                "Invalid cache_ttl '%s' from environment, must be > 0. Keeping previous value %r.",
                config.cache_ttl,
                previous_values["cache_ttl"],
            )
        if config.batch_max_concurrent < 1:
            restore(
                "batch_max_concurrent",
                "Invalid batch_max_concurrent '%s' from environment, must be >= 1. "
                "Keeping previous value %r.",
                config.batch_max_concurrent,
                previous_values["batch_max_concurrent"],
            )
        if config.batch_timeout <= 0.0:
            restore(
                "batch_timeout",
                "Invalid batch_timeout '%s' from environment, must be > 0.0. "
                "Keeping previous value %r.",
                config.batch_timeout,
                previous_values["batch_timeout"],
            )
        elif not math.isfinite(config.batch_timeout):
            restore(
                "batch_timeout",
                "Invalid batch_timeout '%s' from environment, must be finite. "
                "Keeping previous value %r.",
                config.batch_timeout,
                previous_values["batch_timeout"],
            )

    def _apply_env_overrides(self, config: Config) -> Config:
        """Apply environment variable overrides."""
        explicit = set(config.explicit_keys())
        previous_explicit = {
            config_field.name: config_field.name in explicit
            for config_field in fields(Config)
            if not config_field.name.startswith("_")
        }
        env_policy = os.getenv("MDI_POLICY")
        env_policy_name = os.getenv("MDI_POLICY_NAME")
        if env_policy is not None and env_policy_name is not None and env_policy != env_policy_name:
            raise ValueError(
                "Environment cannot define both MDI_POLICY and MDI_POLICY_NAME "
                "with different values"
            )
        env_mapping = self._build_env_var_mapping()
        previous_values = {
            attr_name: getattr(config, attr_name) for _, (attr_name, _) in env_mapping.items()
        }
        self._apply_scalar_list_and_custom_overrides(config, env_mapping, explicit, previous_values)
        self._validate_categorical_fields(config, previous_values, previous_explicit, explicit)
        self._validate_numeric_fields(config, previous_values, previous_explicit, explicit)
        config._explicit_keys = frozenset(explicit)
        return config

    @staticmethod
    def _str_to_bool(value: str) -> bool:
        """Convert string to boolean.

        Handles common truthy and falsy string representations.
        Raises ValueError for unrecognized values so callers can preserve the
        previous config value instead of silently degrading behavior.
        """
        lower = value.strip().lower()
        if lower in ("true", "1", "yes", "on", "enabled"):
            return True
        if lower in ("false", "0", "no", "off", "disabled"):
            return False
        raise ValueError(
            f"invalid boolean {value!r}; expected one of "
            "true/false/1/0/yes/no/on/off/enabled/disabled"
        )

    @classmethod
    def _str_to_bool_or_string(cls, value: str) -> bool | str:
        """Convert env values like true/false into bool, otherwise keep explicit paths."""
        normalized = value.strip()
        lowered = normalized.lower()
        if lowered in ("true", "1", "yes", "on", "enabled"):
            return True
        if lowered in ("false", "0", "no", "off", "disabled"):
            return False
        return normalized

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
