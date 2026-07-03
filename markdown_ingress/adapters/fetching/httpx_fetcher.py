"""HTTP fetcher adapter using httpx - fast mode implementation."""

import asyncio
import time
from threading import Lock
from typing import TypedDict, Unpack, cast

import httpx

from markdown_ingress.adapters.fetching.client_lifecycle import ClientLifecycleMixin
from markdown_ingress.adapters.fetching.domain_state import DomainStateMixin
from markdown_ingress.adapters.fetching.http_support import (
    DEFAULT_DOMAIN_STATE_TTL as _DEFAULT_DOMAIN_STATE_TTL,
)
from markdown_ingress.adapters.fetching.http_support import (
    DEFAULT_MAX_HOSTS as _DEFAULT_MAX_HOSTS,
)
from markdown_ingress.adapters.fetching.http_support import (
    DEFAULT_MAX_RESPONSE_SIZE as _DEFAULT_MAX_RESPONSE_SIZE,
)
from markdown_ingress.adapters.fetching.http_support import ResponseSizeLimitError
from markdown_ingress.adapters.fetching.httpx_fetch_async import AsyncHttpxFetchMixin
from markdown_ingress.adapters.fetching.httpx_fetch_sync import SyncHttpxFetchMixin
from markdown_ingress.adapters.fetching.request_policy import FetchRequestPolicyMixin
from markdown_ingress.adapters.fetching.response_content import ResponseContentMixin
from markdown_ingress.adapters.fetching.ssl_bypass_fetch import SslBypassFetchMixin
from markdown_ingress.config_validation import collect_option_values
from markdown_ingress.core.interfaces import IFetcher
from markdown_ingress.core.policy import DomainCircuitOpenError, UnsupportedContentTypeError
from markdown_ingress.stealth_browser_profiles import ADVANCED_USER_AGENTS

__all__ = [
    "DomainCircuitOpenError",
    "Fetcher",
    "ResponseSizeLimitError",
    "UnsupportedContentTypeError",
]


class FetcherOptions(TypedDict, total=False):
    timeout: float
    user_agent: str | None
    follow_redirects: bool
    max_redirects: int
    rotate_ua: bool
    domain_request_interval: float
    circuit_breaker_threshold: int
    circuit_breaker_open_seconds: float
    allow_ssl_bypass: bool
    ca_bundle: str | None
    allow_local_urls: bool | None
    domain_state_ttl: float
    max_hosts: int
    max_response_size: int | None
    failure_decay_seconds: float | None


_FETCHER_OPTION_NAMES = (
    "timeout",
    "user_agent",
    "follow_redirects",
    "max_redirects",
    "rotate_ua",
    "domain_request_interval",
    "circuit_breaker_threshold",
    "circuit_breaker_open_seconds",
    "allow_ssl_bypass",
    "ca_bundle",
    "allow_local_urls",
    "domain_state_ttl",
    "max_hosts",
    "max_response_size",
    "failure_decay_seconds",
)


def _normalize_fetcher_options(
    args: tuple[object, ...],
    options: FetcherOptions,
) -> FetcherOptions:
    return cast(
        FetcherOptions,
        collect_option_values("Fetcher()", _FETCHER_OPTION_NAMES, args, options),
    )


class Fetcher(
    AsyncHttpxFetchMixin,
    SyncHttpxFetchMixin,
    ClientLifecycleMixin,
    FetchRequestPolicyMixin,
    ResponseContentMixin,
    SslBypassFetchMixin,
    DomainStateMixin,
    IFetcher,
):
    """HTTP fetcher for fast mode (no JS rendering)."""

    DEFAULT_TIMEOUT = 30.0
    DEFAULT_DOMAIN_REQUEST_INTERVAL = 0.25

    def __init__(self, *args: object, **options: Unpack[FetcherOptions]) -> None:
        parsed = _normalize_fetcher_options(args, options)
        timeout = parsed.get("timeout", self.DEFAULT_TIMEOUT)
        user_agent = parsed.get("user_agent")
        follow_redirects = parsed.get("follow_redirects", True)
        max_redirects = parsed.get("max_redirects", 10)
        rotate_ua = parsed.get("rotate_ua", True)
        domain_request_interval = parsed.get(
            "domain_request_interval",
            self.DEFAULT_DOMAIN_REQUEST_INTERVAL,
        )
        circuit_breaker_threshold = parsed.get("circuit_breaker_threshold", 3)
        circuit_breaker_open_seconds = parsed.get("circuit_breaker_open_seconds", 30.0)
        allow_ssl_bypass = parsed.get("allow_ssl_bypass", False)
        ca_bundle = parsed.get("ca_bundle")
        allow_local_urls = parsed.get("allow_local_urls")
        domain_state_ttl = parsed.get("domain_state_ttl", _DEFAULT_DOMAIN_STATE_TTL)
        max_hosts = parsed.get("max_hosts", _DEFAULT_MAX_HOSTS)
        max_response_size = parsed.get("max_response_size", _DEFAULT_MAX_RESPONSE_SIZE)
        failure_decay_seconds = parsed.get("failure_decay_seconds", 300.0)

        self.timeout = timeout
        self._fixed_ua = user_agent
        self.rotate_ua = rotate_ua and user_agent is None
        self._ua_pool = tuple(ADVANCED_USER_AGENTS)
        self._stable_ua: str | None = None
        self._last_rotating_ua: str | None = None
        if user_agent is None and not self.rotate_ua:
            self._stable_ua = self._next_user_agent()
        self.follow_redirects = follow_redirects
        self.max_redirects = max_redirects
        self.domain_request_interval = max(0.0, domain_request_interval)
        self.circuit_breaker_threshold = max(1, circuit_breaker_threshold)
        self.circuit_breaker_open_seconds = max(1.0, circuit_breaker_open_seconds)
        self.allow_ssl_bypass = allow_ssl_bypass
        self.ca_bundle = ca_bundle
        self.allow_local_urls = self._resolve_allow_local_urls(allow_local_urls)
        self.domain_state_ttl = max(0.0, domain_state_ttl)
        self.max_hosts = max(1, max_hosts)
        if max_response_size is not None and max_response_size <= 0:
            raise ValueError("max_response_size must be > 0 or None")
        self.max_response_size = max_response_size
        if failure_decay_seconds is not None and failure_decay_seconds <= 0:
            raise ValueError("failure_decay_seconds must be > 0 or None")
        self.failure_decay_seconds = failure_decay_seconds
        # CRITICAL: Lock ordering to prevent deadlocks.
        # Always acquire _domain_lock before _failure_lock.
        self._domain_lock = Lock()
        self._next_allowed_by_host: dict[str, float] = {}
        self._domain_state_timestamp: dict[str, float] = {}
        self._failure_lock = Lock()
        self._failures_by_host: dict[str, int] = {}
        self._failure_first_seen: dict[str, float] = {}
        self._open_until_by_host: dict[str, float] = {}
        self._last_cleanup = time.monotonic()
        self._last_failure_cleanup = self._last_cleanup
        self._cleanup_lock = Lock()
        self._cleanup_running = False
        self._async_client: httpx.AsyncClient | None = None
        self._sync_client: httpx.Client | None = None
        self._client_lock = Lock()
        self._async_client_lock_guard = Lock()
        self._async_client_lock: asyncio.Lock | None = None
        self._ssl_bypass_hosts: dict[str, float] = {}
        self._ssl_bypass_ttl: float = 300.0
        self._ssl_bypass_lock = Lock()
        self._closing = False
        self._async_close_tasks: set[asyncio.Task[None]] = set()
