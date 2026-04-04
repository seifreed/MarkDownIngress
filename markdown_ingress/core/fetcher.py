"""
HTTP fetcher module - Fast mode implementation
"""

import asyncio
import logging
import os
import random
import time
from datetime import datetime, timezone
from threading import Lock, RLock
from urllib.parse import urlsplit

import httpx

from markdown_ingress.core.ssrf import validate_http_url_no_ssrf
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
_HTML_CONTENT_TYPE_PREFIXES = (
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


class UnsupportedContentTypeError(ValueError):
    """Raised when the fetched response is not HTML-like content."""


class DomainCircuitOpenError(RuntimeError):
    """Raised when a per-domain circuit breaker is open."""


def _is_supported_html_content_type(content_type: str | None) -> bool:
    """Return whether a response Content-Type looks HTML-compatible."""
    if content_type is None:
        return True  # Missing header - assume HTML
    if not content_type.strip():
        return False  # Empty Content-Type - reject as invalid
    normalized = content_type.split(";", 1)[0].strip().lower()
    return normalized in _HTML_CONTENT_TYPE_PREFIXES


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
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        retry_at = datetime.strptime(raw, "%a, %d %b %Y %H:%M:%S GMT").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())


def _retry_delay_seconds(response: httpx.Response, attempt: int) -> float:
    """Choose a retry delay using Retry-After when available, else status-aware backoff."""
    retry_after = _parse_retry_after(response.headers.get("retry-after"))
    if retry_after is not None:
        return float(min(retry_after, 10.0))
    if response.status_code == 429:
        return float(min(1.5 * (2**attempt), 10.0))
    return float(min(0.5 * (attempt + 1), 2.0))


