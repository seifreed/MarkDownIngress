"""Shared fetcher lifecycle management for the application layer."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Callable

from markdown_ingress.config_models import IngestConfig
from markdown_ingress.core.inflight import build_request_identity
from markdown_ingress.core.interfaces import IFetcher
from markdown_ingress.core.stealth.browser_config import ADVANCED_USER_AGENTS

_logger = logging.getLogger(__name__)

_DEFAULT_FETCHER_UA_SEED = "markdown-ingress:auto-fetcher-user-agent:v1"


def _select_stable_fetcher_user_agent() -> str:
    """Select one deterministic automatic UA for a shared ingest use case."""
    digest = hashlib.sha256(_DEFAULT_FETCHER_UA_SEED.encode("utf-8")).digest()
    return ADVANCED_USER_AGENTS[int.from_bytes(digest[:8], "big") % len(ADVANCED_USER_AGENTS)]


def _ensure_fetcher_user_agent(
    url: str,
    config: IngestConfig,
    matched_domain_policy=None,
    *,
    default_user_agent: str | None = None,
) -> str:
    """Select and persist a per-request HTTP user agent.

    The request identity and the actual fetcher must use the same UA so cache
    and in-flight deduplication do not cross-contaminate different request
    variants. Application-owned requests should pass ``default_user_agent`` so
    the shared fetcher and per-domain state remain stable across URLs.

    Note: Intentionally mutates ``config.fetcher_user_agent`` in-place.
    Callers must pass a cloned config to avoid polluting shared state.
    """
    if config.fetcher_user_agent:
        return config.fetcher_user_agent
    if default_user_agent:
        config.fetcher_user_agent = default_user_agent
        return default_user_agent
    identity_config = config.clone()
    identity_config.fetcher_user_agent = ""
    identity_payload = build_request_identity(url, identity_config, matched_domain_policy)
    digest = hashlib.sha256(
        json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).digest()
    selected = ADVANCED_USER_AGENTS[int.from_bytes(digest[:8], "big") % len(ADVANCED_USER_AGENTS)]
    config.fetcher_user_agent = selected
    return selected


def _close_fetcher(fetcher: object) -> None:
    """Close a sync fetcher if it exposes a close() hook."""
    close = getattr(fetcher, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception as exc:  # pragma: no cover - defensive cleanup path
        _logger.warning("Failed to close fetcher cleanly: %s", exc, exc_info=True)


class _SharedFetcherManager:
    """Manages shared Fetchers, reusing them when config is compatible."""

    def __init__(self, factory: Callable[[IngestConfig], IFetcher]) -> None:
        self._factory = factory
        self._fetchers: dict[tuple, IFetcher] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _make_config_key(config: IngestConfig) -> tuple:
        return (
            config.timeout,
            getattr(config, "fetcher_user_agent", None),
            config.allow_local_urls,
            config.domain_request_interval,
            config.circuit_breaker_threshold,
            config.circuit_breaker_open_seconds,
        )

    def get(self, config: IngestConfig) -> IFetcher:
        key = self._make_config_key(config)
        with self._lock:
            fetcher = self._fetchers.get(key)
            if fetcher is None:
                fetcher = self._factory(config)
                self._fetchers[key] = fetcher
            return fetcher

    def close(self) -> None:
        with self._lock:
            fetchers = list(self._fetchers.values())
            self._fetchers.clear()
        for fetcher in fetchers:
            _close_fetcher(fetcher)
