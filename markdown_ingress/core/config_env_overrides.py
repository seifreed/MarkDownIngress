"""Environment override application for legacy Config instances."""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Collection
from dataclasses import dataclass, fields
from typing import Any

from markdown_ingress.config_models import (
    _validate_output_profile_name,
    _validate_output_representations,
)
from markdown_ingress.core.config_env import EnvVarMapping
from markdown_ingress.core.config_env import parse_csv_string_list as _parse_csv_string_list
from markdown_ingress.core.config_rules import (
    VALID_CACHE_TYPES,
    VALID_CHUNKING_STRATEGIES,
    VALID_MODES,
    VALID_OUTPUT_FORMATS,
    VALID_POLICIES,
)
from markdown_ingress.core.config_rules import (
    _validate_regex_patterns as _validate_regex_patterns,
)

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FieldRestoreContext:
    """State required to restore fields invalidated by environment overrides."""

    config: Any
    previous_values: dict[str, Any]
    previous_explicit: dict[str, bool]
    explicit: set[str]


def restore_field(
    context: FieldRestoreContext,
    attr_name: str,
    message: str,
    *args: object,
) -> None:
    """Revert a field to its pre-env-override value and update explicit tracking."""
    _logger.warning(message, *args)
    setattr(context.config, attr_name, context.previous_values[attr_name])
    if context.previous_explicit.get(attr_name, False):
        context.explicit.add(attr_name)
    else:
        context.explicit.discard(attr_name)


def _apply_env_mapping_overrides(
    config: Any,
    env_mapping: dict,
    explicit: set,
) -> None:
    for env_var, (attr_name, converter) in env_mapping.items():
        value = os.getenv(env_var)
        if value is None:
            continue
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


def _apply_custom_patterns_override(config: Any, explicit: set) -> None:
    custom_patterns_env = os.getenv("MDI_CUSTOM_PATTERNS")
    if not custom_patterns_env:
        return
    patterns = _parse_csv_string_list("custom_patterns", custom_patterns_env)
    if not patterns:
        return
    try:
        _validate_regex_patterns(patterns)
    except ValueError as e:
        _logger.warning(
            "Invalid value for custom_patterns (%s=%s): %s. Keeping previous value.",
            "MDI_CUSTOM_PATTERNS",
            custom_patterns_env,
            e,
        )
        return
    config.custom_patterns = patterns
    explicit.add("custom_patterns")


def _apply_csv_list_override(config: Any, explicit: set, env_var: str, attr_name: str) -> None:
    value = os.getenv(env_var)
    if value is None:
        return
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


def _apply_csv_list_overrides(config: Any, explicit: set) -> None:
    list_env_mapping: dict[str, str] = {
        "MDI_PLUGIN_DIRS": "plugin_dirs",
        "MDI_OUTPUT_FORMATS": "output_formats",
    }
    for env_var, attr_name in list_env_mapping.items():
        _apply_csv_list_override(config, explicit, env_var, attr_name)


def apply_scalar_list_and_custom_overrides(
    config: Any,
    env_mapping: dict,
    explicit: set,
) -> None:
    """Apply scalar, list, and custom-pattern env var overrides to config in-place."""
    _apply_env_mapping_overrides(config, env_mapping, explicit)
    _apply_custom_patterns_override(config, explicit)
    _apply_csv_list_overrides(config, explicit)


def _restore_invalid_choice(
    context: FieldRestoreContext,
    attr_name: str,
    valid_values: Collection[str],
) -> None:
    value = getattr(context.config, attr_name)
    if value in valid_values:
        return
    restore_field(
        context,
        attr_name,
        "Invalid %s '%s' from environment, valid values: %s. Keeping previous value %r.",
        attr_name,
        value,
        valid_values,
        context.previous_values[attr_name],
    )


def _validate_output_profile_field(context: FieldRestoreContext) -> None:
    try:
        context.config.output_profile = (
            _validate_output_profile_name(context.config.output_profile) or "default"
        )
    except ValueError as exc:
        restore_field(
            context,
            "output_profile",
            "Invalid output_profile '%s' from environment: %s. Keeping previous value %r.",
            context.config.output_profile,
            exc,
            context.previous_values["output_profile"],
        )


def _validate_minimum_env_field(
    context: FieldRestoreContext,
    attr_name: str,
    minimum: int,
    label: str,
) -> None:
    value = getattr(context.config, attr_name)
    if value >= minimum:
        return
    restore_field(
        context,
        attr_name,
        "Invalid %s %r from environment, must be >= %s. Keeping previous value %r.",
        label,
        value,
        minimum,
        context.previous_values[attr_name],
    )


