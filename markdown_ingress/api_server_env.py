"""Environment variable helpers for api_server configuration."""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass

from markdown_ingress.core.config_env import (
    read_bool_env,
    read_env,
    read_optional_float_env,
    read_positive_int_env,
)

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


@dataclass(frozen=True)
class APIRateLimitEnvConfig:
    """Parsed API rate-limit environment configuration."""

    requests: int
    window_seconds: int
    backend: str
    redis_url: str
    redis_prefix: str


@dataclass(frozen=True)
class APIServerModelValidationConfig:
    """Parsed API request-model validation limits."""

    max_batch_urls: int
    max_timeout_seconds: int
    max_chunk_size: int
    max_custom_patterns: int
    max_domain_policies: int


@dataclass(frozen=True)
class APIServerAuthEnvConfig:
    """Parsed API authentication and trusted proxy configuration."""

    optional_api_key: str | None
    api_key_config_error: bool
    trusted_proxy_ips: frozenset[str]


def load_api_server_env_config() -> APIServerEnvConfig:
    """Build a typed API server configuration snapshot from environment values."""
    webhook_retry_delay = read_optional_float_env(
        "MDI_API_WEBHOOK_RETRY_DELAY_SECONDS", minimum=0.0
    )
    return APIServerEnvConfig(
        job_ttl_seconds=read_positive_int_env("MDI_API_JOB_TTL_SECONDS", 3600),
        job_db_path=read_env("MDI_API_JOB_DB_PATH") or "artifacts/api_jobs/jobs.sqlite3",
        job_workers=read_positive_int_env("MDI_API_JOB_WORKERS", 2),
        max_queued_jobs=read_positive_int_env("MDI_API_MAX_QUEUED_JOBS", 100),
        webhook_max_retries=read_positive_int_env("MDI_API_WEBHOOK_MAX_RETRIES", 2),
        webhook_retry_delay_seconds=0.25 if webhook_retry_delay is None else webhook_retry_delay,
        execution_timeout_seconds=read_optional_float_env(
            "MDI_API_JOB_TIMEOUT_SECONDS", minimum=0.0, exclusive_minimum=True
        ),
        allow_local_webhooks=read_bool_env("MDI_API_ALLOW_LOCAL_WEBHOOKS", False),
    )


def load_api_server_rate_limit_config() -> APIRateLimitEnvConfig:
    """Build a typed rate-limit configuration snapshot from environment values."""
    return APIRateLimitEnvConfig(
        requests=read_positive_int_env("MDI_API_RATE_LIMIT_REQUESTS", 100),
        window_seconds=read_positive_int_env("MDI_API_RATE_LIMIT_WINDOW", 60),
        backend=(read_env("MDI_RATE_LIMIT_BACKEND") or "memory").strip().lower(),
        redis_url=read_env("MDI_RATE_LIMIT_REDIS_URL") or "redis://localhost:6379/0",
        redis_prefix=read_env("MDI_RATE_LIMIT_REDIS_PREFIX") or "mdi:rl:",
    )


def load_api_server_auth_config() -> APIServerAuthEnvConfig:
    """Build a typed API auth/trusted-proxy configuration snapshot from environment values."""
    raw_api_key = read_env("MDI_API_KEY")
    api_key_config_error = raw_api_key is not None and raw_api_key.strip() == ""
    trusted_proxy_ips = frozenset(_parse_csv_set(read_env("MDI_TRUSTED_PROXY_IPS") or ""))
    return APIServerAuthEnvConfig(
        optional_api_key=None if api_key_config_error else raw_api_key,
        api_key_config_error=api_key_config_error,
        trusted_proxy_ips=trusted_proxy_ips,
    )


def load_api_server_model_validation_config() -> APIServerModelValidationConfig:
    """Build typed validation limit configuration for API request models."""
    return APIServerModelValidationConfig(
        max_batch_urls=read_positive_int_env("MDI_API_MAX_BATCH_URLS", 100),
        max_timeout_seconds=read_positive_int_env("MDI_API_MAX_TIMEOUT", 300),
        max_chunk_size=read_positive_int_env("MDI_API_MAX_CHUNK_SIZE", 20000),
        max_custom_patterns=read_positive_int_env("MDI_API_MAX_CUSTOM_PATTERNS", 1000),
        max_domain_policies=read_positive_int_env("MDI_API_MAX_DOMAIN_POLICIES", 1000),
    )


def load_api_server_listen_config() -> tuple[str, int]:
    """Build host/port configuration for API server startup."""
    host = read_env("MDI_HOST") or "127.0.0.1"
    port = read_positive_int_env("MDI_PORT", 8000)
    return host, port


def _parse_csv_set(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


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
        raw = read_env(env_name)
        if not raw:
            continue
        try:
            if int(raw) > 1:
                return True
        except ValueError:
            _logger.warning(
                "Invalid integer for %s=%r. Assuming single-worker deployment.",
                env_name,
                raw,
            )
    return False
