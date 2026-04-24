"""HTTP fetcher adapter using httpx - fast mode implementation."""

import asyncio
import logging
import random
import ssl
import time
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any, NamedTuple
from urllib.parse import urlsplit

import httpx

from markdown_ingress.core.interfaces import IFetcher
from markdown_ingress.core.policy import DomainCircuitOpenError, UnsupportedContentTypeError
from markdown_ingress.core.ssrf import (
    normalize_hostname,
    resolve_allow_local_urls,
    validate_http_url_no_ssrf,
)
from markdown_ingress.core.stealth.browser_config import ADVANCED_USER_AGENTS
from markdown_ingress.models import FetchResult

logger = logging.getLogger(__name__)

_SAFE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "max-age=0",
    "DNT": "1",
    "Upgrade-Insecure-Requests": "1",
}

_RETRYABLE_STATUS = {403, 429, 503}
_FOLLOW_REDIRECT_STATUS = {301, 302, 303, 307, 308}
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

_DEFAULT_DOMAIN_STATE_TTL = 3600
_DEFAULT_MAX_HOSTS = 10000
_DEFAULT_MAX_RESPONSE_SIZE = 10 * 1024 * 1024

_rng = random.SystemRandom()


class ResponseSizeLimitError(ValueError):
    """Raised when a response exceeds the configured fetch size limit."""


class _PreparedRequest(NamedTuple):
    transport_url: str
    logical_url: str
    host_header: str | None
    sni_hostname: str | None
    logical_host: str


def _format_host_header(hostname: str, port: int | None, scheme: str) -> str:
    host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    default_port = 443 if scheme.lower() == "https" else 80
    return f"{host}:{port}" if port is not None and port != default_port else host



def _is_supported_html_content_type(content_type: str | None) -> bool:
    if content_type is None:
        logger.debug("Response has no Content-Type header; rejecting as non-HTML")
        return False
    if not content_type.strip():
        logger.debug("Response has empty Content-Type header; rejecting as non-HTML")
        return False
    normalized = content_type.split(";", 1)[0].strip().lower()
    return normalized in _HTML_CONTENT_TYPES


def _validate_content_type(response: httpx.Response) -> None:
    content_type = response.headers.get("content-type")
    if _is_supported_html_content_type(content_type):
        return
    raise UnsupportedContentTypeError(
        f"Unsupported content type for HTML ingestion: {content_type or 'unknown'}"
    )


def _parse_retry_after(value: str | None) -> float | None:
    from email.utils import parsedate_to_datetime

    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        return max(0.25, float(raw))
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(raw)
    except Exception:
        return None
    return max(0.25, (retry_at - datetime.now(UTC)).total_seconds())


def _retry_delay_seconds(response: httpx.Response, attempt: int) -> float:
    retry_after = _parse_retry_after(response.headers.get("retry-after"))
    if retry_after is not None:
        return float(min(retry_after, 10.0))
    if response.status_code == 429:
        return float(min(1.5 * (2**attempt), 10.0))
    return float(min(0.5 * (attempt + 1), 2.0))


def _parse_content_length(content_length: str | None) -> int | None:
    if not content_length:
        return None
    try:
        value = int(content_length)
        return value if value >= 0 else None
    except ValueError:
        logger.warning("Malformed Content-Length header: %s", content_length)
        return None


def _host_soft_throttle_delay(host: str, base_delay: float) -> float:
    normalized = host.lower()
    for suffix, (multiplier, minimum_delay) in _HOST_SOFT_THROTTLE_HINTS.items():
        if normalized == suffix or normalized.endswith(f".{suffix}"):
            return max(base_delay * multiplier, minimum_delay)
    return base_delay


