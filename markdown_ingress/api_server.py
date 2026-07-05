"""
FastAPI server for MarkDownIngress.
"""

from __future__ import annotations

import logging
import threading

from fastapi import Depends, FastAPI

import markdown_ingress.api_server_queue_runtime_hooks as _job_queue_runtime
from markdown_ingress.adapters.jobs.sqlite_job_queue import (
    PersistentJobQueue,
)
from markdown_ingress.api import (
    compare_extractors,
    generate_security_report,
    ingest,
    ingest_many,
    retry_ingest,
)
from markdown_ingress.api_server_auth import (
    RATE_LIMIT_REQUESTS,
    RATE_LIMIT_WINDOW_SECONDS,
    _check_rate_limit_redis,
)
from markdown_ingress.api_server_dependencies import (
    _rate_limit_client_id as _rate_limit_client_id,
)
from markdown_ingress.api_server_dependencies import (
    _require_api_key,
    _require_rate_limit,
)
from markdown_ingress.api_server_env import (
    APIServerAuthEnvConfig,
    _detect_multiworker_environment,
    load_api_server_auth_config,
    load_api_server_env_config,
    load_api_server_listen_config,
    load_api_server_rate_limit_config,
)
from markdown_ingress.api_server_handlers import (
    handle_batch_status,
    handle_batch_submit,
    handle_extractor_comparison,
    handle_ingest,
    handle_retry_ingest,
    handle_security_report,
    handle_sync_batch,
)
from markdown_ingress.api_server_legacy_routes import LegacyRouteHandlers, register_legacy_routes
from markdown_ingress.api_server_queue import _find_job_record_in_queues
from markdown_ingress.api_server_rate_limit import RequestWindow
from markdown_ingress.api_server_rate_limit_runtime import _check_rate_limit_runtime
from markdown_ingress.api_server_responses import (
    build_detailed_health_payload,
    build_health_payload,
    build_root_payload,
    build_stats_payload,
)
from markdown_ingress.api_server_routes import ApiRouteProviders, register_api_routes
from markdown_ingress.api_server_support import validate_batch_request_ssrf_async
from markdown_ingress.core.orchestrator import get_ingest_stats

_logger = logging.getLogger(__name__)

API_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# API auth configuration (kept here so monkeypatch via api_server.* works)
# ---------------------------------------------------------------------------
_API_SERVER_AUTH_CONFIG: APIServerAuthEnvConfig = load_api_server_auth_config()
API_KEY_CONFIG_ERROR: bool = _API_SERVER_AUTH_CONFIG.api_key_config_error
OPTIONAL_API_KEY: str | None = _API_SERVER_AUTH_CONFIG.optional_api_key
TRUSTED_PROXY_IPS: frozenset[str] = _API_SERVER_AUTH_CONFIG.trusted_proxy_ips

# ---------------------------------------------------------------------------
# Rate limiting state (kept here so monkeypatch via api_server.* works)
# ---------------------------------------------------------------------------
_RATE_LIMIT_BACKEND: str = load_api_server_rate_limit_config().backend
_request_counts: dict[str, RequestWindow] = {}


_rate_limit_lock = threading.Lock()
_rate_limit_cleanup_counter: int = 0
_RATE_LIMIT_CLEANUP_THRESHOLD: int = 1000
_RATE_LIMIT_MAX_CLIENTS: int = 10000

if _detect_multiworker_environment():
    _logger.warning(
        "Rate limiting is per-worker in multi-worker deployments. "
        "Each worker process maintains separate rate limit state. "
        "Consider using Redis-backed rate limiting for production deployments."
    )


def _check_rate_limit(client_id: str) -> tuple[bool, int]:
    """Check if client is within rate limit.

    Args:
        client_id: Client identifier (typically IP address or API key hash)

    Returns:
        Tuple of (is_allowed, retry_after_seconds)
    """
    if _RATE_LIMIT_BACKEND == "redis":
        return _check_rate_limit_redis(client_id)

    global _rate_limit_cleanup_counter
    allowed, retry_after, _rate_limit_cleanup_counter = _check_rate_limit_runtime(
        client_id=client_id,
        request_counts=_request_counts,
        lock=_rate_limit_lock,
        cleanup_counter=_rate_limit_cleanup_counter,
        cleanup_threshold=_RATE_LIMIT_CLEANUP_THRESHOLD,
        max_clients=_RATE_LIMIT_MAX_CLIENTS,
        rate_limit_requests=RATE_LIMIT_REQUESTS,
        rate_limit_window_seconds=RATE_LIMIT_WINDOW_SECONDS,
        backend=_RATE_LIMIT_BACKEND,
        check_rate_limit_redis=_check_rate_limit_redis,
    )
    return allowed, retry_after