def _validate_render_cost_budget_field(context: FieldRestoreContext) -> None:
    if context.config.render_cost_budget is None or context.config.render_cost_budget >= 1:
        return
    restore_field(
        context,
        "render_cost_budget",
        "Invalid render_cost_budget %r from environment, must be >= 1. "
        "Keeping previous value %r.",
        context.config.render_cost_budget,
        context.previous_values["render_cost_budget"],
    )


def _validate_output_formats_field(context: FieldRestoreContext) -> None:
    if not context.config.output_formats:
        restore_field(
            context,
            "output_formats",
            "Invalid output_formats from environment, list cannot be empty. "
            "Keeping previous value %r.",
            context.previous_values["output_formats"],
        )
        return
    try:
        context.config.output_formats = _validate_output_representations(
            context.config.output_formats
        )
    except (ValueError, TypeError) as exc:
        restore_field(
            context,
            "output_formats",
            "Invalid output_formats from environment: %s. Keeping previous value %r.",
            exc,
            context.previous_values["output_formats"],
        )


def validate_categorical_fields(
    config: Any,
    previous_values: dict,
    previous_explicit: dict,
    explicit: set,
) -> None:
    """Validate and restore categorical/enum fields set by environment variables."""
    context = FieldRestoreContext(config, previous_values, previous_explicit, explicit)
    _restore_invalid_choice(context, "mode", VALID_MODES)
    _restore_invalid_choice(context, "cache_type", VALID_CACHE_TYPES)
    _restore_invalid_choice(context, "output_format", VALID_OUTPUT_FORMATS)
    _validate_output_profile_field(context)
    _validate_minimum_env_field(
        context,
        "auto_render_threshold",
        1,
        "auto_render_threshold",
    )
    _validate_render_cost_budget_field(context)
    _validate_output_formats_field(context)
    _restore_invalid_choice(context, "chunking_strategy", VALID_CHUNKING_STRATEGIES)
    _restore_invalid_choice(context, "policy", VALID_POLICIES)


def _restore_numeric_field(
    context: FieldRestoreContext,
    attr_name: str,
    message: str,
    *args: object,
) -> None:
    restore_field(context, attr_name, message, *args)


def _validate_timeout_field(context: FieldRestoreContext) -> None:
    config = context.config
    if config.timeout <= 0 or config.timeout > 3600:
        _restore_numeric_field(
            context,
            "timeout",
            "Invalid timeout '%s' from environment, must be > 0 and <= 3600. "
            "Keeping previous value %r.",
            config.timeout,
            context.previous_values["timeout"],
        )
    elif not math.isfinite(config.timeout):
        _restore_numeric_field(
            context,
            "timeout",
            "Invalid timeout '%s' from environment, must be finite. Keeping previous value %r.",
            config.timeout,
            context.previous_values["timeout"],
        )


def _validate_chunk_fields(context: FieldRestoreContext) -> None:
    config = context.config
    if config.chunk_size < 100 or config.chunk_size > 50000:
        _restore_numeric_field(
            context,
            "chunk_size",
            "Invalid chunk_size '%s' from environment, must be 100-50000. "
            "Keeping previous value %r.",
            config.chunk_size,
            context.previous_values["chunk_size"],
        )
    if config.chunk_overlap < 0 or config.chunk_overlap > 10000:
        _restore_numeric_field(
            context,
            "chunk_overlap",
            "Invalid chunk_overlap '%s' from environment, must be 0-10000. "
            "Keeping previous value %r.",
            config.chunk_overlap,
            context.previous_values["chunk_overlap"],
        )
    if config.chunk_overlap >= config.chunk_size:
        _restore_numeric_field(
            context,
            "chunk_overlap",
            "chunk_overlap (%s) must be less than chunk_size (%s) after "
            "env overrides. Keeping previous value %r.",
            config.chunk_overlap,
            config.chunk_size,
            context.previous_values["chunk_overlap"],
        )


def _validate_domain_request_interval_field(context: FieldRestoreContext) -> None:
    config = context.config
    if config.domain_request_interval < 0.0:
        _restore_numeric_field(
            context,
            "domain_request_interval",
            "Invalid domain_request_interval '%s' from environment, must be "
            ">= 0.0. Keeping previous value %r.",
            config.domain_request_interval,
            context.previous_values["domain_request_interval"],
        )
    elif not math.isfinite(config.domain_request_interval):
        _restore_numeric_field(
            context,
            "domain_request_interval",
            "Invalid domain_request_interval '%s' from environment, must be "
            "finite. Keeping previous value %r.",
            config.domain_request_interval,
            context.previous_values["domain_request_interval"],
        )


