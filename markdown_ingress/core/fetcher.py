"""
HTTP fetcher module - Fast mode implementation
"""

import asyncio
import logging
import random
import ssl
import time
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import urlsplit

import httpx

from markdown_ingress.core.ssrf import (
    normalize_hostname,
    resolve_allow_local_urls,
    validate_http_url_no_ssrf,
)
from markdown_ingress.core.stealth.browser_config import ADVANCED_USER_AGENTS
from markdown_ingress.models import FetchResult

logger = logging.getLogger(__name__)

# Safe headers that don't require TLS fingerprint matching.
# NOTE: Accept-Encoding is intentionally omitted — let httpx manage it based
# on installed decompression libraries to avoid receiving unsupported encodings.
_SAFE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "max-age=0",
    "DNT": "1",
    "Upgrade-Insecure-Requests": "1",
}

# HTTP status codes that warrant a retry with a different User-Agent
_RETRYABLE_STATUS = {403, 429, 503}
_MAX_RETRIES = 3
_HTML_CONTENT_TYPES = (
    "text/html",
    "application/xhtml+xml",
)
_HOST_SOFT_THROTTLE_HINTS = {
    "amazon.com": (3.0, 2.5),
    "bestbuy.com": (2.5, 2.0),
    "bestbuyads.com": (2.5, 2.0),
    "facebook.com": (4.0, 3.0),
    "instagram.com": (4.0, 3.0),
    "sephora.com": (3.0, 2.0),
    "wayfair.com": (3.0, 2.0),
    "walmart.com": (2.5, 2.0),
}

# Default TTL for domain state entries (seconds)
_DEFAULT_DOMAIN_STATE_TTL = 3600  # 1 hour
# Maximum number of hosts to track before cleanup
_DEFAULT_MAX_HOSTS = 10000
# Default max response size (10MB)
_DEFAULT_MAX_RESPONSE_SIZE = 10 * 1024 * 1024

_rng = random.SystemRandom()


class UnsupportedContentTypeError(ValueError):
    """Raised when the fetched response is not HTML-like content."""


class DomainCircuitOpenError(RuntimeError):
    """Raised when a per-domain circuit breaker is open."""


def _is_supported_html_content_type(content_type: str | None) -> bool:
    """Return whether a response Content-Type looks HTML-compatible."""
    if content_type is None:
        logger.debug("Response has no Content-Type header; rejecting as non-HTML")
        return False  # Missing header — do NOT assume HTML
    if not content_type.strip():
        logger.debug("Response has empty Content-Type header; rejecting as non-HTML")
        return False  # Empty Content-Type — do NOT assume HTML
    normalized = content_type.split(";", 1)[0].strip().lower()
    return normalized in _HTML_CONTENT_TYPES


def _validate_content_type(response: httpx.Response) -> None:
    """Reject non-HTML payloads early before sending them through HTML extraction."""
    content_type = response.headers.get("content-type")
    if _is_supported_html_content_type(content_type):
        return
    raise UnsupportedContentTypeError(
        f"Unsupported content type for HTML ingestion: {content_type or 'unknown'}"
    )


def _parse_retry_after(value: str | None) -> float | None:
    """Parse Retry-After seconds or HTTP date into a non-negative delay."""
    from email.utils import parsedate_to_datetime

    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        # Apply a 0.25s floor to avoid tight retry loops on Retry-After: 0.
        return max(0.25, float(raw))
    except ValueError:
        pass
    try:
        # parsedate_to_datetime is locale-independent (RFC 2822), unlike strptime.
        retry_at = parsedate_to_datetime(raw)
    except Exception:
        return None
    return max(0.25, (retry_at - datetime.now(UTC)).total_seconds())


def _retry_delay_seconds(response: httpx.Response, attempt: int) -> float:
    """Choose a retry delay using Retry-After when available, else status-aware backoff."""
    retry_after = _parse_retry_after(response.headers.get("retry-after"))
    if retry_after is not None:
        return float(min(retry_after, 10.0))
    if response.status_code == 429:
        return float(min(1.5 * (2**attempt), 10.0))
    return float(min(0.5 * (attempt + 1), 2.0))


def _parse_content_length(content_length: str | None) -> int | None:
    """
    Safely parse Content-Length header value.
    Returns None if the value is missing or malformed.
    """
    if not content_length:
        return None
    try:
        value = int(content_length)
        return value if value >= 0 else None
    except ValueError:
        logger.warning("Malformed Content-Length header: %s", content_length)
        return None


def _host_soft_throttle_delay(host: str, base_delay: float) -> float:
    """Increase delay for domains that frequently rate limit or block automation."""
    normalized = host.lower()
    for suffix, (multiplier, minimum_delay) in _HOST_SOFT_THROTTLE_HINTS.items():
        if normalized == suffix or normalized.endswith(f".{suffix}"):
            return max(base_delay * multiplier, minimum_delay)
    return base_delay


