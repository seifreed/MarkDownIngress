"""Shared configuration validation helpers."""

from __future__ import annotations

import copy
import math
import re
from dataclasses import MISSING, fields
from typing import Any

VALID_WAIT_UNTIL = ("networkidle", "load", "domcontentloaded")
VALID_OUTPUT_FORMATS = ("text", "json", "markdown")
VALID_OUTPUT_REPRESENTATIONS = ("markdown", "blocks", "chunks", "metadata", "security")
VALID_POLICY_NAMES = ("permissive", "normal", "strict", "paranoid", "moderate")
VALID_OUTPUT_PROFILES = ("default", "llm_safe", "rag_chunkable", "for_search", "for_archive")


def ensure_bool(field_name: str, value: object) -> bool:
    """Return a boolean value or reject untyped config input."""
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a bool, got {type(value).__name__}")
    return value


def ensure_optional_bool(field_name: str, value: object) -> bool | None:
    """Return an optional boolean value or reject untyped config input."""
    if value is None:
        return None
    return ensure_bool(field_name, value)


def ensure_str(field_name: str, value: object) -> str:
    """Return a string value or reject untyped config input."""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string, got {type(value).__name__}")
    return value


def ensure_optional_str(field_name: str, value: object) -> str | None:
    """Return an optional string value or reject untyped config input."""
    if value is None:
        return None
    return ensure_str(field_name, value)


def ensure_int(field_name: str, value: object) -> int:
    """Return an integer value or reject ambiguous numeric config input."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an int, got {type(value).__name__}")
    return value


def ensure_optional_int(field_name: str, value: object) -> int | None:
    """Return an optional integer value or reject ambiguous numeric config input."""
    if value is None:
        return None
    return ensure_int(field_name, value)


def ensure_finite_float(field_name: str, value: object) -> float:
    """Return a finite float value or reject unsafe numeric config input."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number, got {type(value).__name__}")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
    return numeric


def ensure_optional_finite_float(field_name: str, value: object) -> float | None:
    """Return an optional finite float value or reject unsafe numeric config input."""
    if value is None:
        return None
    return ensure_finite_float(field_name, value)


def ensure_screenshot_value(field_name: str, value: object) -> bool | str | None:
    """Return a screenshot option or reject unsupported untyped config input."""
    if value is None or isinstance(value, (bool, str)):
        return value
    raise ValueError(f"{field_name} must be a bool, string, or None, got {type(value).__name__}")


def validate_output_representations(value: list[str]) -> list[str]:
    """Validate requested document output representations."""
    if not isinstance(value, list):
        raise ValueError(
            "output_formats must be a non-empty list of supported format strings, "
            f"got {type(value).__name__}"
        )
    if not value:
        raise ValueError("output_formats must be a non-empty list of supported format strings")
    normalized: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(f"output_formats[{index}] must be a string, got {type(item).__name__}")
        if item not in VALID_OUTPUT_REPRESENTATIONS:
            raise ValueError(
                "Invalid output_formats entry "
                f"'{item}'. Must be one of: {', '.join(VALID_OUTPUT_REPRESENTATIONS)}"
            )
        normalized.append(item)
    return normalized


def validate_output_profile_name(value: str | None) -> str | None:
    """Validate a named output profile."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"output_profile must be a string or None, got {type(value).__name__}")
    if value not in VALID_OUTPUT_PROFILES:
        raise ValueError(
            f"Unknown output profile '{value}'. "
            f"Valid profiles: {', '.join(VALID_OUTPUT_PROFILES)}"
        )
    return value


def validate_optional_string_list(field_name: str, value: list[str] | None) -> list[str] | None:
    """Validate optional list[str] fields used by domain-specific rules."""
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list of strings, got {type(value).__name__}")
    normalized: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(f"{field_name}[{index}] must be a string, got {type(item).__name__}")
        normalized.append(item)
    return normalized


def validate_string_list(field_name: str, value: object) -> list[str]:
    """Validate required list[str] fields used by runtime configuration."""
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list of strings, got {type(value).__name__}")
    normalized: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(f"{field_name}[{index}] must be a string, got {type(item).__name__}")
        normalized.append(item)
    return normalized


def validate_regex_patterns(patterns: list[str]) -> None:
    """Validate user-provided regex patterns before runtime scanning."""
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"Invalid regex pattern '{pattern}': {exc}") from exc


def collect_init_values(
    cls: type[Any], args: tuple[Any, ...], kwargs: dict[str, Any]
) -> tuple[dict[str, Any], frozenset[str]]:
    """Resolve dataclass-style init arguments while preserving explicit keys."""
    init_fields = [config_field for config_field in fields(cls) if config_field.init]
    if len(args) > len(init_fields):
        raise TypeError(
            f"{cls.__name__}.__init__() takes at most {len(init_fields)} positional arguments "
            f"but {len(args)} were given"
        )

    remaining_kwargs = dict(kwargs)
    values: dict[str, Any] = {}
    explicit: set[str] = set()

    for index, config_field in enumerate(init_fields):
        if index < len(args):
            if config_field.name in remaining_kwargs:
                raise TypeError(
                    f"{cls.__name__}.__init__() got multiple values for argument "
                    f"{config_field.name!r}"
                )
            values[config_field.name] = args[index]
            explicit.add(config_field.name)
            continue

        if config_field.name in remaining_kwargs:
            values[config_field.name] = remaining_kwargs.pop(config_field.name)
            explicit.add(config_field.name)
            continue

        if config_field.default is not MISSING:
            values[config_field.name] = copy.deepcopy(config_field.default)
            continue

        if config_field.default_factory is not MISSING:
            values[config_field.name] = config_field.default_factory()
            continue

        raise TypeError(
            f"{cls.__name__}.__init__() missing required argument: '{config_field.name}'"
        )

    if remaining_kwargs:
        unexpected = next(iter(remaining_kwargs))
        raise TypeError(
            f"{cls.__name__}.__init__() got an unexpected keyword argument '{unexpected}'"
        )

    return values, frozenset(explicit)