def _decode_response(response: httpx.Response) -> str:
    """
    Decode response bytes to string with robust encoding fallback.
    Uses charset from Content-Type header, falls back to latin-1 (lossless).
    """
    encoding = response.charset_encoding or "utf-8"
    try:
        return response.content.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        # latin-1 can decode any byte sequence without errors
        return response.content.decode("latin-1")


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
        allow_local_urls: bool | None = None,
        domain_state_ttl: float = _DEFAULT_DOMAIN_STATE_TTL,
        max_hosts: int = _DEFAULT_MAX_HOSTS,
        max_response_size: int | None = _DEFAULT_MAX_RESPONSE_SIZE,
        failure_decay_seconds: float | None = 300.0,
    ):
        self.timeout = timeout
        # If a specific UA is given, use it; otherwise rotate through ADVANCED_USER_AGENTS
        self._fixed_ua = user_agent
        self.rotate_ua = rotate_ua and user_agent is None
        self.follow_redirects = follow_redirects
        self.max_redirects = max_redirects
        self.domain_request_interval = max(0.0, domain_request_interval)
        self.circuit_breaker_threshold = max(1, circuit_breaker_threshold)
        self.circuit_breaker_open_seconds = max(1.0, circuit_breaker_open_seconds)
        self.allow_ssl_bypass = allow_ssl_bypass
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
        # Use RLock to allow _apply_failure_decay to acquire lock even when called from
        # methods that already hold the lock (e.g., _ensure_circuit_closed, _record_failure)
        self._failure_lock = RLock()
        self._failures_by_host: dict[str, int] = {}
        self._failure_first_seen: dict[str, float] = {}  # Track when failures started
        self._open_until_by_host: dict[str, float] = {}
        self._last_cleanup = time.monotonic()
        self._cleanup_lock = Lock()  # Protects _last_cleanup for atomic check-and-set
        # Cached HTTP clients for connection pool reuse across requests
        self._async_client: httpx.AsyncClient | None = None
        self._sync_client: httpx.Client | None = None
        self._client_lock = Lock()  # Protects lazy client creation
        self._async_client_lock_guard = Lock()
        self._async_client_lock: asyncio.Lock | None = None

    @property
    def user_agent(self) -> str:
        """Return fixed UA or a random legitimate browser UA."""
        if self._fixed_ua:
            return self._fixed_ua
        return random.choice(ADVANCED_USER_AGENTS)

    def _get_sync_client(self) -> httpx.Client:
        """Get or create the cached synchronous HTTP client for connection pooling."""
        if self._sync_client is None:
            with self._client_lock:
                if self._sync_client is None:
                    self._sync_client = httpx.Client(
                        timeout=self.timeout,
                        follow_redirects=self.follow_redirects,
                        max_redirects=self.max_redirects,
                        verify=True,
                        trust_env=False,
                    )
        return self._sync_client

    def _get_async_client_lock(self) -> asyncio.Lock:
        """Create the async lock lazily inside an active event loop."""
        lock = self._async_client_lock
        if lock is None:
            with self._async_client_lock_guard:
                if self._async_client_lock is None:
                    self._async_client_lock = asyncio.Lock()
                lock = self._async_client_lock
        return lock

    async def _get_async_client(self) -> httpx.AsyncClient:
        """Get or create the cached async HTTP client for connection pooling."""
        if self._async_client is None:
            async with self._get_async_client_lock():
                if self._async_client is None:
                    self._async_client = httpx.AsyncClient(
                        timeout=self.timeout,
                        follow_redirects=self.follow_redirects,
                        max_redirects=self.max_redirects,
                        verify=True,
                        trust_env=False,
                    )
        return self._async_client

    def close(self) -> None:
        """Close the synchronous HTTP client and release resources."""
        with self._client_lock:
            if self._sync_client is not None:
                self._sync_client.close()
                self._sync_client = None

    async def aclose(self) -> None:
        """Close the async HTTP client and release resources.

        Thread-safe: Uses async lock to prevent race conditions when
        multiple coroutines try to close the client simultaneously.
        """
        async with self._get_async_client_lock():
            if self._async_client is not None:
                await self._async_client.aclose()
                self._async_client = None

    def __del__(self) -> None:
        """Cleanup on destruction."""
        try:
            self.close()
        except Exception:
            pass

    def _build_headers(self, ua: str) -> dict:
        """Build safe browser-like headers for a given User-Agent."""
        headers = dict(_SAFE_HEADERS)
        headers["User-Agent"] = ua
        return headers

    @staticmethod
    def _resolve_allow_local_urls(allow_local_urls: bool | None) -> bool:
        """Resolve explicit allow-local setting with an env-based opt-in fallback."""
        if allow_local_urls is not None:
            return bool(allow_local_urls)

        raw = os.getenv("MDI_ALLOW_LOCAL_URLS")
        if raw is None:
            return False

        normalized = raw.strip().lower()
        if normalized in ("true", "1", "yes", "on", "enabled"):
            return True
        if normalized in ("false", "0", "no", "off", "disabled"):
            return False

        logger.warning(
            "Invalid MDI_ALLOW_LOCAL_URLS=%r for Fetcher; defaulting to False.",
            raw,
        )
        return False

    @staticmethod
    def _validate_url(url: str, *, allow_local_urls: bool = False) -> str:
        """Validate URL and return normalized form.

        Raises:
            ValueError: If URL is invalid or uses disallowed scheme.
        """
        return validate_http_url_no_ssrf(url, allow_local=allow_local_urls)

    @staticmethod
    def _host_key(url: str) -> str:
        """Normalize URL host for per-domain throttling.

        Assumes URL has already been validated by _validate_url().
        """
        return (urlsplit(url).hostname or "").lower()

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

        # CRITICAL: Acquire _domain_lock BEFORE _failure_lock to prevent ABBA deadlock.
        # This ordering must match all other methods that need both locks.
        with self._domain_lock:
            # Clean up _next_allowed_by_host
            stale_hosts = [
                host for host, ts in self._domain_state_timestamp.items()
                if now - ts > self.domain_state_ttl
            ]
            stale_hosts_set = set(stale_hosts)
            for host in stale_hosts:
                self._next_allowed_by_host.pop(host, None)
                self._domain_state_timestamp.pop(host, None)

            # Enforce max_hosts limit by evicting oldest entries
            if len(self._next_allowed_by_host) > self.max_hosts:
                # Sort by timestamp and remove oldest
                sorted_hosts = sorted(
                    self._domain_state_timestamp.items(),
                    key=lambda x: x[1],
                )
                evict_count = len(self._next_allowed_by_host) - self.max_hosts
                for host, _ in sorted_hosts[:evict_count]:
                    self._next_allowed_by_host.pop(host, None)
                    self._domain_state_timestamp.pop(host, None)
                    stale_hosts_set.add(host)

            with self._failure_lock:
                # Clean up failure tracking - synchronize with domain state
                # Remove hosts that were evicted from domain state AND stale hosts
                for host in stale_hosts_set:
                    self._failures_by_host.pop(host, None)
                    self._failure_first_seen.pop(host, None)
                    self._open_until_by_host.pop(host, None)

                # Also clean up any remaining stale hosts from failure tracking
                remaining_stale = [
                    host for host, ts in self._failure_first_seen.items()
                    if now - ts > self.domain_state_ttl
                ]
                for host in remaining_stale:
                    self._failures_by_host.pop(host, None)
                    self._failure_first_seen.pop(host, None)
                    self._open_until_by_host.pop(host, None)

    def _apply_failure_decay(self, host: str) -> int:
        """Apply time-based decay to failure counts and return current count.

        This method is thread-safe and acquires _failure_lock internally.
        Callers do NOT need to hold the lock when calling this method.
        """
        if self.failure_decay_seconds is None or self.failure_decay_seconds <= 0:
            with self._failure_lock:
                return self._failures_by_host.get(host, 0)

        now = time.monotonic()
        with self._failure_lock:
            first_seen = self._failure_first_seen.get(host, now)
            current = self._failures_by_host.get(host, 0)

            if current > 0 and first_seen:
                # Decay: reduce failures proportionally to time elapsed
                elapsed = now - first_seen
                if elapsed > self.failure_decay_seconds:
                    # Reset failures after decay period
                    self._failures_by_host[host] = 0
                    self._failure_first_seen[host] = now
                    return 0
                # Partial decay: reduce by half for each decay period elapsed
                decay_factor = 0.5 ** (elapsed / self.failure_decay_seconds)
                decayed = int(current * decay_factor)
                self._failures_by_host[host] = decayed
                return decayed
            return current

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
            open_until = self._open_until_by_host.get(host, 0.0)
            # Apply decay before checking circuit
            self._apply_failure_decay(host)
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
        with self._failure_lock:
            # Track when failures first started for this host
            if host not in self._failure_first_seen:
                self._failure_first_seen[host] = time.monotonic()

            # Apply decay before incrementing
            current = self._apply_failure_decay(host)
            current += 1
            self._failures_by_host[host] = current

            if current >= self.circuit_breaker_threshold:
                self._open_until_by_host[host] = time.monotonic() + self.circuit_breaker_open_seconds
                # Reset failure count when circuit opens to allow recovery
                self._failures_by_host[host] = 0
                self._failure_first_seen.pop(host, None)

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
        Retries up to _MAX_RETRIES times with a different User-Agent on 403/429/503.

        Args:
            url: Target URL to fetch

        Returns:
            FetchResult with HTML content and metadata

        Raises:
            httpx.HTTPError: On network/HTTP errors after all retries
            ValueError: If URL is invalid or uses disallowed scheme
        """
        # Validate URL before processing to prevent SSRF and bypass attacks
        url = self._validate_url(url, allow_local_urls=self.allow_local_urls)

        last_exc: Exception | None = None
        verify: bool | str = True
        host = self._host_key(url)
        attempt = 0
        ssl_retried = False

        # Use cached client for connection pool reuse across requests
        client = await self._get_async_client()
        while attempt < _MAX_RETRIES:
            ua = self.user_agent
            self._ensure_circuit_closed(host)
            sleep_for = self._reserve_domain_slot(host)
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
                # BUG FIX: Re-check circuit breaker after sleep - state may have changed
                # during the sleep period (another coroutine could have triggered failures)
                self._ensure_circuit_closed(host)
            start_time = time.perf_counter()

            try:
                response = await client.get(url, headers=self._build_headers(ua))
                response_host = self._effective_host(str(response.url), host)

                # Check response size to prevent DoS
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
                    await asyncio.sleep(retry_delay)
                    attempt += 1
                    continue

                response.raise_for_status()
                _validate_content_type(response)

                # Check actual content size after fetching
                if self.max_response_size is not None and len(response.content) > self.max_response_size:
                    raise ValueError(
                        f"Response content size {len(response.content)} exceeds max_response_size {self.max_response_size}"
                    )

                elapsed_ms = (time.perf_counter() - start_time) * 1000

                self._record_success(response_host)
                return FetchResult(
                    html=_decode_response(response),
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
                # Retryable status: record failure and continue
                retry_delay = _retry_delay_seconds(exc.response, attempt)
                if status_code in {403, 429}:
                    self._record_soft_throttle(response_host, retry_delay)
                else:
                    self._defer_host(response_host, retry_delay)
                    self._record_failure(response_host)
                await asyncio.sleep(retry_delay)
                attempt += 1
                continue

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
                    # Exit main retry loop to start SSL bypass (attempt not incremented)
                    break  # pragma: no cover
                # Non-SSL error or SSL bypass disabled: increment attempt and retry
                attempt += 1

        # If we need to retry with SSL bypass, recreate client
        if ssl_retried and verify is False:
            consumed_attempts = attempt + 1
            remaining_attempts = max(0, _MAX_RETRIES - consumed_attempts)
            if remaining_attempts == 0:
                if last_exc is not None:
                    raise last_exc
                raise RuntimeError(f"SSL bypass fetch failed for {url} after {_MAX_RETRIES} attempts")
            ssl_last_exc: Exception | None = None
            for ssl_attempt in range(remaining_attempts):
                ssl_attempt_num = ssl_attempt + 1  # 1-based for logging
                total_attempt_num = consumed_attempts + ssl_attempt_num
                async with httpx.AsyncClient(
                    timeout=self.timeout,
                    follow_redirects=self.follow_redirects,
                    max_redirects=self.max_redirects,
                    verify=False,
                    trust_env=False,
                ) as client:
                    ua = self.user_agent
                    self._ensure_circuit_closed(host)
                    sleep_for = self._reserve_domain_slot(host)
                    if sleep_for > 0:
                        await asyncio.sleep(sleep_for)
                        self._ensure_circuit_closed(host)
                    start_time = time.perf_counter()

                    try:
                        response = await client.get(url, headers=self._build_headers(ua))
                        response_host = self._effective_host(str(response.url), host)

                        if self.max_response_size is not None:
                            content_length = response.headers.get("content-length")
                            parsed_length = _parse_content_length(content_length)
                            if parsed_length is not None and parsed_length > self.max_response_size:
                                raise ValueError(
                                    f"Response size {parsed_length} exceeds max_response_size {self.max_response_size}"
                                )

                        response.raise_for_status()
                        _validate_content_type(response)

                        if self.max_response_size is not None and len(response.content) > self.max_response_size:
                            raise ValueError(
                                f"Response content size {len(response.content)} exceeds max_response_size {self.max_response_size}"
                            )

                        elapsed_ms = (time.perf_counter() - start_time) * 1000
                        self._record_success(response_host)
                        return FetchResult(
                            html=_decode_response(response),
                            url=url,
                            status_code=response.status_code,
                            final_url=str(response.url),
                            headers=response.headers,
                            timing_ms=elapsed_ms,
                            metadata={"fetcher": "httpx", "user_agent": ua, "attempt": total_attempt_num, "ssl_bypass": True},
                        )

                    except httpx.HTTPStatusError as exc:
                        ssl_last_exc = exc
                        status_code = exc.response.status_code
                        retry_delay = _retry_delay_seconds(exc.response, ssl_attempt)  # 0-indexed

                        if status_code in _RETRYABLE_STATUS and ssl_attempt < remaining_attempts - 1:
                            logger.warning(
                                "SSL bypass attempt %d/%d failed with %d for %s, retrying in %.1fs",
                                ssl_attempt_num,
                                remaining_attempts,
                                status_code,
                                url,
                                retry_delay,
                            )
                            if status_code in {403, 429}:
                                self._record_soft_throttle(
                                    self._effective_host(str(exc.response.url), host), retry_delay
                                )
                            else:
                                response_host = self._effective_host(str(exc.response.url), host)
                                self._defer_host(response_host, retry_delay)
                                self._record_failure(response_host)
                            await asyncio.sleep(retry_delay)
                            continue
                        raise

                    except Exception as exc:
                        ssl_last_exc = exc
                        self._record_failure(host)
                        if ssl_attempt < remaining_attempts - 1:
                            retry_delay = float(min(0.5 * ssl_attempt_num, 2.0))
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
        raise RuntimeError(f"Fetch failed for {url} after {_MAX_RETRIES} attempts")  # pragma: no cover

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
        # Validate URL before processing to prevent SSRF and bypass attacks
        url = self._validate_url(url, allow_local_urls=self.allow_local_urls)

        last_exc: Exception | None = None
        verify: bool | str = True
        host = self._host_key(url)
        attempt = 0
        ssl_retried = False

        # Use cached client for connection pool reuse across requests
        client = self._get_sync_client()
        while attempt < _MAX_RETRIES:
            ua = self.user_agent
            self._ensure_circuit_closed(host)
            sleep_for = self._reserve_domain_slot(host)
            if sleep_for > 0:
                time.sleep(sleep_for)
                self._ensure_circuit_closed(host)
            start_time = time.perf_counter()

            try:
                response = client.get(url, headers=self._build_headers(ua))
                response_host = self._effective_host(str(response.url), host)

                # Check response size to prevent DoS
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
                    time.sleep(retry_delay)
                    attempt += 1
                    continue

                response.raise_for_status()
                _validate_content_type(response)

                # Check actual content size after fetching
                if self.max_response_size is not None and len(response.content) > self.max_response_size:
                    raise ValueError(
                        f"Response content size {len(response.content)} exceeds max_response_size {self.max_response_size}"
                    )

                elapsed_ms = (time.perf_counter() - start_time) * 1000

                self._record_success(response_host)
                return FetchResult(
                    html=_decode_response(response),
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
                # Retryable status: record failure and continue
                retry_delay = _retry_delay_seconds(exc.response, attempt)
                if status_code in {403, 429}:
                    self._record_soft_throttle(response_host, retry_delay)
                else:
                    self._defer_host(response_host, retry_delay)
                    self._record_failure(response_host)
                time.sleep(retry_delay)
                attempt += 1
                continue

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
                    # Exit main retry loop to start SSL bypass (attempt not incremented)
                    break  # pragma: no cover
                # Non-SSL error or SSL bypass disabled: increment attempt and retry
                attempt += 1

        # If we need to retry with SSL bypass, recreate client
        if ssl_retried and verify is False:
            consumed_attempts = attempt + 1
            remaining_attempts = max(0, _MAX_RETRIES - consumed_attempts)
            if remaining_attempts == 0:
                if last_exc is not None:
                    raise last_exc
                raise RuntimeError(f"SSL bypass fetch failed for {url} after {_MAX_RETRIES} attempts")
            ssl_last_exc: Exception | None = None
            for ssl_attempt in range(remaining_attempts):
                ssl_attempt_num = ssl_attempt + 1  # 1-based for logging
                total_attempt_num = consumed_attempts + ssl_attempt_num
                with httpx.Client(
                    timeout=self.timeout,
                    follow_redirects=self.follow_redirects,
                    max_redirects=self.max_redirects,
                    verify=False,
                    trust_env=False,
                ) as client:
                    ua = self.user_agent
                    self._ensure_circuit_closed(host)
                    sleep_for = self._reserve_domain_slot(host)
                    if sleep_for > 0:
                        time.sleep(sleep_for)
                        self._ensure_circuit_closed(host)
                    start_time = time.perf_counter()

                    try:
                        response = client.get(url, headers=self._build_headers(ua))
                        response_host = self._effective_host(str(response.url), host)

                        if self.max_response_size is not None:
                            content_length = response.headers.get("content-length")
                            parsed_length = _parse_content_length(content_length)
                            if parsed_length is not None and parsed_length > self.max_response_size:
                                raise ValueError(
                                    f"Response size {parsed_length} exceeds max_response_size {self.max_response_size}"
                                )

                        # Check for retryable status codes BEFORE raising_for_status
                        if response.status_code in _RETRYABLE_STATUS and ssl_attempt < remaining_attempts - 1:
                            retry_delay = _retry_delay_seconds(response, ssl_attempt)
                            if response.status_code in {403, 429}:
                                self._record_soft_throttle(response_host, retry_delay)
                            else:
                                self._defer_host(response_host, retry_delay)
                                self._record_failure(response_host)
                            time.sleep(retry_delay)
                            continue

                        response.raise_for_status()
                        _validate_content_type(response)

                        if self.max_response_size is not None and len(response.content) > self.max_response_size:
                            raise ValueError(
                                f"Response content size {len(response.content)} exceeds max_response_size {self.max_response_size}"
                            )

                        elapsed_ms = (time.perf_counter() - start_time) * 1000
                        self._record_success(response_host)
                        return FetchResult(
                            html=_decode_response(response),
                            url=url,
                            status_code=response.status_code,
                            final_url=str(response.url),
                            headers=response.headers,
                            timing_ms=elapsed_ms,
                            metadata={"fetcher": "httpx", "user_agent": ua, "attempt": total_attempt_num, "ssl_bypass": True},
                        )

                    except httpx.HTTPStatusError as exc:
                        ssl_last_exc = exc
                        status_code = exc.response.status_code
                        retry_delay = _retry_delay_seconds(exc.response, ssl_attempt)

                        if status_code in _RETRYABLE_STATUS and ssl_attempt < remaining_attempts - 1:
                            logger.warning(
                                "SSL bypass attempt %d/%d failed with %d for %s, retrying in %.1fs",
                                ssl_attempt_num,
                                remaining_attempts,
                                status_code,
                                url,
                                retry_delay,
                            )
                            if status_code in {403, 429}:
                                self._record_soft_throttle(
                                    self._effective_host(str(exc.response.url), host), retry_delay
                                )
                            else:
                                response_host = self._effective_host(str(exc.response.url), host)
                                self._defer_host(response_host, retry_delay)
                                self._record_failure(response_host)
                            time.sleep(retry_delay)
                            continue
                        raise

                    except Exception as exc:
                        ssl_last_exc = exc
                        self._record_failure(host)
                        if ssl_attempt < remaining_attempts - 1:
                            retry_delay = float(min(0.5 * ssl_attempt_num, 2.0))
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
        raise RuntimeError(f"Fetch failed for {url} after {_MAX_RETRIES} attempts")  # pragma: no cover