def _validate_circuit_breaker_fields(context: FieldRestoreContext) -> None:
    config = context.config
    if config.circuit_breaker_threshold < 1:
        _restore_numeric_field(
            context,
            "circuit_breaker_threshold",
            "Invalid circuit_breaker_threshold '%s' from environment, must be "
            ">= 1. Keeping previous value %r.",
            config.circuit_breaker_threshold,
            context.previous_values["circuit_breaker_threshold"],
        )
    if config.circuit_breaker_open_seconds <= 0.0:
        _restore_numeric_field(
            context,
            "circuit_breaker_open_seconds",
            "Invalid circuit_breaker_open_seconds '%s' from environment, must "
            "be > 0.0. Keeping previous value %r.",
            config.circuit_breaker_open_seconds,
            context.previous_values["circuit_breaker_open_seconds"],
        )
    elif not math.isfinite(config.circuit_breaker_open_seconds):
        _restore_numeric_field(
            context,
            "circuit_breaker_open_seconds",
            "Invalid circuit_breaker_open_seconds '%s' from environment, must "
            "be finite. Keeping previous value %r.",
            config.circuit_breaker_open_seconds,
            context.previous_values["circuit_breaker_open_seconds"],
        )


def _validate_cache_and_batch_fields(context: FieldRestoreContext) -> None:
    config = context.config
    if config.cache_ttl <= 0:
        _restore_numeric_field(
            context,
            "cache_ttl",
            "Invalid cache_ttl '%s' from environment, must be > 0. Keeping previous value %r.",
            config.cache_ttl,
            context.previous_values["cache_ttl"],
        )
    if config.batch_max_concurrent < 1:
        _restore_numeric_field(
            context,
            "batch_max_concurrent",
            "Invalid batch_max_concurrent '%s' from environment, must be >= 1. "
            "Keeping previous value %r.",
            config.batch_max_concurrent,
            context.previous_values["batch_max_concurrent"],
        )
    if config.batch_timeout <= 0.0:
        _restore_numeric_field(
            context,
            "batch_timeout",
            "Invalid batch_timeout '%s' from environment, must be > 0.0. "
            "Keeping previous value %r.",
            config.batch_timeout,
            context.previous_values["batch_timeout"],
        )
    elif not math.isfinite(config.batch_timeout):
        _restore_numeric_field(
            context,
            "batch_timeout",
            "Invalid batch_timeout '%s' from environment, must be finite. "
            "Keeping previous value %r.",
            config.batch_timeout,
            context.previous_values["batch_timeout"],
        )


def validate_numeric_fields(
    config: Any,
    previous_values: dict,
    previous_explicit: dict,
    explicit: set,
) -> None:
    """Validate and restore numeric fields that fall outside their allowed bounds."""
    context = FieldRestoreContext(config, previous_values, previous_explicit, explicit)
    _validate_timeout_field(context)
    _validate_chunk_fields(context)
    _validate_domain_request_interval_field(context)
    _validate_circuit_breaker_fields(context)
    _validate_cache_and_batch_fields(context)


def apply_env_overrides(config: Any, env_mapping: EnvVarMapping) -> Any:
    """Apply environment variable overrides to a Config-like object."""
    explicit = set(config.explicit_keys())
    previous_explicit = {
        config_field.name: config_field.name in explicit
        for config_field in fields(type(config))
        if not config_field.name.startswith("_")
    }
    env_policy = os.getenv("MDI_POLICY")
    env_policy_name = os.getenv("MDI_POLICY_NAME")
    if env_policy is not None and env_policy_name is not None and env_policy != env_policy_name:
        raise ValueError(
            "Environment cannot define both MDI_POLICY and MDI_POLICY_NAME " "with different values"
        )
    previous_values = {
        attr_name: getattr(config, attr_name) for _, (attr_name, _) in env_mapping.items()
    }
    apply_scalar_list_and_custom_overrides(config, env_mapping, explicit)
    validate_categorical_fields(config, previous_values, previous_explicit, explicit)
    validate_numeric_fields(config, previous_values, previous_explicit, explicit)
    config._explicit_keys = frozenset(explicit)
    return config