class Fetcher:  # implements IFetcher protocol
    """HTTP fetcher for fast mode (no JS rendering)"""

    DEFAULT_TIMEOUT = 30.0
    DEFAULT_DOMAIN_REQUEST_INTERVAL = 0.25

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT,
        user_agent: str | None = None,
        follow_redirects: bool = True,
        max_redirects: int = 10,
        rotate_ua: bool = True,
        domain_request_interval: float = DEFAULT_DOMAIN_REQUEST_INTERVAL,
        circuit_breaker_threshold: int = 3,
        circuit_breaker_open_seconds: float = 30.0,
        allow_ssl_bypass: bool = False,
        ca_bundle: str | None = None,
        allow_local_urls: bool | None = None,
        domain_state_ttl: float = _DEFAULT_DOMAIN_STATE_TTL,
        max_hosts: int = _DEFAULT_MAX_HOSTS,
        max_response_size: int | None = _DEFAULT_MAX_RESPONSE_SIZE,
        failure_decay_seconds: float | None = 300.0,
    ):
        self.timeout = timeout
        # If a specific UA is given, always use it. Otherwise either keep one
        # stable per instance or rotate across attempts depending on rotate_ua.
        self._fixed_ua = user_agent
        self.rotate_ua = rotate_ua and user_agent is None
        self._ua_pool = tuple(ADVANCED_USER_AGENTS)
        self._stable_ua: str | None = None
        self._last_rotating_ua: str | None = None
        if user_agent is None and not self.rotate_ua:
            self._stable_ua = _rng.choice(self._ua_pool)
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
        # CRITICAL: Lock ordering to prevent deadlocks - ALWAYS acquire _domain_lock BEFORE _failure_lock
        # when both locks are needed. This ordering must be consistent across all methods.
        self._domain_lock = Lock()
        self._next_allowed_by_host: dict[str, float] = {}
        self._domain_state_timestamp: dict[str, float] = {}  # Track when host was last accessed
        self._failure_lock = Lock()
        self._failures_by_host: dict[str, int] = {}
        self._failure_first_seen: dict[str, float] = {}  # Track when failures started
        self._open_until_by_host: dict[str, float] = {}
        self._last_cleanup = time.monotonic()
        self._last_failure_cleanup = self._last_cleanup
        self._cleanup_lock = Lock()  # Protects _last_cleanup for atomic check-and-set
        # Cached HTTP clients for connection pool reuse across requests
        self._async_client: httpx.AsyncClient | None = None
        self._sync_client: httpx.Client | None = None
        self._client_lock = Lock()  # Protects lazy client creation
        self._async_client_lock_guard = Lock()
        self._async_client_lock: asyncio.Lock | None = None
        self._async_client_lock_loop: asyncio.AbstractEventLoop | None = None
        # Track pending async close tasks to prevent leaks (Bug 8)
        self._pending_async_closes: list[asyncio.Task] = []
        self._pending_closes_lock = Lock()
        # Cache hosts that require SSL bypass with TTL to avoid repeated retry cycles
        self._ssl_bypass_hosts: dict[str, float] = {}
        self._ssl_bypass_ttl: float = 300.0
        # Runtime fix (L6): guard the bypass cache with its own lock so that
        # concurrent expiry pops and reads are serialised.
        self._ssl_bypass_lock = Lock()

    def _is_ssl_bypass_active(self, host: str) -> bool:
        """Check if SSL bypass is cached and still valid for a host."""
        with self._ssl_bypass_lock:
            expiry = self._ssl_bypass_hosts.get(host)
            if expiry is None:
                return False
            if time.monotonic() > expiry:
                self._ssl_bypass_hosts.pop(host, None)
                return False
            return True

    async def _validate_redirect_response(self, response: httpx.Response) -> None:
        """httpx event hook: validate each redirect target against SSRF."""
        if response.is_redirect:
            location = response.headers.get("location")
            if location:
                redirect_url = str(response.url.join(location))
                validate_http_url_no_ssrf(
                    redirect_url,
                    allow_local=self.allow_local_urls,
                    resolve_dns=True,
                )

    def _validate_redirect_response_sync(self, response: httpx.Response) -> None:
        """httpx sync event hook: validate each redirect target against SSRF."""
        if response.is_redirect:
            location = response.headers.get("location")
            if location:
                redirect_url = str(response.url.join(location))
                validate_http_url_no_ssrf(
                    redirect_url,
                    allow_local=self.allow_local_urls,
                    resolve_dns=True,
                )

    def _build_ssl_context(self) -> ssl.SSLContext:
        """Build an SSL context, optionally with a custom CA bundle."""
        ctx = ssl.create_default_context()
        if self.ca_bundle:
            if Path(self.ca_bundle).is_dir():
                ctx.load_verify_locations(capath=self.ca_bundle)
            else:
                ctx.load_verify_locations(cafile=self.ca_bundle)
        return ctx

    @property
    def user_agent(self) -> str:
        """Return the effective User-Agent for the current fetcher instance."""
        if self._fixed_ua:
            return self._fixed_ua
        if not self.rotate_ua:
            if self._stable_ua is None:
                self._stable_ua = _rng.choice(self._ua_pool)
            return self._stable_ua
        return self._next_user_agent(previous=self._last_rotating_ua)

    def _next_user_agent(self, *, previous: str | None = None) -> str:
        """Select the next User-Agent, avoiding the previous one when rotating."""
        if self._fixed_ua:
            return self._fixed_ua
        if not self.rotate_ua:
            return self.user_agent

        if previous is not None and len(self._ua_pool) > 1:
            candidates = [ua for ua in self._ua_pool if ua != previous]
        else:
            candidates = list(self._ua_pool)
        ua = _rng.choice(candidates)
        self._last_rotating_ua = ua
        return ua

    def _get_sync_client(self) -> httpx.Client:
        """Get or create the cached synchronous HTTP client for connection pooling."""
        if self._sync_client is None:
            with self._client_lock:
                if self._sync_client is None:
                    self._sync_client = httpx.Client(
                        timeout=self.timeout,
                        follow_redirects=self.follow_redirects,
                        max_redirects=self.max_redirects,
                        verify=self._build_ssl_context(),
                        trust_env=False,
                        event_hooks={
                            "response": [self._validate_redirect_response_sync],
                        },
                    )
        return self._sync_client

    def _get_async_client_lock(self) -> asyncio.Lock:
        """Create the async lock lazily inside an active event loop.

        If the cached lock was created in a different event loop (which
        happens when the Fetcher is reused across ``asyncio.run()`` calls),
        discard it and create a fresh one for the current loop.
        """
        with self._async_client_lock_guard:
            try:
                current_loop = asyncio.get_running_loop()
            except RuntimeError:
                current_loop = None
            # Invalidate cached lock when there is no running loop (called from a
            # thread pool) OR when it was created for a different event loop.
            if self._async_client_lock is not None and (
                current_loop is None
                or self._async_client_lock_loop is not current_loop
            ):
                self._async_client_lock = None
            if self._async_client_lock is None:
                self._async_client_lock = asyncio.Lock()
                self._async_client_lock_loop = current_loop
            return self._async_client_lock

    async def _get_async_client(self) -> httpx.AsyncClient:
        """Get or create the cached async HTTP client for connection pooling."""
        if self._async_client is None:
            async with self._get_async_client_lock():
                if self._async_client is None:
                    self._async_client = httpx.AsyncClient(
                        timeout=self.timeout,
                        follow_redirects=self.follow_redirects,
                        max_redirects=self.max_redirects,
                        verify=self._build_ssl_context(),
                        trust_env=False,
                        event_hooks={
                            "response": [self._validate_redirect_response],
                        },
                    )
        return self._async_client

    def close(self) -> None:
        """Close both the synchronous and asynchronous HTTP clients and release resources.

        For the async client, if no event loop is running, this creates a
        short-lived event loop to run ``aclose()``. If a loop is already
        running (e.g. inside pytest-asyncio), the client reference is nulled
        and a close is scheduled as a background task on the running loop.
        """
        with self._client_lock:
            if self._sync_client is not None:
                self._sync_client.close()
                self._sync_client = None
        # Nullify async client under the guard lock to prevent race with
        # _get_async_client() overwriting a new client after we set None.
        with self._async_client_lock_guard:
            client_to_close = self._async_client
            self._async_client = None
            self._async_client_lock = None
        if client_to_close is None:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No running loop -- safe to create one for the close.
            try:
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(client_to_close.aclose())
                finally:
                    loop.close()
            except Exception:
                logger.warning(
                    "Could not close async HTTP client synchronously; "
                    "use 'async with fetcher:' or 'await fetcher.aclose()' instead."
                )
        else:
            # A loop is already running -- schedule close as background task.
            logger.warning(
                "Cannot close async HTTP client from within a running event loop; "
                "use 'async with fetcher:' or 'await fetcher.aclose()' instead. "
                "Scheduling close as background task."
            )
            try:
                loop = asyncio.get_running_loop()
                close_future = loop.create_task(client_to_close.aclose())
                with self._pending_closes_lock:
                    self._pending_async_closes.append(close_future)
            except Exception:
                logger.debug(
                    "Failed to schedule async client close as background task"
                )

    async def aclose(self) -> None:
        """Close the async HTTP client and release resources.

        Thread-safe: Uses async lock to prevent race conditions when
        multiple coroutines try to close the client simultaneously.
        Also resets the cached asyncio.Lock so that a subsequent event
        loop (e.g. a new ``asyncio.run()``) creates a fresh one.
        """
        async with self._get_async_client_lock():
            if self._async_client is not None:
                await self._async_client.aclose()
                self._async_client = None
        with self._async_client_lock_guard:
            self._async_client_lock = None
        # Await any pending close tasks scheduled by close() from a running loop
        with self._pending_closes_lock:
            pending = list(self._pending_async_closes)
            self._pending_async_closes.clear()
        for task in pending:
            if not task.done():
                try:
                    await task
                except Exception:
                    pass

    def __enter__(self) -> "Fetcher":
        """Context manager entry for synchronous usage."""
        return self

    def __exit__(self, *args: Any) -> None:
        """Context manager exit - ensures resources are released."""
        self.close()

    async def __aenter__(self) -> "Fetcher":
        """Async context manager entry."""
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Async context manager exit - ensures resources are released."""
        await self.aclose()

    def __del__(self) -> None:
        """Cleanup on destruction with warning for resource leaks."""
        # BUG FIX: Use hasattr to avoid AttributeError if __init__ failed before
        # setting these attributes
        sync_client_exists = hasattr(self, "_sync_client") and self._sync_client is not None
        async_client_exists = hasattr(self, "_async_client") and self._async_client is not None

        if sync_client_exists or async_client_exists:
            import warnings

            warnings.warn(
                "Fetcher was not properly closed; HTTP client resources may leak. "
                "Use 'async with fetcher:' or call 'await fetcher.aclose()' explicitly.",
                ResourceWarning,
                stacklevel=2,
            )
        # Only attempt synchronous client cleanup. The async client cannot
        # be safely closed during interpreter shutdown (the event loop it
        # was created in may no longer be running), so just null references.
        try:
            if (
                hasattr(self, "_client_lock")
                and hasattr(self, "_sync_client")
                and self._sync_client is not None
            ):
                self._sync_client.close()
                self._sync_client = None
        except Exception:
            pass
        if hasattr(self, "_async_client"):
            self._async_client = None
        if hasattr(self, "_async_client_lock_guard"):
            with self._async_client_lock_guard:
                self._async_client_lock = None

    def _build_headers(self, ua: str, *, host_header: str | None = None) -> dict:
        """Build safe browser-like headers for a given User-Agent.

        When ``host_header`` is provided (for DNS-rebinding protection where
        the URL target is an IP but the original hostname must be sent as the
        Host header), it is included in the headers.
        """
        headers = dict(_SAFE_HEADERS)
        headers["User-Agent"] = ua
        if host_header is not None:
            headers["Host"] = host_header
        return headers

    @staticmethod
    def _resolve_allow_local_urls(allow_local_urls: bool | None) -> bool:
        """Resolve explicit allow-local setting with an env-based opt-in fallback."""
        return resolve_allow_local_urls(allow_local_urls)

    @staticmethod
    def _validate_url(url: str, *, allow_local_urls: bool = False) -> str:
        """Validate URL and return normalized form.

        Raises:
            ValueError: If URL is invalid or uses disallowed scheme.
        """
        return validate_http_url_no_ssrf(
            url,
            allow_local=allow_local_urls,
            resolve_dns=True,
        )

    @staticmethod
    def _host_key(url: str) -> str:
        """Normalize URL host for per-domain throttling.

        Assumes URL has already been validated by _validate_url().
        """
        return normalize_hostname(urlsplit(url).hostname or "")

    @classmethod
    def _effective_host(cls, final_url: str | None, fallback_host: str) -> str:
        """Resolve the effective host for post-response accounting."""
        if not final_url:
            return fallback_host
        resolved = cls._host_key(final_url)
        return resolved or fallback_host

    def _cleanup_domain_state(self) -> None:
        """Remove stale entries from domain state dicts to prevent unbounded growth."""
        now = time.monotonic()

        # Only run cleanup periodically to avoid overhead on every request
        # Use _cleanup_lock for atomic check-and-set to prevent race conditions
        with self._cleanup_lock:
            if now - self._last_cleanup < 60.0:  # Cleanup at most once per minute
                return
            self._last_cleanup = now

        # Clean up entries older than TTL
        if self.domain_state_ttl <= 0:
            return

        # Collect stale hosts under _domain_lock, then clean failure tracking
        # under _failure_lock separately — never nest one lock inside the other
        # to prevent ABBA deadlock with callers that acquire failure_lock first.
        with self._domain_lock:
            # Clean up _next_allowed_by_host
            stale_hosts = [
                host
                for host, ts in self._domain_state_timestamp.items()
                if now - ts > self.domain_state_ttl
            ]
            stale_hosts_set = set(stale_hosts)
            for host in stale_hosts:
                self._next_allowed_by_host.pop(host, None)
                self._domain_state_timestamp.pop(host, None)

            # Enforce max_hosts limit by evicting oldest entries
            if len(self._next_allowed_by_host) > self.max_hosts:
                sorted_hosts = sorted(
                    self._domain_state_timestamp.items(),
                    key=lambda x: x[1],
                )
                evict_count = len(self._next_allowed_by_host) - self.max_hosts
                for host, _ in sorted_hosts[:evict_count]:
                    self._next_allowed_by_host.pop(host, None)
                    self._domain_state_timestamp.pop(host, None)
                    stale_hosts_set.add(host)

        # _domain_lock released before acquiring _failure_lock to prevent ABBA deadlock.
        with self._failure_lock:
            # Remove hosts that were evicted from domain state AND stale hosts
            for host in stale_hosts_set:
                self._failures_by_host.pop(host, None)
                self._failure_first_seen.pop(host, None)
                self._open_until_by_host.pop(host, None)

            # Also clean up any remaining stale hosts from failure tracking
            remaining_stale = [
                host
                for host, ts in self._failure_first_seen.items()
                if now - ts > self.domain_state_ttl
            ]
            for host in remaining_stale:
                self._failures_by_host.pop(host, None)
                self._failure_first_seen.pop(host, None)
                self._open_until_by_host.pop(host, None)

    def _apply_failure_decay_locked(self, host: str) -> int:
        """Apply time-based decay to failure counts and return current count.

        Must be called with _failure_lock held.
        """
        if self.failure_decay_seconds is None or self.failure_decay_seconds <= 0:
            return self._failures_by_host.get(host, 0)

        now = time.monotonic()
        first_seen = self._failure_first_seen.get(host, now)
        current = self._failures_by_host.get(host, 0)

        if current > 0 and first_seen:
            # Decay: reduce failures proportionally to time elapsed
            elapsed = now - first_seen
            if elapsed > self.failure_decay_seconds:
                # Reset failures after decay period
                self._failures_by_host[host] = 0
                self._failure_first_seen[host] = now
                self._open_until_by_host.pop(host, None)
                return 0
            # Partial decay: reduce by half for each decay period elapsed.
            # Return computed value WITHOUT persisting — only the full-decay
            # branch (reset to 0) mutates state.  This prevents repeated
            # int() truncation from eroding the failure count on every read.
            decay_factor = 0.5 ** (elapsed / self.failure_decay_seconds)
            return int(round(current * decay_factor))
        return current

    def _apply_failure_decay(self, host: str) -> int:
        """Apply time-based decay to failure counts and return current count.

        This method is thread-safe and acquires _failure_lock internally.
        Callers do NOT need to hold the lock when calling this method.
        """
        with self._failure_lock:
            return self._apply_failure_decay_locked(host)

    def _reserve_domain_slot(self, host: str) -> float:
        """Reserve the next allowed request slot for a host and return sleep seconds."""
        if not host:
            logger.warning("Empty host detected - rate limiting bypassed for malformed URL")
            return 0.0

        # Periodic cleanup to prevent unbounded growth
        self._cleanup_domain_state()

        with self._domain_lock:
            now = time.monotonic()
            next_allowed = self._next_allowed_by_host.get(host, 0.0)
            slot = max(now, next_allowed)
            if self.domain_request_interval > 0.0:
                self._next_allowed_by_host[host] = slot + self.domain_request_interval
            else:
                self._next_allowed_by_host[host] = slot
            # Track when this host was last accessed for TTL cleanup
            self._domain_state_timestamp[host] = now
            return max(0.0, slot - now)

    def _defer_host(self, host: str, delay_seconds: float) -> None:
        """Push the next allowed request slot for a host into the future."""
        if not host or delay_seconds <= 0.0:
            return
        with self._domain_lock:
            now = time.monotonic()
            next_allowed = self._next_allowed_by_host.get(host, now)
            self._next_allowed_by_host[host] = max(next_allowed, now + delay_seconds)
            self._domain_state_timestamp[host] = now

    def _ensure_circuit_closed(self, host: str) -> None:
        """Raise if the host circuit is currently open.

        Thread-safety: The entire check is performed under _failure_lock to
        prevent TOCTOU race conditions between checking open_until and
        other threads modifying circuit breaker state.
        """
        if not host:
            return
        with self._failure_lock:
            # BUG FIX: Apply decay BEFORE reading open_until to avoid using stale value.
            # Previously, open_until was read before decay, which could miss circuit resets.
            self._apply_failure_decay_locked(host)
            # Read open_until AFTER decay to get current state
            open_until = self._open_until_by_host.get(host, 0.0)
            # Check inside lock to prevent TOCTOU race
            if open_until > time.monotonic():
                raise DomainCircuitOpenError(f"Circuit breaker open for host: {host}")

    def _record_success(self, host: str) -> None:
        """Reset failure state for a healthy host."""
        if not host:
            return
        with self._failure_lock:
            self._failures_by_host.pop(host, None)
            self._failure_first_seen.pop(host, None)
            self._open_until_by_host.pop(host, None)

    def _record_failure(self, host: str) -> None:
        """Increment failure count and open circuit when threshold is reached."""
        if not host:
            return

        # Periodic cleanup of stale hosts from failure tracking
        # This ensures hosts that fail once and are never requested again
        # don't cause unbounded memory growth in failure dictionaries.
        now = time.monotonic()
        should_cleanup = False
        with self._cleanup_lock:
            if now - self._last_failure_cleanup > 60.0:
                should_cleanup = True
                self._last_failure_cleanup = now

        with self._failure_lock:
            # Track when failures first started for this host
            if host not in self._failure_first_seen:
                self._failure_first_seen[host] = now

            # Apply decay BEFORE incrementing to get the correct effective count
            decayed = self._apply_failure_decay_locked(host)
            new_count = decayed + 1
            self._failures_by_host[host] = new_count

            # Use decayed+1 value for threshold comparison
            if new_count >= self.circuit_breaker_threshold:
                self._open_until_by_host[host] = now + self.circuit_breaker_open_seconds
                # Keep count at half-threshold so persistent failures reopen faster
                self._failures_by_host[host] = max(1, (self.circuit_breaker_threshold + 1) // 2)

            # Run cleanup if triggered (already holds _failure_lock)
            if should_cleanup:
                self._cleanup_stale_failures_locked(now)

    def _cleanup_stale_failures_locked(self, now: float) -> None:
        """Clean up stale entries from failure tracking dictionaries.

        Must be called with _failure_lock held.
        """
        if self.domain_state_ttl <= 0:
            return

        # Find hosts that have been in failure tracking longer than TTL
        stale_hosts = [
            host
            for host, ts in self._failure_first_seen.items()
            if now - ts > self.domain_state_ttl
        ]

        for host in stale_hosts:
            self._failures_by_host.pop(host, None)
            self._failure_first_seen.pop(host, None)
            self._open_until_by_host.pop(host, None)

    def _record_soft_throttle(self, host: str, delay_seconds: float) -> None:
        """Apply per-host backoff without escalating to circuit-breaker failures."""
        if not host:
            return
        self._defer_host(host, _host_soft_throttle_delay(host, delay_seconds))
        with self._failure_lock:
            self._failures_by_host.pop(host, None)
            self._failure_first_seen.pop(host, None)
            self._open_until_by_host.pop(host, None)

    async def fetch(self, url: str) -> FetchResult:
        """
        Fetch HTML content from URL using httpx (async).
        Makes up to _MAX_RETRIES attempts (1 initial + retries) with a different User-Agent on 403/429/503.

        Args:
            url: Target URL to fetch

        Returns:
            FetchResult with HTML content and metadata

        Raises:
            httpx.HTTPError: On network/HTTP errors after all retries
            ValueError: If URL is invalid or uses disallowed scheme
        """
        # Validate URL before processing to prevent SSRF and bypass attacks.
        # When DNS rebinding protection rewrites the URL to use an IP, we need
        # to send the original hostname as the Host header.
        original_hostname = urlsplit(url).hostname or ""
        url = self._validate_url(url, allow_local_urls=self.allow_local_urls)
        validated_hostname = urlsplit(url).hostname or ""
        # If the hostname changed (IP-pinned by SSRF protection), set Host header
        host_header: str | None = None
        if original_hostname and original_hostname != validated_hostname:
            host_header = original_hostname

        last_exc: Exception | None = None
        host = self._host_key(url)
        # Runtime fix (L6): evaluate SSL-bypass state once; the TTL could expire
        # between two separate calls and leave `verify=True, ssl_retried=True`,
        # which silently contradicts the retry logic.
        ssl_retried = self._is_ssl_bypass_active(host)
        verify: bool | str = not ssl_retried
        attempt = 0
        previous_ua: str | None = None

        # Use cached client for connection pool reuse across requests
        client = await self._get_async_client()
        # Skip main loop when SSL bypass is already cached; go straight to the bypass block.
        while attempt < _MAX_RETRIES and not ssl_retried:
            ua = self._next_user_agent(previous=previous_ua)
            previous_ua = ua
            self._ensure_circuit_closed(host)
            sleep_for = self._reserve_domain_slot(host)
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
                # BUG FIX: Re-check circuit breaker after sleep - state may have changed
                # during the sleep period (another coroutine could have triggered failures)
                self._ensure_circuit_closed(host)
            start_time = time.perf_counter()

            try:
                # BUG FIX: Use streaming to check size incrementally and prevent DoS
                # from large responses that don't have Content-Length header
                # When URL was DNS-pinned to an IP, override SNI so TLS uses the original hostname.
                _stream_kw: dict[str, object] = {"headers": self._build_headers(ua, host_header=host_header)}
                if host_header:
                    _stream_kw["extensions"] = {"sni_hostname": host_header.encode("ascii")}
                async with client.stream("GET", url, **_stream_kw) as response:
                    response_host = self._effective_host(str(response.url), host)

                    # Check Content-Length header first (fast path for responses with known size)
                    if self.max_response_size is not None:
                        content_length = response.headers.get("content-length")
                        parsed_length = _parse_content_length(content_length)
                        if parsed_length is not None and parsed_length > self.max_response_size:
                            raise ValueError(
                                f"Response size {parsed_length} exceeds max_response_size {self.max_response_size}"
                            )

                    # Check for retryable status codes BEFORE raising_for_status
                    if response.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES - 1:
                        retry_delay = _retry_delay_seconds(response, attempt)
                        if response.status_code in {403, 429}:
                            self._record_soft_throttle(response_host, retry_delay)
                        else:
                            self._defer_host(response_host, retry_delay)
                            self._record_failure(response_host)
                        try:
                            await response.aread()
                        except Exception:
                            logger.debug("Failed to read response body during retry", exc_info=True)
                        await asyncio.sleep(retry_delay)
                        attempt += 1
                        continue

                    response.raise_for_status()
                    _validate_content_type(response)

                    # Stream content with size checking to prevent DoS
                    chunks: list[bytes] = []
                    total_size = 0
                    async for chunk in response.aiter_bytes():
                        total_size += len(chunk)
                        if (
                            self.max_response_size is not None
                            and total_size > self.max_response_size
                        ):
                            raise ValueError(
                                f"Response content size {total_size} exceeds max_response_size {self.max_response_size}"
                            )
                        chunks.append(chunk)

                    content = b"".join(chunks)

                elapsed_ms = (time.perf_counter() - start_time) * 1000

                self._record_success(response_host)

                # Decode content using charset from Content-Type header
                encoding = response.charset_encoding or "utf-8"
                try:
                    html = content.decode(encoding)
                except (UnicodeDecodeError, LookupError):
                    # latin-1 can decode any byte sequence without errors
                    html = content.decode("latin-1")

                return FetchResult(
                    html=html,
                    url=url,
                    status_code=response.status_code,
                    final_url=str(response.url),
                    headers=response.headers,
                    timing_ms=elapsed_ms,
                    metadata={"fetcher": "httpx", "user_agent": ua, "attempt": attempt + 1},
                )

            except httpx.HTTPStatusError as exc:
                last_exc = exc
                status_code = exc.response.status_code
                response_host = self._effective_host(str(exc.response.url), host)
                # Early-fail for non-retryable status codes
                if status_code not in _RETRYABLE_STATUS:
                    self._record_failure(response_host)
                    raise
                # On the final attempt, record the appropriate throttle/failure
                # and re-raise without sleeping (no more retries remain).
                if attempt >= _MAX_RETRIES - 1:
                    retry_delay = _retry_delay_seconds(exc.response, attempt)
                    if status_code in {403, 429}:
                        self._record_soft_throttle(response_host, retry_delay)
                    else:
                        self._defer_host(response_host, retry_delay)
                        self._record_failure(response_host)
                    raise
                # Retryable status: record soft throttle and continue
                retry_delay = _retry_delay_seconds(exc.response, attempt)
                if status_code in {403, 429}:
                    self._record_soft_throttle(response_host, retry_delay)
                else:
                    self._defer_host(response_host, retry_delay)
                    self._record_failure(response_host)
                await asyncio.sleep(retry_delay)
                attempt += 1
                continue

            except DomainCircuitOpenError:
                raise

            except Exception as exc:
                last_exc = exc
                self._record_failure(host)

                # On SSL errors, only bypass if explicitly configured and warn
                if (
                    not ssl_retried
                    and self.allow_ssl_bypass
                    and ("SSL" in type(exc).__name__ or "certificate" in str(exc).lower())
                ):
                    logger.warning(
                        "SSL verification failed for %s, retrying with verify=False (insecure). "
                        "This bypass is insecure and should only be used for testing.",
                        url,
                    )
                    verify = False  # pragma: no cover
                    ssl_retried = True  # pragma: no cover
                    # Count the SSL failure as a consumed attempt before switching to bypass.
                    attempt += 1
                    break  # pragma: no cover
                # Non-SSL error or SSL bypass disabled: increment attempt and retry
                attempt += 1

        # If we need to retry with SSL bypass, recreate client
        if ssl_retried and verify is False:
            consumed_attempts = attempt
            remaining_attempts = max(0, _MAX_RETRIES - consumed_attempts)
            if remaining_attempts == 0:
                if last_exc is not None:
                    raise last_exc
                raise RuntimeError(
                    f"SSL bypass fetch failed for {url} after {_MAX_RETRIES} attempts"
                )
            ssl_last_exc: Exception | None = None
            for ssl_attempt in range(remaining_attempts):
                ssl_attempt_num = ssl_attempt + 1  # 1-based for logging
                total_attempt_num = consumed_attempts + ssl_attempt_num
                async with httpx.AsyncClient(
                    timeout=self.timeout,
                    follow_redirects=self.follow_redirects,
                    max_redirects=self.max_redirects,
                    verify=False,  # SSL bypass — insecure, only for testing
                    trust_env=False,
                ) as client:
                    ua = self._next_user_agent(previous=previous_ua)
                    previous_ua = ua
                    self._ensure_circuit_closed(host)
                    sleep_for = self._reserve_domain_slot(host)
                    if sleep_for > 0:
                        await asyncio.sleep(sleep_for)
                        self._ensure_circuit_closed(host)
                    start_time = time.perf_counter()

                    try:
                        # BUG FIX: Use streaming to check size incrementally
                        _ssl_stream_kw: dict[str, object] = {"headers": self._build_headers(ua, host_header=host_header)}
                        if host_header:
                            _ssl_stream_kw["extensions"] = {"sni_hostname": host_header.encode("ascii")}
                        async with client.stream(
                            "GET", url, **_ssl_stream_kw
                        ) as response:
                            response_host = self._effective_host(str(response.url), host)

                            if self.max_response_size is not None:
                                content_length = response.headers.get("content-length")
                                parsed_length = _parse_content_length(content_length)
                                if (
                                    parsed_length is not None
                                    and parsed_length > self.max_response_size
                                ):
                                    raise ValueError(
                                        f"Response size {parsed_length} exceeds max_response_size {self.max_response_size}"
                                    )

                            response.raise_for_status()
                            _validate_content_type(response)

                            # Stream content with size checking
                            ssl_chunks: list[bytes] = []
                            ssl_total_size = 0
                            async for chunk in response.aiter_bytes():
                                ssl_total_size += len(chunk)
                                if (
                                    self.max_response_size is not None
                                    and ssl_total_size > self.max_response_size
                                ):
                                    raise ValueError(
                                        f"Response content size {ssl_total_size} exceeds max_response_size {self.max_response_size}"
                                    )
                                ssl_chunks.append(chunk)

                            ssl_content = b"".join(ssl_chunks)

                        elapsed_ms = (time.perf_counter() - start_time) * 1000
                        self._record_success(response_host)
                        with self._ssl_bypass_lock:
                            self._ssl_bypass_hosts[host] = (
                                time.monotonic() + self._ssl_bypass_ttl
                            )

                        # Decode content using charset from Content-Type header
                        encoding = response.charset_encoding or "utf-8"
                        try:
                            html = ssl_content.decode(encoding)
                        except (UnicodeDecodeError, LookupError):
                            html = ssl_content.decode("latin-1")

                        return FetchResult(
                            html=html,
                            url=url,
                            status_code=response.status_code,
                            final_url=str(response.url),
                            headers=response.headers,
                            timing_ms=elapsed_ms,
                            metadata={
                                "fetcher": "httpx",
                                "user_agent": ua,
                                "attempt": total_attempt_num,
                                "ssl_bypass": True,
                            },
                        )

                    except httpx.HTTPStatusError as exc:
                        ssl_last_exc = exc
                        status_code = exc.response.status_code
                        retry_delay = _retry_delay_seconds(exc.response, ssl_attempt)  # 0-indexed
                        response_host = self._effective_host(str(exc.response.url), host)

                        if (
                            status_code in _RETRYABLE_STATUS
                            and ssl_attempt < remaining_attempts - 1
                        ):
                            logger.warning(
                                "SSL bypass attempt %d/%d failed with %d for %s, retrying in %.1fs",
                                ssl_attempt_num,
                                remaining_attempts,
                                status_code,
                                url,
                                retry_delay,
                            )
                            if status_code in {403, 429}:
                                self._record_soft_throttle(response_host, retry_delay)
                            else:
                                self._defer_host(response_host, retry_delay)
                                self._record_failure(response_host)
                            await asyncio.sleep(retry_delay)
                            continue
                        # Non-retryable or exhausted attempts: still feed the circuit breaker.
                        self._record_failure(response_host)
                        raise

                    except Exception as exc:
                        ssl_last_exc = exc
                        self._record_failure(host)
                        if ssl_attempt < remaining_attempts - 1:
                            retry_delay = float(min(0.5 * (2**ssl_attempt), 2.0))
                            logger.warning(
                                "SSL bypass attempt %d/%d failed for %s: %s, retrying in %.1fs",
                                ssl_attempt_num,
                                remaining_attempts,
                                url,
                                type(exc).__name__,
                                retry_delay,
                            )
                            await asyncio.sleep(retry_delay)
                            continue
                        raise

            # All SSL bypass retries exhausted
            if ssl_last_exc is not None:
                raise ssl_last_exc
            raise RuntimeError(f"SSL bypass fetch failed for {url} after {_MAX_RETRIES} attempts")

        if last_exc is not None:
            raise last_exc
        raise RuntimeError(
            f"Fetch failed for {url} after {_MAX_RETRIES} attempts"
        )  # pragma: no cover

    def fetch_sync(self, url: str) -> FetchResult:
        """
        Synchronous fetch wrapper with UA rotation and retry.

        Args:
            url: Target URL to fetch

        Returns:
            FetchResult with HTML content and metadata

        Raises:
            httpx.HTTPError: On network/HTTP errors after all retries
            ValueError: If URL is invalid or uses disallowed scheme
        """
        # Validate URL before processing to prevent SSRF and bypass attacks.
        # When DNS rebinding protection rewrites the URL to use an IP, we need
        # to send the original hostname as the Host header.
        original_hostname = urlsplit(url).hostname or ""
        url = self._validate_url(url, allow_local_urls=self.allow_local_urls)
        validated_hostname = urlsplit(url).hostname or ""
        host_header: str | None = None
        if original_hostname and original_hostname != validated_hostname:
            host_header = original_hostname

        last_exc: Exception | None = None
        host = self._host_key(url)
        ssl_retried = self._is_ssl_bypass_active(host)
        verify: bool | str = not ssl_retried
        attempt = 0
        previous_ua: str | None = None

        # Use cached client for connection pool reuse across requests
        client = self._get_sync_client()
        # Skip main loop when SSL bypass is already cached; go straight to the bypass block.
        while attempt < _MAX_RETRIES and not ssl_retried:
            ua = self._next_user_agent(previous=previous_ua)
            previous_ua = ua
            self._ensure_circuit_closed(host)
            sleep_for = self._reserve_domain_slot(host)
            if sleep_for > 0:
                time.sleep(sleep_for)
                self._ensure_circuit_closed(host)
            start_time = time.perf_counter()

            try:
                # BUG FIX: Use streaming to check size incrementally
                with client.stream("GET", url, headers=self._build_headers(ua, host_header=host_header)) as response:
                    response_host = self._effective_host(str(response.url), host)
                    # Check Content-Length header first (fast path)
                    if self.max_response_size is not None:
                        content_length = response.headers.get("content-length")
                        parsed_length = _parse_content_length(content_length)
                        if parsed_length is not None and parsed_length > self.max_response_size:
                            raise ValueError(
                                f"Response size {parsed_length} exceeds max_response_size {self.max_response_size}"
                            )

                    # Check for retryable status codes BEFORE raising_for_status
                    if response.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES - 1:
                        retry_delay = _retry_delay_seconds(response, attempt)
                        if response.status_code in {403, 429}:
                            self._record_soft_throttle(response_host, retry_delay)
                        else:
                            self._defer_host(response_host, retry_delay)
                            self._record_failure(response_host)
                        try:
                            response.read()
                        except Exception:
                            logger.debug("Failed to read response body during retry", exc_info=True)
                        time.sleep(retry_delay)
                        attempt += 1
                        continue

                    response.raise_for_status()
                    _validate_content_type(response)

                    # Stream content with size checking
                    sync_chunks: list[bytes] = []
                    sync_total_size = 0
                    for chunk in response.iter_bytes():
                        sync_total_size += len(chunk)
                        if (
                            self.max_response_size is not None
                            and sync_total_size > self.max_response_size
                        ):
                            raise ValueError(
                                f"Response content size {sync_total_size} exceeds max_response_size {self.max_response_size}"
                            )
                        sync_chunks.append(chunk)

                    sync_content = b"".join(sync_chunks)

                elapsed_ms = (time.perf_counter() - start_time) * 1000

                self._record_success(response_host)

                # Decode content using charset from Content-Type header
                encoding = response.charset_encoding or "utf-8"
                try:
                    html = sync_content.decode(encoding)
                except (UnicodeDecodeError, LookupError):
                    html = sync_content.decode("latin-1")

                return FetchResult(
                    html=html,
                    url=url,
                    status_code=response.status_code,
                    final_url=str(response.url),
                    headers=response.headers,
                    timing_ms=elapsed_ms,
                    metadata={"fetcher": "httpx", "user_agent": ua, "attempt": attempt + 1},
                )

            except httpx.HTTPStatusError as exc:
                last_exc = exc
                status_code = exc.response.status_code
                response_host = self._effective_host(str(exc.response.url), host)
                # Early-fail for non-retryable status codes
                if status_code not in _RETRYABLE_STATUS:
                    self._record_failure(response_host)
                    raise
                # On the final attempt, record the appropriate throttle/failure
                # and re-raise without sleeping (no more retries remain).
                if attempt >= _MAX_RETRIES - 1:
                    retry_delay = _retry_delay_seconds(exc.response, attempt)
                    if status_code in {403, 429}:
                        self._record_soft_throttle(response_host, retry_delay)
                    else:
                        self._defer_host(response_host, retry_delay)
                        self._record_failure(response_host)
                    raise
                # Retryable status: record soft throttle and continue
                retry_delay = _retry_delay_seconds(exc.response, attempt)
                if status_code in {403, 429}:
                    self._record_soft_throttle(response_host, retry_delay)
                else:
                    self._defer_host(response_host, retry_delay)
                    self._record_failure(response_host)
                time.sleep(retry_delay)
                attempt += 1
                continue

            except DomainCircuitOpenError:
                raise

            except Exception as exc:
                last_exc = exc
                self._record_failure(host)

                # On SSL errors, only bypass if explicitly configured and warn
                if (
                    not ssl_retried
                    and self.allow_ssl_bypass
                    and ("SSL" in type(exc).__name__ or "certificate" in str(exc).lower())
                ):
                    logger.warning(
                        "SSL verification failed for %s, retrying with verify=False (insecure). "
                        "This bypass is insecure and should only be used for testing.",
                        url,
                    )
                    verify = False  # pragma: no cover
                    ssl_retried = True  # pragma: no cover
                    # Count the SSL failure as a consumed attempt before switching to bypass.
                    attempt += 1
                    break  # pragma: no cover
                # Non-SSL error or SSL bypass disabled: increment attempt and retry
                attempt += 1

        # If we need to retry with SSL bypass, recreate client
        if ssl_retried and verify is False:
            consumed_attempts = attempt
            remaining_attempts = max(0, _MAX_RETRIES - consumed_attempts)
            if remaining_attempts == 0:
                if last_exc is not None:
                    raise last_exc
                raise RuntimeError(
                    f"SSL bypass fetch failed for {url} after {_MAX_RETRIES} attempts"
                )
            ssl_last_exc: Exception | None = None
            for ssl_attempt in range(remaining_attempts):
                ssl_attempt_num = ssl_attempt + 1  # 1-based for logging
                total_attempt_num = consumed_attempts + ssl_attempt_num
                with httpx.Client(
                    timeout=self.timeout,
                    follow_redirects=self.follow_redirects,
                    max_redirects=self.max_redirects,
                    verify=False,  # SSL bypass — insecure, only for testing
                    trust_env=False,
                ) as client:
                    ua = self._next_user_agent(previous=previous_ua)
                    previous_ua = ua
                    self._ensure_circuit_closed(host)
                    sleep_for = self._reserve_domain_slot(host)
                    if sleep_for > 0:
                        time.sleep(sleep_for)
                        self._ensure_circuit_closed(host)
                    start_time = time.perf_counter()

                    try:
                        # BUG FIX: Use streaming to check size incrementally
                        with client.stream("GET", url, headers=self._build_headers(ua, host_header=host_header)) as response:
                            response_host = self._effective_host(str(response.url), host)

                            if self.max_response_size is not None:
                                content_length = response.headers.get("content-length")
                                parsed_length = _parse_content_length(content_length)
                                if (
                                    parsed_length is not None
                                    and parsed_length > self.max_response_size
                                ):
                                    raise ValueError(
                                        f"Response size {parsed_length} exceeds max_response_size {self.max_response_size}"
                                    )

                            # Check for retryable status codes BEFORE raising_for_status
                            if (
                                response.status_code in _RETRYABLE_STATUS
                                and ssl_attempt < remaining_attempts - 1
                            ):
                                retry_delay = _retry_delay_seconds(response, ssl_attempt)
                                if response.status_code in {403, 429}:
                                    self._record_soft_throttle(response_host, retry_delay)
                                else:
                                    self._defer_host(response_host, retry_delay)
                                    self._record_failure(response_host)
                                # Runtime fix (L1): drain the streaming body before
                                # continuing so the connection is released back to
                                # the pool instead of being abandoned half-read.
                                try:
                                    for _ in response.iter_bytes():
                                        pass
                                except Exception:
                                    pass
                                time.sleep(retry_delay)
                                continue

                            response.raise_for_status()
                            _validate_content_type(response)

                            # Stream content with size checking
                            ssl_sync_chunks: list[bytes] = []
                            ssl_sync_total = 0
                            for chunk in response.iter_bytes():
                                ssl_sync_total += len(chunk)
                                if (
                                    self.max_response_size is not None
                                    and ssl_sync_total > self.max_response_size
                                ):
                                    raise ValueError(
                                        f"Response content size {ssl_sync_total} exceeds max_response_size {self.max_response_size}"
                                    )
                                ssl_sync_chunks.append(chunk)

                            ssl_sync_content = b"".join(ssl_sync_chunks)

                        elapsed_ms = (time.perf_counter() - start_time) * 1000
                        self._record_success(response_host)
                        with self._ssl_bypass_lock:
                            self._ssl_bypass_hosts[host] = time.monotonic() + self._ssl_bypass_ttl

                        # Decode content using charset from Content-Type header
                        encoding = response.charset_encoding or "utf-8"
                        try:
                            html = ssl_sync_content.decode(encoding)
                        except (UnicodeDecodeError, LookupError):
                            html = ssl_sync_content.decode("latin-1")

                        return FetchResult(
                            html=html,
                            url=url,
                            status_code=response.status_code,
                            final_url=str(response.url),
                            headers=response.headers,
                            timing_ms=elapsed_ms,
                            metadata={
                                "fetcher": "httpx",
                                "user_agent": ua,
                                "attempt": total_attempt_num,
                                "ssl_bypass": True,
                            },
                        )

                    except httpx.HTTPStatusError as exc:
                        ssl_last_exc = exc
                        status_code = exc.response.status_code
                        retry_delay = _retry_delay_seconds(exc.response, ssl_attempt)
                        response_host = self._effective_host(str(exc.response.url), host)

                        if (
                            status_code in _RETRYABLE_STATUS
                            and ssl_attempt < remaining_attempts - 1
                        ):
                            logger.warning(
                                "SSL bypass attempt %d/%d failed with %d for %s, retrying in %.1fs",
                                ssl_attempt_num,
                                remaining_attempts,
                                status_code,
                                url,
                                retry_delay,
                            )
                            if status_code in {403, 429}:
                                self._record_soft_throttle(response_host, retry_delay)
                            else:
                                self._defer_host(response_host, retry_delay)
                                self._record_failure(response_host)
                            time.sleep(retry_delay)
                            continue
                        # Non-retryable or exhausted attempts: still feed the circuit breaker.
                        self._record_failure(response_host)
                        raise

                    except Exception as exc:
                        ssl_last_exc = exc
                        self._record_failure(host)
                        if ssl_attempt < remaining_attempts - 1:
                            retry_delay = float(min(0.5 * (2**ssl_attempt), 2.0))
                            logger.warning(
                                "SSL bypass attempt %d/%d failed for %s: %s, retrying in %.1fs",
                                ssl_attempt_num,
                                remaining_attempts,
                                url,
                                type(exc).__name__,
                                retry_delay,
                            )
                            time.sleep(retry_delay)
                            continue
                        raise

            # All SSL bypass retries exhausted
            if ssl_last_exc is not None:
                raise ssl_last_exc
            raise RuntimeError(f"SSL bypass fetch failed for {url} after {_MAX_RETRIES} attempts")

        if last_exc is not None:
            raise last_exc
        raise RuntimeError(
            f"Fetch failed for {url} after {_MAX_RETRIES} attempts"
        )  # pragma: no cover
