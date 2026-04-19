"""Shared fetcher lifecycle management for the application layer."""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable

from markdown_ingress.config_models import IngestConfig
from markdown_ingress.core.inflight import build_request_identity
from markdown_ingress.core.interfaces import IFetcher
from markdown_ingress.core.stealth.browser_config import ADVANCED_USER_AGENTS

_logger = logging.getLogger(__name__)


def _ensure_fetcher_user_agent(
    url: str,
    config: IngestConfig,
    matched_domain_policy=None,
) -> str:
    """Select and persist a per-request HTTP user agent.

    The request identity and the actual fetcher must use the same UA so cache
    and in-flight deduplication do not cross-contaminate different request
    variants.

    Note: Intentionally mutates ``config.fetcher_user_agent`` in-place.
    Callers must pass a cloned config to avoid polluting shared state.
    """
    if config.fetcher_user_agent:
        return config.fetcher_user_agent
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
    """Manages a shared Fetcher, reusing it when config is compatible."""

    def __init__(self, factory: Callable[[IngestConfig], IFetcher]) -> None:
        self._factory = factory
        self._fetcher: IFetcher | None = None
        self._config_key: tuple | None = None

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
        if self._fetcher is not None and self._config_key == key:
            return self._fetcher
        if self._fetcher is not None:
            _close_fetcher(self._fetcher)
        self._fetcher = self._factory(config)
        self._config_key = key
        return self._fetcher

    def close(self) -> None:
        if self._fetcher is not None:
            _close_fetcher(self._fetcher)
            self._fetcher = None
            self._config_key = None
