"""Environment variable helpers for api_server configuration."""

from __future__ import annotations

import datetime
import logging
import math
import os
from dataclasses import dataclass

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class APIServerEnvConfig:
    """Parsed API server environment configuration."""

    job_ttl_seconds: int
    job_db_path: str
    job_workers: int
    max_queued_jobs: int
    webhook_max_retries: int
    webhook_retry_delay_seconds: float
    execution_timeout_seconds: float | None
    allow_local_webhooks: bool


def load_api_server_env_config() -> APIServerEnvConfig:
    """Build a typed API server configuration snapshot from environment values."""
    webhook_retry_delay = _read_optional_float_env(
        "MDI_API_WEBHOOK_RETRY_DELAY_SECONDS", minimum=0.0
    )
    return APIServerEnvConfig(
        job_ttl_seconds=_read_positive_int_env("MDI_API_JOB_TTL_SECONDS", 3600),
        job_db_path=os.getenv("MDI_API_JOB_DB_PATH", "artifacts/api_jobs/jobs.sqlite3"),
        job_workers=_read_positive_int_env("MDI_API_JOB_WORKERS", 2),
        max_queued_jobs=_read_positive_int_env("MDI_API_MAX_QUEUED_JOBS", 100),
        webhook_max_retries=_read_positive_int_env("MDI_API_WEBHOOK_MAX_RETRIES", 2),
        webhook_retry_delay_seconds=0.25 if webhook_retry_delay is None else webhook_retry_delay,
        execution_timeout_seconds=_read_optional_float_env(
            "MDI_API_JOB_TIMEOUT_SECONDS", minimum=0.0, exclusive_minimum=True
        ),
        allow_local_webhooks=_read_bool_env("MDI_API_ALLOW_LOCAL_WEBHOOKS", False),
    )


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
    if not math.isfinite(value):
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
