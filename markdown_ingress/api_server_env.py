"""Environment variable helpers for api_server configuration."""

from __future__ import annotations

import datetime
import logging
import os

_logger = logging.getLogger(__name__)


def _read_positive_int_env(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        _logger.warning("Invalid integer for %s=%r. Using default %d.", name, raw, default)
        return default
    if value < minimum:
        _logger.warning(
            "Invalid value for %s=%r. Minimum is %d. Using default %d.", name, raw, minimum, default
        )
        return default
    return value


def _read_bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    _logger.warning("Invalid boolean for %s=%r. Using default %s.", name, raw, default)
    return default


def _read_optional_float_env(
    name: str, *, minimum: float = 0.0, exclusive_minimum: bool = False
) -> float | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        value = float(raw)
    except ValueError:
        _logger.warning("Invalid float for %s=%r. Disabling optional setting.", name, raw)
        return None
    is_invalid = value < minimum or (exclusive_minimum and value == minimum)
    if is_invalid:
        comparator = ">" if exclusive_minimum else ">="
        _logger.warning(
            "Invalid value for %s=%r. Expected %s %s. Disabling optional setting.",
            name,
            raw,
            comparator,
            minimum,
        )
        return None
    return value


def _parse_iso_datetime_utc(value: str) -> datetime.datetime | None:
    """Parse an ISO timestamp and normalize naive values to UTC.

    Legacy rows in the job database may omit timezone information. For retention
    and lease comparisons we interpret those values as UTC instead of crashing on
    aware/naive comparisons.
    """
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime.UTC)
    return parsed.astimezone(datetime.UTC)


def _detect_multiworker_environment() -> bool:
    """Detect if running in a multi-worker deployment environment.

    Returns:
        True if multiple worker processes are configured, False otherwise.
    """
    # Gunicorn and Uvicorn use worker-count environment variables.
    # Invalid values should degrade gracefully instead of crashing import.
    for env_name in ("GUNICORN_WORKERS", "UVICORN_WORKERS"):
        raw = os.environ.get(env_name)
        if not raw:
            continue
        try:
            if int(raw) > 1:
                return True
        except ValueError:
            _logger.warning(
                "Invalid integer for %s=%r. Assuming single-worker deployment.", env_name, raw
            )
    return False
