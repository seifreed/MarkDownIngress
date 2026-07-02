"""Numeric environment override validation."""

from __future__ import annotations

import math
from typing import Any

from markdown_ingress.core.config_env_restore import FieldRestoreContext, restore_field


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


def _restore_conflicting_chunk_fields(context: FieldRestoreContext) -> None:
    config = context.config
    changed_fields = [
        attr_name
        for attr_name in ("chunk_size", "chunk_overlap")
        if getattr(config, attr_name) != context.previous_values[attr_name]
    ]
    if not changed_fields:
        changed_fields = ["chunk_overlap"]
    for attr_name in changed_fields:
        _restore_numeric_field(
            context,
            attr_name,
            "chunk_overlap (%s) must be less than chunk_size (%s) after "
            "env overrides. Keeping previous %s value %r.",
            config.chunk_overlap,
            config.chunk_size,
            attr_name,
            context.previous_values[attr_name],
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
        _restore_conflicting_chunk_fields(context)


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
