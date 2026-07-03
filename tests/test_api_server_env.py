"""Unit tests for API server environment configuration loading."""

from __future__ import annotations

from _pytest.monkeypatch import MonkeyPatch

from markdown_ingress.api_server_env import (
    APIRateLimitEnvConfig,
    APIServerEnvConfig,
    APIServerModelValidationConfig,
    load_api_server_env_config,
    load_api_server_listen_config,
    load_api_server_model_validation_config,
    load_api_server_rate_limit_config,
)


def test_load_api_server_env_config_uses_defaults(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("MDI_API_JOB_TTL_SECONDS", raising=False)
    monkeypatch.delenv("MDI_API_JOB_DB_PATH", raising=False)
    monkeypatch.delenv("MDI_API_JOB_WORKERS", raising=False)
    monkeypatch.delenv("MDI_API_MAX_QUEUED_JOBS", raising=False)
    monkeypatch.delenv("MDI_API_WEBHOOK_MAX_RETRIES", raising=False)
    monkeypatch.delenv("MDI_API_WEBHOOK_RETRY_DELAY_SECONDS", raising=False)
    monkeypatch.delenv("MDI_API_JOB_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("MDI_API_ALLOW_LOCAL_WEBHOOKS", raising=False)

    config = load_api_server_env_config()

    assert isinstance(config, APIServerEnvConfig)
    assert config.job_ttl_seconds == 3600
    assert config.job_db_path == "artifacts/api_jobs/jobs.sqlite3"
    assert config.job_workers == 2
    assert config.max_queued_jobs == 100
    assert config.webhook_max_retries == 2
    assert config.webhook_retry_delay_seconds == 0.25
    assert config.execution_timeout_seconds is None
    assert config.allow_local_webhooks is False


def test_load_api_server_env_config_parses_overrides_and_invalid_values(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("MDI_API_JOB_TTL_SECONDS", "120")
    monkeypatch.setenv("MDI_API_JOB_DB_PATH", "/tmp/custom-jobs.db")
    monkeypatch.setenv("MDI_API_JOB_WORKERS", "4")
    monkeypatch.setenv("MDI_API_MAX_QUEUED_JOBS", "200")
    monkeypatch.setenv("MDI_API_WEBHOOK_MAX_RETRIES", "3")
    monkeypatch.setenv("MDI_API_WEBHOOK_RETRY_DELAY_SECONDS", "0.75")
    monkeypatch.setenv("MDI_API_JOB_TIMEOUT_SECONDS", "15.5")
    monkeypatch.setenv("MDI_API_ALLOW_LOCAL_WEBHOOKS", "true")
    monkeypatch.setenv("MDI_API_JOB_WORKERS", "invalid")
    monkeypatch.setenv("MDI_API_WEBHOOK_MAX_RETRIES", "not-a-number")

    config = load_api_server_env_config()

    assert config.job_ttl_seconds == 120
    assert config.job_db_path == "/tmp/custom-jobs.db"
    assert config.job_workers == 2  # invalid value falls back to default
    assert config.max_queued_jobs == 200
    assert config.webhook_max_retries == 2  # invalid value falls back to default
    assert config.webhook_retry_delay_seconds == 0.75
    assert config.execution_timeout_seconds == 15.5
    assert config.allow_local_webhooks is True


def test_load_api_server_rate_limit_config_uses_defaults(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("MDI_API_RATE_LIMIT_REQUESTS", raising=False)
    monkeypatch.delenv("MDI_API_RATE_LIMIT_WINDOW", raising=False)
    monkeypatch.delenv("MDI_RATE_LIMIT_BACKEND", raising=False)
    monkeypatch.delenv("MDI_RATE_LIMIT_REDIS_URL", raising=False)
    monkeypatch.delenv("MDI_RATE_LIMIT_REDIS_PREFIX", raising=False)

    config = load_api_server_rate_limit_config()

    assert isinstance(config, APIRateLimitEnvConfig)
    assert config.requests == 100
    assert config.window_seconds == 60
    assert config.backend == "memory"
    assert config.redis_url == "redis://localhost:6379/0"
    assert config.redis_prefix == "mdi:rl:"


def test_load_api_server_rate_limit_config_parses_overrides(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("MDI_API_RATE_LIMIT_REQUESTS", "250")
    monkeypatch.setenv("MDI_API_RATE_LIMIT_WINDOW", "120")
    monkeypatch.setenv("MDI_RATE_LIMIT_BACKEND", "redis")
    monkeypatch.setenv("MDI_RATE_LIMIT_REDIS_URL", "redis://custom")
    monkeypatch.setenv("MDI_RATE_LIMIT_REDIS_PREFIX", "custom:rl:")

    config = load_api_server_rate_limit_config()

    assert config.requests == 250
    assert config.window_seconds == 120
    assert config.backend == "redis"
    assert config.redis_url == "redis://custom"
    assert config.redis_prefix == "custom:rl:"


def test_load_api_server_model_validation_config_isolation_by_env(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("MDI_API_MAX_BATCH_URLS", "7")
    monkeypatch.setenv("MDI_API_MAX_TIMEOUT", "42")
    monkeypatch.setenv("MDI_API_MAX_CHUNK_SIZE", "321")
    monkeypatch.setenv("MDI_API_MAX_CUSTOM_PATTERNS", "13")
    monkeypatch.setenv("MDI_API_MAX_DOMAIN_POLICIES", "17")

    config = load_api_server_model_validation_config()

    assert isinstance(config, APIServerModelValidationConfig)
    assert config.max_batch_urls == 7
    assert config.max_timeout_seconds == 42
    assert config.max_chunk_size == 321
    assert config.max_custom_patterns == 13
    assert config.max_domain_policies == 17


def test_load_api_server_listen_config_defaults(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("MDI_HOST", raising=False)
    monkeypatch.delenv("MDI_PORT", raising=False)

    host, port = load_api_server_listen_config()

    assert host == "127.0.0.1"
    assert port == 8000


def test_load_api_server_listen_config_reads_invalid_port_as_default(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("MDI_HOST", "127.0.0.42")
    monkeypatch.setenv("MDI_PORT", "not-a-port")

    host, port = load_api_server_listen_config()

    assert host == "127.0.0.42"
    assert port == 8000
