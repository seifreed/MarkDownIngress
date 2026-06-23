"""Validation helpers shared by security scanning components."""

from __future__ import annotations

import logging
import math

from markdown_ingress.config_validation import ensure_bool


def ensure_numeric_score_input(field_name: str, value: object) -> float:
    """Return a numeric score input without accepting bools as ints."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a number, got {type(value).__name__}")
    return float(value)


def ensure_severity_threshold(field_name: str, value: object) -> float:
    """Return a Nova severity threshold in [0.0, 1.0]."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            "Invalid severity thresholds: "
            f"{field_name} must be a finite number, got {type(value).__name__}"
        )
    threshold = float(value)
    if not math.isfinite(threshold):
        raise ValueError(
            f"Invalid severity thresholds: {field_name} must be a finite number, got {value!r}"
        )
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(
            f"Invalid severity thresholds: {field_name} must be between 0.0 and 1.0, "
            f"got {threshold}"
        )
    return threshold


def resolve_exception_fallback_score(
    value: object | None,
    *,
    default: float,
    minimum: float,
    logger: logging.Logger,
) -> float:
    """Normalize the fail-closed score used when Nova scan results are invalid."""
    if value is None:
        return default
    try:
        fallback_score = ensure_numeric_score_input("exception_fallback_score", value)
    except ValueError:
        logger.warning(
            "Invalid exception_fallback_score '%s', must be 0.0-1.0. Using default 0.75.",
            value,
        )
        return default

    if math.isnan(fallback_score) or not 0.0 <= fallback_score <= 1.0:
        logger.warning(
            "Invalid exception_fallback_score '%s', must be 0.0-1.0. Using default 0.75.",
            value,
        )
        return default
    if fallback_score < minimum:
        logger.warning(
            "exception_fallback_score '%s' is below safe minimum %.1f. "
            "Setting to %.1f to prevent security bypass.",
            fallback_score,
            minimum,
            minimum,
        )
        return minimum
    return fallback_score


def effective_security_thresholds(
    block_threshold: float = 0.7,
    warn_threshold: float = 0.4,
    *,
    strict: bool = False,
    logger: logging.Logger,
) -> tuple[float, float]:
    """Return policy thresholds after applying strict-mode tightening."""
    strict = ensure_bool("strict", strict)
    block_threshold = ensure_numeric_score_input("block_threshold", block_threshold)
    warn_threshold = ensure_numeric_score_input("warn_threshold", warn_threshold)
    if math.isnan(block_threshold) or not 0.0 <= block_threshold <= 1.0:
        if math.isnan(block_threshold):
            logger.warning("block_threshold is NaN, using safe default 1.0")
            block_threshold = 1.0
        else:
            logger.warning(
                "block_threshold %s out of range [0.0, 1.0], clamping",
                block_threshold,
            )
            block_threshold = max(0.0, min(1.0, block_threshold))
    if math.isnan(warn_threshold) or not 0.0 <= warn_threshold <= 1.0:
        if math.isnan(warn_threshold):
            logger.warning("warn_threshold is NaN, using safe default 0.5")
            warn_threshold = 0.5
        else:
            logger.warning(
                "warn_threshold %s out of range [0.0, 1.0], clamping",
                warn_threshold,
            )
            warn_threshold = max(0.0, min(1.0, warn_threshold))
    if strict:
        block_threshold = min(block_threshold, 0.5)
        warn_threshold = min(warn_threshold, 0.3)
    if warn_threshold >= block_threshold:
        if block_threshold > 0.0:
            adjusted = block_threshold - 0.01
            if adjusted < 0.0:
                adjusted = block_threshold * 0.9
            if adjusted < 0.0:
                adjusted = 0.0
            warn_threshold = adjusted
        else:
            warn_threshold = 0.0
    return block_threshold, warn_threshold