_API_SERVER_ENV_CONFIG = load_api_server_env_config()
JOB_TTL_SECONDS = _API_SERVER_ENV_CONFIG.job_ttl_seconds
JOB_DB_PATH = _API_SERVER_ENV_CONFIG.job_db_path
JOB_WORKERS = _API_SERVER_ENV_CONFIG.job_workers
MAX_QUEUED_JOBS = _API_SERVER_ENV_CONFIG.max_queued_jobs
JOB_WEBHOOK_MAX_RETRIES = _API_SERVER_ENV_CONFIG.webhook_max_retries
JOB_WEBHOOK_RETRY_DELAY_SECONDS = _API_SERVER_ENV_CONFIG.webhook_retry_delay_seconds
JOB_EXECUTION_TIMEOUT_SECONDS = _API_SERVER_ENV_CONFIG.execution_timeout_seconds
ALLOW_LOCAL_WEBHOOKS = _API_SERVER_ENV_CONFIG.allow_local_webhooks

app = FastAPI(
    title="MarkDownIngress API",
    description="Deterministic Web → Markdown Engine for LLM Pipelines",
    version=API_VERSION,
)


_JOB_QUEUE_LOCK = threading.RLock()
_JOB_QUEUE_INIT_LOCK = threading.Lock()  # Protects lazy job queue initialization
_JOB_QUEUE_BUILD_LOCK = threading.Lock()
_PREVIOUS_JOB_QUEUE_REPAIR_THREAD: threading.Thread | None = None
_PREVIOUS_JOB_QUEUE_REPAIR_STOP: threading.Event | None = None
_PREVIOUS_JOB_QUEUE_WATCHDOG_THREAD: threading.Thread | None = None
_PREVIOUS_JOB_QUEUE_WATCHDOG_STOP: threading.Event | None = None
_JOB_QUEUE_REPAIR_THREAD: threading.Thread | None = None
_JOB_QUEUE_REPAIR_STOP: threading.Event | None = None
_JOB_QUEUE_WATCHDOG_THREAD: threading.Thread | None = None
_JOB_QUEUE_WATCHDOG_STOP: threading.Event | None = None
_JOB_QUEUE_HISTORY: list[PersistentJobQueue] = []

_EXTERNAL_OWNER_REPAIR_RETRY_SECONDS = 5.0
_BACKEND_ERROR_REPAIR_RETRY_SECONDS = 5.0

# Compatibility exports for tests and monkeypatching hooks.
LEGACY_UNKNOWN_TTL_SECONDS = _job_queue_runtime.LEGACY_UNKNOWN_TTL_SECONDS
_RECOVERABLE_QUEUE_STATES = _job_queue_runtime._RECOVERABLE_QUEUE_STATES
_REPAIRABLE_QUEUE_STATES = _job_queue_runtime._REPAIRABLE_QUEUE_STATES
_legacy_unknown_ttl_seconds = _job_queue_runtime._legacy_unknown_ttl_seconds
_external_owner_job_queue_impl = _job_queue_runtime._external_owner_job_queue_impl
_close_queue_for_repair_impl = _job_queue_runtime._close_queue_for_repair_impl
_close_queue_for_repair = _job_queue_runtime._close_queue_for_repair
_ExternalOwnerJobQueue = _job_queue_runtime._ExternalOwnerJobQueue

_build_job_queue = _job_queue_runtime._build_job_queue
_remember_job_queue = _job_queue_runtime._remember_job_queue
_prune_job_queue_history = _job_queue_runtime._prune_job_queue_history
_replace_job_queue_if_current = _job_queue_runtime._replace_job_queue_if_current
_promote_external_owner_queue = _job_queue_runtime._promote_external_owner_queue
_current_queue_if_expected_changed = _job_queue_runtime._current_queue_if_expected_changed
_queue_if_expected_state = _job_queue_runtime._queue_if_expected_state
_replacement_for_runtime_build_error = _job_queue_runtime._replacement_for_runtime_build_error
_current_queue_after_superseded_replacement = (
    _job_queue_runtime._current_queue_after_superseded_replacement
)
_build_replacement_queue_or_current = _job_queue_runtime._build_replacement_queue_or_current
_external_owner_backend_still_owned = _job_queue_runtime._external_owner_backend_still_owned
_clear_job_queue_repair_state_locked = _job_queue_runtime._clear_job_queue_repair_state_locked
_clear_job_queue_repair_state = _job_queue_runtime._clear_job_queue_repair_state
_current_recoverable_job_queue = _job_queue_runtime._current_recoverable_job_queue
_wait_for_next_job_queue_repair_attempt = _job_queue_runtime._wait_for_next_job_queue_repair_attempt
_maybe_wait_for_external_owner_backend = _job_queue_runtime._maybe_wait_for_external_owner_backend
_finish_repair_if_replaced_or_terminal = _job_queue_runtime._finish_repair_if_replaced_or_terminal
_run_job_queue_repair_attempt = _job_queue_runtime._run_job_queue_repair_attempt
_job_queue_repair_loop = _job_queue_runtime._job_queue_repair_loop
_start_job_queue_repair_loop = _job_queue_runtime._start_job_queue_repair_loop
_maybe_start_job_queue_repair = _job_queue_runtime._maybe_start_job_queue_repair
_job_queue_watchdog_tick = _job_queue_runtime._job_queue_watchdog_tick
_start_job_queue_watchdog = _job_queue_runtime._start_job_queue_watchdog
_stop_reloaded_job_queue_control_threads = (
    _job_queue_runtime._stop_reloaded_job_queue_control_threads
)
_reset_job_queue_control_thread_refs = _job_queue_runtime._reset_job_queue_control_thread_refs
_fallback_queue_for_init_build_error = _job_queue_runtime._fallback_queue_for_init_build_error
_init_job_queue = _job_queue_runtime._init_job_queue
JOB_QUEUE: PersistentJobQueue | None = None
_job_queue_initialized = False
_job_queue_init_failed_at: float | None = None
_JOB_QUEUE_RETRY_BACKOFF_SECONDS = 10.0