class Fetcher(IFetcher):
    """HTTP fetcher for fast mode (no JS rendering)."""

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
        self._async_client: httpx.AsyncClient | None = None
        self._sync_client: httpx.Client | None = None
        self._client_lock = Lock()
        self._async_client_lock_guard = Lock()
        self._async_client_lock: asyncio.Lock | None = None
        self._async_client_lock_loop: asyncio.AbstractEventLoop | None = None
        self._pending_async_closes: list[asyncio.Task] = []
        self._pending_closes_lock = Lock()
        self._ssl_bypass_hosts: dict[str, float] = {}
        self._ssl_bypass_ttl: float = 300.0
        self._ssl_bypass_lock = Lock()

    def _is_ssl_bypass_active(self, host: str) -> bool:
        with self._ssl_bypass_lock:
            expiry = self._ssl_bypass_hosts.get(host)
            if expiry is None:
                return False
            if time.monotonic() > expiry:
                self._ssl_bypass_hosts.pop(host, None)
                return False
            return True

    def _should_follow_redirect(self, response: httpx.Response) -> bool:
        if not self.follow_redirects:
            return False
        has_redirect_location = getattr(response, "has_redirect_location", None)
        if has_redirect_location is not None:
            return bool(has_redirect_location)
        return (
            response.status_code in _FOLLOW_REDIRECT_STATUS
            and bool(response.headers.get("location"))
        )

    def _is_redirect_response(self, response: httpx.Response) -> bool:
        return (
            bool(getattr(response, "is_redirect", False))
            or response.status_code in _FOLLOW_REDIRECT_STATUS
        )

    def _prepare_request_url(self, url: str) -> _PreparedRequest:
        logical_url = str(url).strip()
        validated_url = self._validate_url(logical_url, allow_local_urls=self.allow_local_urls)
        original_parts = urlsplit(logical_url)
        original_hostname = original_parts.hostname or ""
        validated_hostname = urlsplit(validated_url).hostname or ""
        host_header: str | None = None
        sni_hostname: str | None = None
        if original_hostname and original_hostname != validated_hostname:
            host_header = _format_host_header(
                original_hostname,
                original_parts.port,
                original_parts.scheme,
            )
            sni_hostname = original_hostname
        logical_host = self._host_key(logical_url)
        return _PreparedRequest(
            validated_url,
            logical_url,
            host_header,
            sni_hostname,
            logical_host,
        )

    def _prepare_redirect_url(
        self,
        response: httpx.Response,
        logical_url: str,
        redirect_count: int,
    ) -> _PreparedRequest | None:
        location = response.headers.get("location")
        if not location:
            return None
        if redirect_count >= self.max_redirects:
            raise httpx.TooManyRedirects(
                f"Exceeded maximum allowed redirects: {self.max_redirects}",
                request=getattr(response, "request", None),
            )
        redirect_url = str(httpx.URL(logical_url).join(location))
        return self._prepare_request_url(redirect_url)

    def _build_ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        if self.ca_bundle:
            if Path(self.ca_bundle).is_dir():
                ctx.load_verify_locations(capath=self.ca_bundle)
            else:
                ctx.load_verify_locations(cafile=self.ca_bundle)
        return ctx

    @property
    def user_agent(self) -> str:
        if self._fixed_ua:
            return self._fixed_ua
        if not self.rotate_ua:
            if self._stable_ua is None:
                self._stable_ua = _rng.choice(self._ua_pool)
            return self._stable_ua
        return self._next_user_agent(previous=self._last_rotating_ua)

    def _next_user_agent(self, *, previous: str | None = None) -> str:
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
        if self._sync_client is None:
            with self._client_lock:
                if self._sync_client is None:
                    self._sync_client = httpx.Client(
                        timeout=self.timeout,
                        follow_redirects=False,
                        max_redirects=self.max_redirects,
                        verify=self._build_ssl_context(),
                        trust_env=False,
                    )
        return self._sync_client

    def _get_async_client_lock(self) -> asyncio.Lock:
        with self._async_client_lock_guard:
            try:
                current_loop = asyncio.get_running_loop()
            except RuntimeError:
                current_loop = None
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
        if self._async_client is None:
            async with self._get_async_client_lock():
                if self._async_client is None:
                    self._async_client = httpx.AsyncClient(
                        timeout=self.timeout,
                        follow_redirects=False,
                        max_redirects=self.max_redirects,
                        verify=self._build_ssl_context(),
                        trust_env=False,
                    )
        return self._async_client

    def close(self) -> None:
        with self._client_lock:
            if self._sync_client is not None:
                self._sync_client.close()
                self._sync_client = None
        with self._async_client_lock_guard:
            client_to_close = self._async_client
            self._async_client = None
            self._async_client_lock = None
        if client_to_close is None:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
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
                logger.debug("Failed to schedule async client close as background task")

    async def aclose(self) -> None:
        async with self._get_async_client_lock():
            if self._async_client is not None:
                await self._async_client.aclose()
                self._async_client = None
        with self._async_client_lock_guard:
            self._async_client_lock = None
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
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    async def __aenter__(self) -> "Fetcher":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

    def __del__(self) -> None:
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
        headers = dict(_SAFE_HEADERS)
        headers["User-Agent"] = ua
        if host_header is not None:
            headers["Host"] = host_header
        return headers

    @staticmethod
    def _resolve_allow_local_urls(allow_local_urls: bool | None) -> bool:
        return resolve_allow_local_urls(allow_local_urls)

    @staticmethod
    def _validate_url(url: str, *, allow_local_urls: bool = False) -> str:
        return validate_http_url_no_ssrf(
            url,
            allow_local=allow_local_urls,
            resolve_dns=True,
        )

    @staticmethod
    def _host_key(url: str) -> str:
        return normalize_hostname(urlsplit(url).hostname or "")

    @classmethod
    def _effective_host(cls, final_url: str | None, fallback_host: str) -> str:
        if not final_url:
            return fallback_host
        resolved = cls._host_key(final_url)
        return resolved or fallback_host

    def _cleanup_domain_state(self) -> None:
        now = time.monotonic()
        with self._cleanup_lock:
            if now - self._last_cleanup < 60.0:
                return
            self._last_cleanup = now

        if self.domain_state_ttl <= 0:
            return

        with self._domain_lock:
            stale_hosts = [
                host
                for host, ts in self._domain_state_timestamp.items()
                if now - ts > self.domain_state_ttl
            ]
            stale_hosts_set = set(stale_hosts)
            for host in stale_hosts:
                self._next_allowed_by_host.pop(host, None)
                self._domain_state_timestamp.pop(host, None)

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

        with self._failure_lock:
            for host in stale_hosts_set:
                self._failures_by_host.pop(host, None)
                self._failure_first_seen.pop(host, None)
                self._open_until_by_host.pop(host, None)

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
        if self.failure_decay_seconds is None or self.failure_decay_seconds <= 0:
            return self._failures_by_host.get(host, 0)

        now = time.monotonic()
        first_seen = self._failure_first_seen.get(host, now)
        current = self._failures_by_host.get(host, 0)

        if current > 0 and first_seen:
            elapsed = now - first_seen
            if elapsed > self.failure_decay_seconds:
                self._failures_by_host[host] = 0
                self._failure_first_seen[host] = now
                self._open_until_by_host.pop(host, None)
                return 0
            decay_factor = 0.5 ** (elapsed / self.failure_decay_seconds)
            return int(round(current * decay_factor))
        return current

    def _apply_failure_decay(self, host: str) -> int:
        with self._failure_lock:
            return self._apply_failure_decay_locked(host)

    def _reserve_domain_slot(self, host: str) -> float:
        if not host:
            logger.warning("Empty host detected - rate limiting bypassed for malformed URL")
            return 0.0

        self._cleanup_domain_state()

        with self._domain_lock:
            now = time.monotonic()
            next_allowed = self._next_allowed_by_host.get(host, 0.0)
            slot = max(now, next_allowed)
            if self.domain_request_interval > 0.0:
                self._next_allowed_by_host[host] = slot + self.domain_request_interval
            else:
                self._next_allowed_by_host[host] = slot
            self._domain_state_timestamp[host] = now
            return max(0.0, slot - now)

    def _defer_host(self, host: str, delay_seconds: float) -> None:
        if not host or delay_seconds <= 0.0:
            return
        with self._domain_lock:
            now = time.monotonic()
            next_allowed = self._next_allowed_by_host.get(host, now)
            self._next_allowed_by_host[host] = max(next_allowed, now + delay_seconds)
            self._domain_state_timestamp[host] = now

    def _ensure_circuit_closed(self, host: str) -> None:
        if not host:
            return
        with self._failure_lock:
            self._apply_failure_decay_locked(host)
            open_until = self._open_until_by_host.get(host, 0.0)
            if open_until > time.monotonic():
                raise DomainCircuitOpenError(f"Circuit breaker open for host: {host}")

    def _record_success(self, host: str) -> None:
        if not host:
            return
        with self._failure_lock:
            self._failures_by_host.pop(host, None)
            self._failure_first_seen.pop(host, None)
            self._open_until_by_host.pop(host, None)

    def _record_failure(self, host: str) -> None:
        if not host:
            return

        now = time.monotonic()
        should_cleanup = False
        with self._cleanup_lock:
            if now - self._last_failure_cleanup > 60.0:
                should_cleanup = True
                self._last_failure_cleanup = now

        with self._failure_lock:
            if host not in self._failure_first_seen:
                self._failure_first_seen[host] = now

            decayed = self._apply_failure_decay_locked(host)
            new_count = decayed + 1
            self._failures_by_host[host] = new_count

            if new_count >= self.circuit_breaker_threshold:
                self._open_until_by_host[host] = now + self.circuit_breaker_open_seconds
                self._failures_by_host[host] = max(1, (self.circuit_breaker_threshold + 1) // 2)

            if should_cleanup:
                self._cleanup_stale_failures_locked(now)

    def _cleanup_stale_failures_locked(self, now: float) -> None:
        if self.domain_state_ttl <= 0:
            return

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
        if not host:
            return
        self._defer_host(host, _host_soft_throttle_delay(host, delay_seconds))
        with self._failure_lock:
            self._failures_by_host.pop(host, None)
            self._failure_first_seen.pop(host, None)
            self._open_until_by_host.pop(host, None)

    @staticmethod
    def _decode_content(content: bytes, charset_encoding: str | None) -> str:
        encoding = charset_encoding or "utf-8"
        try:
            return content.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            return content.decode("utf-8", errors="replace")

    def _make_fetch_result(
        self,
        content: bytes,
        requested_url: str,
        final_url: str,
        response,
        elapsed_ms: float,
        ua: str,
        attempt: int,
        *,
        ssl_bypass: bool = False,
        total_attempt: int | None = None,
    ) -> FetchResult:
        html = self._decode_content(content, response.charset_encoding)
        metadata: dict = {"fetcher": "httpx", "user_agent": ua, "attempt": (total_attempt or attempt + 1)}
        if ssl_bypass:
            metadata["ssl_bypass"] = True
        return FetchResult(
            html=html,
            url=requested_url,
            status_code=response.status_code,
            final_url=final_url,
            headers=response.headers,
            timing_ms=elapsed_ms,
            metadata=metadata,
        )

    def _handle_retryable_status(
        self, response_host: str, status_code: int, retry_delay: float
    ) -> None:
        if status_code in {403, 429}:
            self._record_soft_throttle(response_host, retry_delay)
        else:
            self._defer_host(response_host, retry_delay)
            self._record_failure(response_host)

    async def fetch(self, url: str) -> FetchResult:
        url, logical_url, host_header, sni_hostname, host = self._prepare_request_url(url)
        requested_logical_url = logical_url

        last_exc: Exception | None = None
        ssl_retried = self._is_ssl_bypass_active(host)
        verify: bool | str = not ssl_retried
        attempt = 0
        redirect_count = 0
        previous_ua: str | None = None

        client = await self._get_async_client()
        while attempt < _MAX_RETRIES and not ssl_retried:
            ua = self._next_user_agent(previous=previous_ua)
            previous_ua = ua
            self._ensure_circuit_closed(host)
            sleep_for = self._reserve_domain_slot(host)
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
                self._ensure_circuit_closed(host)
            start_time = time.perf_counter()

            try:
                _stream_kw: dict[str, object] = {"headers": self._build_headers(ua, host_header=host_header)}
                if sni_hostname:
                    _stream_kw["extensions"] = {"sni_hostname": sni_hostname.encode("ascii")}
                async with client.stream("GET", url, **_stream_kw) as response:
                    response_host = host

                    if self._should_follow_redirect(response):
                        redirect_target = self._prepare_redirect_url(
                            response, logical_url, redirect_count
                        )
                        if redirect_target is None:
                            raise RuntimeError("Redirect response missing Location header")
                        try:
                            await response.aread()
                        except Exception:
                            logger.debug("Failed to read redirect response body", exc_info=True)
                        url, logical_url, host_header, sni_hostname, host = redirect_target
                        redirect_count += 1
                        continue

                    if self.max_response_size is not None:
                        content_length = response.headers.get("content-length")
                        parsed_length = _parse_content_length(content_length)
                        if parsed_length is not None and parsed_length > self.max_response_size:
                            raise ResponseSizeLimitError(
                                f"Response size {parsed_length} exceeds max_response_size {self.max_response_size}"
                            )

                    if self._is_redirect_response(response) and not self.follow_redirects:
                        chunks: list[bytes] = []
                        total_size = 0
                        async for chunk in response.aiter_bytes():
                            total_size += len(chunk)
                            if (
                                self.max_response_size is not None
                                and total_size > self.max_response_size
                            ):
                                raise ResponseSizeLimitError(
                                    f"Response content size {total_size} exceeds max_response_size {self.max_response_size}"
                                )
                            chunks.append(chunk)
                        content = b"".join(chunks)
                        elapsed_ms = (time.perf_counter() - start_time) * 1000
                        self._record_success(response_host)
                        return self._make_fetch_result(
                            content,
                            requested_logical_url,
                            logical_url,
                            response,
                            elapsed_ms,
                            ua,
                            attempt,
                        )

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

                    chunks: list[bytes] = []
                    total_size = 0
                    async for chunk in response.aiter_bytes():
                        total_size += len(chunk)
                        if (
                            self.max_response_size is not None
                            and total_size > self.max_response_size
                        ):
                            raise ResponseSizeLimitError(
                                f"Response content size {total_size} exceeds max_response_size {self.max_response_size}"
                            )
                        chunks.append(chunk)

                    content = b"".join(chunks)

                elapsed_ms = (time.perf_counter() - start_time) * 1000
                self._record_success(response_host)
                return self._make_fetch_result(
                    content,
                    requested_logical_url,
                    logical_url,
                    response,
                    elapsed_ms,
                    ua,
                    attempt,
                )

            except (UnsupportedContentTypeError, ResponseSizeLimitError):
                raise

            except httpx.HTTPStatusError as exc:
                last_exc = exc
                status_code = exc.response.status_code
                response_host = host
                if status_code not in _RETRYABLE_STATUS:
                    self._record_failure(response_host)
                    raise
                retry_delay = _retry_delay_seconds(exc.response, attempt)
                self._handle_retryable_status(response_host, status_code, retry_delay)
                if attempt >= _MAX_RETRIES - 1:
                    raise
                await asyncio.sleep(retry_delay)
                attempt += 1
                continue

            except DomainCircuitOpenError:
                raise

            except httpx.TooManyRedirects:
                self._record_failure(host)
                raise

            except Exception as exc:
                last_exc = exc
                self._record_failure(host)

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
                    attempt += 1
                    break  # pragma: no cover
                attempt += 1

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
            ssl_attempt = 0
            while ssl_attempt < remaining_attempts:
                ssl_attempt_num = ssl_attempt + 1
                total_attempt_num = consumed_attempts + ssl_attempt_num
                async with httpx.AsyncClient(
                    timeout=self.timeout,
                    follow_redirects=False,
                    max_redirects=self.max_redirects,
                    verify=False,
                    trust_env=False,
                ) as client:
                    try:
                        while True:
                            ua = self._next_user_agent(previous=previous_ua)
                            previous_ua = ua
                            self._ensure_circuit_closed(host)
                            sleep_for = self._reserve_domain_slot(host)
                            if sleep_for > 0:
                                await asyncio.sleep(sleep_for)
                                self._ensure_circuit_closed(host)
                            start_time = time.perf_counter()

                            _ssl_stream_kw: dict[str, object] = {
                                "headers": self._build_headers(ua, host_header=host_header)
                            }
                            if sni_hostname:
                                _ssl_stream_kw["extensions"] = {
                                    "sni_hostname": sni_hostname.encode("ascii")
                                }
                            async with client.stream("GET", url, **_ssl_stream_kw) as response:
                                response_host = host

                                if self._should_follow_redirect(response):
                                    redirect_target = self._prepare_redirect_url(
                                        response, logical_url, redirect_count
                                    )
                                    if redirect_target is None:
                                        raise RuntimeError("Redirect response missing Location header")
                                    try:
                                        await response.aread()
                                    except Exception:
                                        logger.debug(
                                            "Failed to read redirect response body", exc_info=True
                                        )
                                    url, logical_url, host_header, sni_hostname, host = redirect_target
                                    redirect_count += 1
                                    continue

                                if self.max_response_size is not None:
                                    content_length = response.headers.get("content-length")
                                    parsed_length = _parse_content_length(content_length)
                                    if (
                                        parsed_length is not None
                                        and parsed_length > self.max_response_size
                                    ):
                                        raise ResponseSizeLimitError(
                                            f"Response size {parsed_length} exceeds max_response_size {self.max_response_size}"
                                        )

                                if self._is_redirect_response(response) and not self.follow_redirects:
                                    ssl_chunks: list[bytes] = []
                                    ssl_total_size = 0
                                    async for chunk in response.aiter_bytes():
                                        ssl_total_size += len(chunk)
                                        if (
                                            self.max_response_size is not None
                                            and ssl_total_size > self.max_response_size
                                        ):
                                            raise ResponseSizeLimitError(
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
                                    return self._make_fetch_result(
                                        ssl_content,
                                        requested_logical_url,
                                        logical_url,
                                        response,
                                        elapsed_ms,
                                        ua,
                                        ssl_attempt,
                                        ssl_bypass=True,
                                        total_attempt=total_attempt_num,
                                    )

                                response.raise_for_status()
                                _validate_content_type(response)

                                ssl_chunks: list[bytes] = []
                                ssl_total_size = 0
                                async for chunk in response.aiter_bytes():
                                    ssl_total_size += len(chunk)
                                    if (
                                        self.max_response_size is not None
                                        and ssl_total_size > self.max_response_size
                                    ):
                                        raise ResponseSizeLimitError(
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
                            return self._make_fetch_result(
                                ssl_content,
                                requested_logical_url,
                                logical_url,
                                response,
                                elapsed_ms,
                                ua,
                                ssl_attempt,
                                ssl_bypass=True,
                                total_attempt=total_attempt_num,
                            )

                    except (UnsupportedContentTypeError, ResponseSizeLimitError):
                        raise

                    except httpx.HTTPStatusError as exc:
                        ssl_last_exc = exc
                        status_code = exc.response.status_code
                        retry_delay = _retry_delay_seconds(exc.response, ssl_attempt)
                        response_host = host

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
                            self._handle_retryable_status(response_host, status_code, retry_delay)
                            await asyncio.sleep(retry_delay)
                            ssl_attempt += 1
                            continue
                        self._record_failure(response_host)
                        raise

                    except DomainCircuitOpenError:
                        raise

                    except httpx.TooManyRedirects:
                        self._record_failure(host)
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
                            ssl_attempt += 1
                            continue
                        raise

            if ssl_last_exc is not None:
                raise ssl_last_exc
            raise RuntimeError(f"SSL bypass fetch failed for {url} after {_MAX_RETRIES} attempts")

        if last_exc is not None:
            raise last_exc
        raise RuntimeError(
            f"Fetch failed for {url} after {_MAX_RETRIES} attempts"
        )  # pragma: no cover

    def fetch_sync(self, url: str) -> FetchResult:
        url, logical_url, host_header, sni_hostname, host = self._prepare_request_url(url)
        requested_logical_url = logical_url

        last_exc: Exception | None = None
        ssl_retried = self._is_ssl_bypass_active(host)
        verify: bool | str = not ssl_retried
        attempt = 0
        redirect_count = 0
        previous_ua: str | None = None

        client = self._get_sync_client()
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
                _stream_kw: dict[str, object] = {
                    "headers": self._build_headers(ua, host_header=host_header)
                }
                if sni_hostname:
                    _stream_kw["extensions"] = {"sni_hostname": sni_hostname.encode("ascii")}
                with client.stream("GET", url, **_stream_kw) as response:
                    response_host = host
                    if self._should_follow_redirect(response):
                        redirect_target = self._prepare_redirect_url(
                            response, logical_url, redirect_count
                        )
                        if redirect_target is None:
                            raise RuntimeError("Redirect response missing Location header")
                        try:
                            response.read()
                        except Exception:
                            logger.debug("Failed to read redirect response body", exc_info=True)
                        url, logical_url, host_header, sni_hostname, host = redirect_target
                        redirect_count += 1
                        continue

                    if self.max_response_size is not None:
                        content_length = response.headers.get("content-length")
                        parsed_length = _parse_content_length(content_length)
                        if parsed_length is not None and parsed_length > self.max_response_size:
                            raise ResponseSizeLimitError(
                                f"Response size {parsed_length} exceeds max_response_size {self.max_response_size}"
                            )

                    if self._is_redirect_response(response) and not self.follow_redirects:
                        sync_chunks: list[bytes] = []
                        sync_total_size = 0
                        for chunk in response.iter_bytes():
                            sync_total_size += len(chunk)
                            if (
                                self.max_response_size is not None
                                and sync_total_size > self.max_response_size
                            ):
                                raise ResponseSizeLimitError(
                                    f"Response content size {sync_total_size} exceeds max_response_size {self.max_response_size}"
                                )
                            sync_chunks.append(chunk)
                        sync_content = b"".join(sync_chunks)
                        elapsed_ms = (time.perf_counter() - start_time) * 1000
                        self._record_success(response_host)
                        return self._make_fetch_result(
                            sync_content,
                            requested_logical_url,
                            logical_url,
                            response,
                            elapsed_ms,
                            ua,
                            attempt,
                        )

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

                    sync_chunks: list[bytes] = []
                    sync_total_size = 0
                    for chunk in response.iter_bytes():
                        sync_total_size += len(chunk)
                        if (
                            self.max_response_size is not None
                            and sync_total_size > self.max_response_size
                        ):
                            raise ResponseSizeLimitError(
                                f"Response content size {sync_total_size} exceeds max_response_size {self.max_response_size}"
                            )
                        sync_chunks.append(chunk)

                    sync_content = b"".join(sync_chunks)

                elapsed_ms = (time.perf_counter() - start_time) * 1000
                self._record_success(response_host)
                return self._make_fetch_result(
                    sync_content,
                    requested_logical_url,
                    logical_url,
                    response,
                    elapsed_ms,
                    ua,
                    attempt,
                )

            except (UnsupportedContentTypeError, ResponseSizeLimitError):
                raise

            except httpx.HTTPStatusError as exc:
                last_exc = exc
                status_code = exc.response.status_code
                response_host = host
                if status_code not in _RETRYABLE_STATUS:
                    self._record_failure(response_host)
                    raise
                retry_delay = _retry_delay_seconds(exc.response, attempt)
                self._handle_retryable_status(response_host, status_code, retry_delay)
                if attempt >= _MAX_RETRIES - 1:
                    raise
                time.sleep(retry_delay)
                attempt += 1
                continue

            except DomainCircuitOpenError:
                raise

            except httpx.TooManyRedirects:
                self._record_failure(host)
                raise

            except Exception as exc:
                last_exc = exc
                self._record_failure(host)

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
                    attempt += 1
                    break  # pragma: no cover
                attempt += 1

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
            ssl_attempt = 0
            while ssl_attempt < remaining_attempts:
                ssl_attempt_num = ssl_attempt + 1
                total_attempt_num = consumed_attempts + ssl_attempt_num
                with httpx.Client(
                    timeout=self.timeout,
                    follow_redirects=False,
                    max_redirects=self.max_redirects,
                    verify=False,
                    trust_env=False,
                ) as client:
                    try:
                        while True:
                            ua = self._next_user_agent(previous=previous_ua)
                            previous_ua = ua
                            self._ensure_circuit_closed(host)
                            sleep_for = self._reserve_domain_slot(host)
                            if sleep_for > 0:
                                time.sleep(sleep_for)
                                self._ensure_circuit_closed(host)
                            start_time = time.perf_counter()

                            _ssl_stream_kw: dict[str, object] = {
                                "headers": self._build_headers(ua, host_header=host_header)
                            }
                            if sni_hostname:
                                _ssl_stream_kw["extensions"] = {
                                    "sni_hostname": sni_hostname.encode("ascii")
                                }
                            with client.stream("GET", url, **_ssl_stream_kw) as response:
                                response_host = host

                                if self._should_follow_redirect(response):
                                    redirect_target = self._prepare_redirect_url(
                                        response, logical_url, redirect_count
                                    )
                                    if redirect_target is None:
                                        raise RuntimeError("Redirect response missing Location header")
                                    try:
                                        response.read()
                                    except Exception:
                                        logger.debug(
                                            "Failed to read redirect response body", exc_info=True
                                        )
                                    url, logical_url, host_header, sni_hostname, host = redirect_target
                                    redirect_count += 1
                                    continue

                                if self.max_response_size is not None:
                                    content_length = response.headers.get("content-length")
                                    parsed_length = _parse_content_length(content_length)
                                    if (
                                        parsed_length is not None
                                        and parsed_length > self.max_response_size
                                    ):
                                        raise ResponseSizeLimitError(
                                            f"Response size {parsed_length} exceeds max_response_size {self.max_response_size}"
                                        )

                                if self._is_redirect_response(response) and not self.follow_redirects:
                                    ssl_sync_chunks: list[bytes] = []
                                    ssl_sync_total = 0
                                    for chunk in response.iter_bytes():
                                        ssl_sync_total += len(chunk)
                                        if (
                                            self.max_response_size is not None
                                            and ssl_sync_total > self.max_response_size
                                        ):
                                            raise ResponseSizeLimitError(
                                                f"Response content size {ssl_sync_total} exceeds max_response_size {self.max_response_size}"
                                            )
                                        ssl_sync_chunks.append(chunk)
                                    ssl_sync_content = b"".join(ssl_sync_chunks)
                                    elapsed_ms = (time.perf_counter() - start_time) * 1000
                                    self._record_success(response_host)
                                    with self._ssl_bypass_lock:
                                        self._ssl_bypass_hosts[host] = (
                                            time.monotonic() + self._ssl_bypass_ttl
                                        )
                                    return self._make_fetch_result(
                                        ssl_sync_content,
                                        requested_logical_url,
                                        logical_url,
                                        response,
                                        elapsed_ms,
                                        ua,
                                        ssl_attempt,
                                        ssl_bypass=True,
                                        total_attempt=total_attempt_num,
                                    )

                                response.raise_for_status()
                                _validate_content_type(response)

                                ssl_sync_chunks: list[bytes] = []
                                ssl_sync_total = 0
                                for chunk in response.iter_bytes():
                                    ssl_sync_total += len(chunk)
                                    if (
                                        self.max_response_size is not None
                                        and ssl_sync_total > self.max_response_size
                                    ):
                                        raise ResponseSizeLimitError(
                                            f"Response content size {ssl_sync_total} exceeds max_response_size {self.max_response_size}"
                                        )
                                    ssl_sync_chunks.append(chunk)

                                ssl_sync_content = b"".join(ssl_sync_chunks)

                            elapsed_ms = (time.perf_counter() - start_time) * 1000
                            self._record_success(response_host)
                            with self._ssl_bypass_lock:
                                self._ssl_bypass_hosts[host] = (
                                    time.monotonic() + self._ssl_bypass_ttl
                                )
                            return self._make_fetch_result(
                                ssl_sync_content,
                                requested_logical_url,
                                logical_url,
                                response,
                                elapsed_ms,
                                ua,
                                ssl_attempt,
                                ssl_bypass=True,
                                total_attempt=total_attempt_num,
                            )

                    except (UnsupportedContentTypeError, ResponseSizeLimitError):
                        raise

                    except httpx.HTTPStatusError as exc:
                        ssl_last_exc = exc
                        status_code = exc.response.status_code
                        retry_delay = _retry_delay_seconds(exc.response, ssl_attempt)
                        response_host = host

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
                            self._handle_retryable_status(response_host, status_code, retry_delay)
                            time.sleep(retry_delay)
                            ssl_attempt += 1
                            continue
                        self._record_failure(response_host)
                        raise

                    except DomainCircuitOpenError:
                        raise

                    except httpx.TooManyRedirects:
                        self._record_failure(host)
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
                            ssl_attempt += 1
                            continue
                        raise

            if ssl_last_exc is not None:
                raise ssl_last_exc
            raise RuntimeError(f"SSL bypass fetch failed for {url} after {_MAX_RETRIES} attempts")

        if last_exc is not None:
            raise last_exc
        raise RuntimeError(
            f"Fetch failed for {url} after {_MAX_RETRIES} attempts"
        )  # pragma: no cover
