"""Unit tests for API server environment configuration loading."""

from __future__ import annotations

from _pytest.monkeypatch import MonkeyPatch

from markdown_ingress.api_server_env import APIServerEnvConfig, load_api_server_env_config


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