_job_queue_init_backoff_active = _job_queue_runtime._job_queue_init_backoff_active
_ensure_job_queue_initialized = _job_queue_runtime._ensure_job_queue_initialized
_select_job_queue_for_use = _job_queue_runtime._select_job_queue_for_use
_current_queue_after_repair_close_failure = (
    _job_queue_runtime._current_queue_after_repair_close_failure
)
_get_job_queue = _job_queue_runtime._get_job_queue
_snapshot_job_queue_state = _job_queue_runtime._snapshot_job_queue_state
_snapshot_job_subsystem = _job_queue_runtime._snapshot_job_subsystem


def _get_job_record(job_id: str):
    queue = _get_job_queue()
    snapshot = _snapshot_job_queue_state()
    history = snapshot.history
    return _find_job_record_in_queues(job_id, queue, history)


def _build_route_providers() -> ApiRouteProviders:
    return ApiRouteProviders(
        api_version=lambda: API_VERSION,
        ingest=lambda: ingest,
        retry_ingest=lambda: retry_ingest,
        ingest_many=lambda: ingest_many,
        generate_security_report=lambda: generate_security_report,
        get_ingest_stats=lambda: get_ingest_stats,
        get_job_queue=lambda: _get_job_queue(),
        get_job_record=lambda: _get_job_record,
        get_job_ttl_seconds=lambda: JOB_TTL_SECONDS,
        snapshot_job_subsystem=lambda: _snapshot_job_subsystem,
        compare_extractors=lambda: compare_extractors,
        validate_batch_request_ssrf_async=lambda: validate_batch_request_ssrf_async,
        handle_ingest=lambda: handle_ingest,
        handle_retry_ingest=lambda: handle_retry_ingest,
        handle_sync_batch=lambda: handle_sync_batch,
        handle_batch_submit=lambda: handle_batch_submit,
        handle_batch_status=lambda: handle_batch_status,
        handle_security_report=lambda: handle_security_report,
        handle_extractor_comparison=lambda: handle_extractor_comparison,
        build_stats_payload=lambda: build_stats_payload,
        build_health_payload=lambda: build_health_payload,
        build_detailed_health_payload=lambda: build_detailed_health_payload,
        build_root_payload=lambda: build_root_payload,
    )


_api_routes = register_api_routes(
    app,
    require_api_key=_require_api_key,
    require_rate_limit=_require_rate_limit,
    providers=_build_route_providers(),
)
ingest_endpoint = _api_routes.ingest_endpoint
retry_ingest_endpoint = _api_routes.retry_ingest_endpoint
batch_ingest_endpoint = _api_routes.batch_ingest_endpoint
batch_job_submit = _api_routes.batch_job_submit
batch_job_status = _api_routes.batch_job_status
security_report_endpoint = _api_routes.security_report_endpoint
extractor_comparison_endpoint = _api_routes.extractor_comparison_endpoint
stats_endpoint = _api_routes.stats_endpoint
health = _api_routes.health
health_detailed = _api_routes.health_detailed
root = _api_routes.root


register_legacy_routes(
    app,
    [Depends(_require_api_key), Depends(_require_rate_limit)],
    LegacyRouteHandlers(
        ingest_endpoint=ingest_endpoint,
        retry_ingest_endpoint=retry_ingest_endpoint,
        batch_ingest_endpoint=batch_ingest_endpoint,
        security_report_endpoint=security_report_endpoint,
        extractor_comparison_endpoint=extractor_comparison_endpoint,
        health_endpoint=health,
    ),
)


def main():
    """Run the server."""
    import uvicorn

    host, port = load_api_server_listen_config()
    uvicorn.run("markdown_ingress.api_server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
