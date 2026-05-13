"""HTTP fetcher adapter using httpx - fast mode implementation."""

import asyncio
import logging
import random
import ssl
import time
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import urlsplit

import httpx

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
from markdown_ingress.adapters.fetching.http_support import (
    FOLLOW_REDIRECT_STATUS as _FOLLOW_REDIRECT_STATUS,
)
from markdown_ingress.adapters.fetching.http_support import (
    MAX_RETRIES as _MAX_RETRIES,
)
from markdown_ingress.adapters.fetching.http_support import (
    RETRYABLE_STATUS as _RETRYABLE_STATUS,
)
from markdown_ingress.adapters.fetching.http_support import (
    SAFE_HEADERS as _SAFE_HEADERS,
)
from markdown_ingress.adapters.fetching.http_support import (
    PreparedRequest as _PreparedRequest,
)
from markdown_ingress.adapters.fetching.http_support import (
    ResponseSizeLimitError,
)
from markdown_ingress.adapters.fetching.http_support import (
    format_host_header as _format_host_header,
)
from markdown_ingress.adapters.fetching.http_support import (
    parse_content_length as _parse_content_length,
)
from markdown_ingress.adapters.fetching.http_support import (
    retry_delay_seconds as _retry_delay_seconds,
)
from markdown_ingress.adapters.fetching.http_support import (
    validate_content_type as _validate_content_type,
)
from markdown_ingress.adapters.fetching.response_content import ResponseContentMixin
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

_rng = random.SystemRandom()


class Fetcher(ResponseContentMixin, DomainStateMixin, IFetcher):
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

    def _track_async_close_task(self, task: asyncio.Task[None]) -> None:
        self._async_close_tasks.add(task)

        def _finalize_close_task(done_task: asyncio.Task[None]) -> None:
            self._async_close_tasks.discard(done_task)
            try:
                done_task.result()
            except Exception as exc:
                logger.debug(
                    "Background async HTTP client close failed: %s",
                    exc,
                    exc_info=True,
                )

        task.add_done_callback(_finalize_close_task)

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
        return response.status_code in _FOLLOW_REDIRECT_STATUS and bool(
            response.headers.get("location")
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

    def _build_ssl_context(self, *, verify_certificates: bool = True) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        if not verify_certificates:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx
        if self.ca_bundle:
            if Path(self.ca_bundle).is_dir():
                ctx.load_verify_locations(capath=self.ca_bundle)
            else:
                ctx.load_verify_locations(cafile=self.ca_bundle)
        return ctx

    @property
    def user_agent(self) -> str:
        if self._fixed_ua is not None:
            return self._fixed_ua
        if not self.rotate_ua:
            if self._stable_ua is None:
                self._stable_ua = _rng.choice(self._ua_pool)
            return self._stable_ua
        return self._next_user_agent(previous=self._last_rotating_ua)

    def _next_user_agent(self, *, previous: str | None = None) -> str:
        if self._fixed_ua is not None:
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
            if self._async_client_lock is None:
                self._async_client_lock = asyncio.Lock()
            return self._async_client_lock

    async def _get_async_client(self) -> httpx.AsyncClient:
        if self._closing:
            raise RuntimeError("Fetcher is closing")
        if self._async_client is None:
            async with self._get_async_client_lock():
                if self._closing:
                    raise RuntimeError("Fetcher is closing")
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
        self._closing = True
        with self._client_lock:
            if self._sync_client is not None:
                self._sync_client.close()
                self._sync_client = None
        with self._async_client_lock_guard:
            client_to_close = self._async_client
            self._async_client = None
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
                self._track_async_close_task(loop.create_task(client_to_close.aclose()))
            except Exception as exc:
                logger.debug(
                    "Failed to schedule async client close as background task: %s",
                    exc,
                    exc_info=True,
                )

    async def aclose(self) -> None:
        self._closing = True
        async with self._get_async_client_lock():
            if self._async_client is not None:
                await self._async_client.aclose()
                self._async_client = None

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    async def __aenter__(self) -> "Fetcher":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

    def __del__(self) -> None:
        sync_client_exists = False
        async_client_exists = False
        try:
            with self._client_lock:
                sync_client_exists = self._sync_client is not None
        except Exception as exc:
            logger.debug(
                "Could not inspect sync client during finalization: %s", exc, exc_info=True
            )
        try:
            with self._async_client_lock_guard:
                async_client_exists = getattr(self, "_async_client", None) is not None
        except Exception as exc:
            logger.debug(
                "Could not inspect async client during finalization: %s", exc, exc_info=True
            )

        if sync_client_exists or async_client_exists:
            import warnings

            warnings.warn(
                "Fetcher was not properly closed; HTTP client resources may leak. "
                "Use 'async with fetcher:' or call 'await fetcher.aclose()' explicitly.",
                ResourceWarning,
                stacklevel=2,
            )
        try:
            with self._client_lock:
                if self._sync_client is not None:
                    self._sync_client.close()
                    self._sync_client = None
        except Exception as exc:
            logger.debug("Could not close sync client during finalization: %s", exc, exc_info=True)
        try:
            with self._async_client_lock_guard:
                self._async_client = None
                self._async_client_lock = None
        except Exception as exc:
            logger.debug("Could not clear async client during finalization: %s", exc, exc_info=True)

    def _build_headers(self, ua: str, *, host_header: str | None = None) -> dict:
        headers = dict(_SAFE_HEADERS)
        headers["User-Agent"] = ua
        if host_header is not None:
            headers["Host"] = host_header
        return headers

    @staticmethod
    def _stream_extensions(sni_hostname: str | None) -> dict[str, Any] | None:
        if sni_hostname is None:
            return None
        return {"sni_hostname": sni_hostname.encode("ascii")}

    @staticmethod
    def _resolve_allow_local_urls(allow_local_urls: bool | None) -> bool:
        return resolve_allow_local_urls(allow_local_urls)

    @staticmethod
    def _is_dns_transient_error(exc: Exception) -> bool:
        """Return True when the exception indicates a transient DNS failure."""
        import socket

        if isinstance(exc, socket.gaierror):
            return True
        if isinstance(exc, ValueError):
            msg = str(exc).lower()
            for hint in ("dns", "resolve", "nxdomain", "timeout", "temporary failure"):
                if hint in msg:
                    return True
        return False

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

    def _handle_retryable_status(
        self, response_host: str, status_code: int, retry_delay: float
    ) -> None:
        if status_code in {403, 429}:
            self._record_soft_throttle(response_host, retry_delay)
        else:
            self._defer_host(response_host, retry_delay)
            self._record_failure(response_host)

    def _prepare_request_url_with_dns_retry(self, url: str) -> _PreparedRequest:
        """Call _prepare_request_url, retrying on transient DNS failures."""
        last_dns_exc: Exception | None = None
        for dns_attempt in range(_MAX_RETRIES):
            try:
                return self._prepare_request_url(url)
            except (ValueError, OSError) as exc:
                if not self._is_dns_transient_error(exc):
                    raise
                last_dns_exc = exc
                if dns_attempt < _MAX_RETRIES - 1:
                    sleep_for = min(0.5 * (2**dns_attempt), 4.0)
                    time.sleep(sleep_for)
        if last_dns_exc is not None:
            raise last_dns_exc
        raise RuntimeError(f"DNS validation failed for {url}")

    async def fetch(self, url: str) -> FetchResult:
        url, logical_url, host_header, sni_hostname, host = (
            self._prepare_request_url_with_dns_retry(url)
        )
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
                headers = self._build_headers(ua, host_header=host_header)
                extensions = self._stream_extensions(sni_hostname)
                if extensions is None:
                    stream = client.stream("GET", url, headers=headers)
                else:
                    stream = client.stream("GET", url, headers=headers, extensions=extensions)
                async with stream as response:
                    response_host = host

                    if self._should_follow_redirect(response):
                        redirect_target = self._prepare_redirect_url(
                            response, logical_url, redirect_count
                        )
                        if redirect_target is None:
                            raise RuntimeError("Redirect response missing Location header")
                        await self._drain_async_response_for_reuse(
                            response, "Failed to read redirect response body"
                        )
                        url, logical_url, host_header, sni_hostname, host = redirect_target
                        redirect_count += 1
                        continue

                    if self.max_response_size is not None:
                        content_length = response.headers.get("content-length")
                        parsed_length = _parse_content_length(content_length)
                        if parsed_length is not None and parsed_length > self.max_response_size:
                            raise ResponseSizeLimitError(
                                f"Response size {parsed_length} exceeds "
                                f"max_response_size {self.max_response_size}"
                            )

                    if self._is_redirect_response(response) and not self.follow_redirects:
                        content = await self._read_async_response_content(response)
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
                        await self._drain_async_response_for_reuse(
                            response, "Failed to read response body during retry"
                        )
                        await asyncio.sleep(retry_delay)
                        attempt += 1
                        continue

                    response.raise_for_status()
                    _validate_content_type(response)

                    content = await self._read_async_response_content(response)

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

            except (ValueError, httpx.InvalidURL, httpx.UnsupportedProtocol):
                raise

            except httpx.HTTPStatusError as exc:
                last_exc = exc
                status_code = exc.response.status_code
                response_host = host
                if status_code not in _RETRYABLE_STATUS:
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
                        "SSL verification failed for %s, retrying with certificate verification "
                        "disabled. "
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
                    verify=self._build_ssl_context(verify_certificates=False),
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

                            headers = self._build_headers(ua, host_header=host_header)
                            extensions = self._stream_extensions(sni_hostname)
                            if extensions is None:
                                stream = client.stream("GET", url, headers=headers)
                            else:
                                stream = client.stream(
                                    "GET", url, headers=headers, extensions=extensions
                                )
                            async with stream as response:
                                response_host = host

                                if self._should_follow_redirect(response):
                                    redirect_target = self._prepare_redirect_url(
                                        response, logical_url, redirect_count
                                    )
                                    if redirect_target is None:
                                        raise RuntimeError(
                                            "Redirect response missing Location header"
                                        )
                                    await self._drain_async_response_for_reuse(
                                        response, "Failed to read redirect response body"
                                    )
                                    url, logical_url, host_header, sni_hostname, host = (
                                        redirect_target
                                    )
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
                                            f"Response size {parsed_length} exceeds "
                                            f"max_response_size {self.max_response_size}"
                                        )

                                if (
                                    self._is_redirect_response(response)
                                    and not self.follow_redirects
                                ):
                                    ssl_content = await self._read_async_response_content(response)
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

                                ssl_content = await self._read_async_response_content(response)

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

                    except (ValueError, httpx.InvalidURL, httpx.UnsupportedProtocol):
                        raise

                    except httpx.HTTPStatusError as exc:
                        ssl_last_exc = exc
                        status_code = exc.response.status_code
                        retry_delay = _retry_delay_seconds(exc.response, ssl_attempt)
                        response_host = host

                        if status_code not in _RETRYABLE_STATUS:
                            raise
                        if ssl_attempt < remaining_attempts - 1:
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
                        self._handle_retryable_status(response_host, status_code, retry_delay)
                        raise

                    except DomainCircuitOpenError:
                        raise

                    except httpx.TooManyRedirects:
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
        url, logical_url, host_header, sni_hostname, host = (
            self._prepare_request_url_with_dns_retry(url)
        )
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
                headers = self._build_headers(ua, host_header=host_header)
                extensions = self._stream_extensions(sni_hostname)
                if extensions is None:
                    stream = client.stream("GET", url, headers=headers)
                else:
                    stream = client.stream("GET", url, headers=headers, extensions=extensions)
                with stream as response:
                    response_host = host
                    if self._should_follow_redirect(response):
                        redirect_target = self._prepare_redirect_url(
                            response, logical_url, redirect_count
                        )
                        if redirect_target is None:
                            raise RuntimeError("Redirect response missing Location header")
                        self._drain_sync_response_for_reuse(
                            response, "Failed to read redirect response body"
                        )
                        url, logical_url, host_header, sni_hostname, host = redirect_target
                        redirect_count += 1
                        continue

                    if self.max_response_size is not None:
                        content_length = response.headers.get("content-length")
                        parsed_length = _parse_content_length(content_length)
                        if parsed_length is not None and parsed_length > self.max_response_size:
                            raise ResponseSizeLimitError(
                                f"Response size {parsed_length} exceeds "
                                f"max_response_size {self.max_response_size}"
                            )

                    if self._is_redirect_response(response) and not self.follow_redirects:
                        sync_content = self._read_sync_response_content(response)
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
                        self._drain_sync_response_for_reuse(
                            response, "Failed to read response body during retry"
                        )
                        time.sleep(retry_delay)
                        attempt += 1
                        continue

                    response.raise_for_status()
                    _validate_content_type(response)

                    sync_content = self._read_sync_response_content(response)

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

            except (ValueError, httpx.InvalidURL, httpx.UnsupportedProtocol):
                raise

            except httpx.HTTPStatusError as exc:
                last_exc = exc
                status_code = exc.response.status_code
                response_host = host
                if status_code not in _RETRYABLE_STATUS:
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
                        "SSL verification failed for %s, retrying with certificate verification "
                        "disabled. "
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
                    verify=self._build_ssl_context(verify_certificates=False),
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

                            headers = self._build_headers(ua, host_header=host_header)
                            extensions = self._stream_extensions(sni_hostname)
                            if extensions is None:
                                stream = client.stream("GET", url, headers=headers)
                            else:
                                stream = client.stream(
                                    "GET", url, headers=headers, extensions=extensions
                                )
                            with stream as response:
                                response_host = host

                                if self._should_follow_redirect(response):
                                    redirect_target = self._prepare_redirect_url(
                                        response, logical_url, redirect_count
                                    )
                                    if redirect_target is None:
                                        raise RuntimeError(
                                            "Redirect response missing Location header"
                                        )
                                    self._drain_sync_response_for_reuse(
                                        response, "Failed to read redirect response body"
                                    )
                                    url, logical_url, host_header, sni_hostname, host = (
                                        redirect_target
                                    )
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
                                            f"Response size {parsed_length} exceeds "
                                            f"max_response_size {self.max_response_size}"
                                        )

                                if (
                                    self._is_redirect_response(response)
                                    and not self.follow_redirects
                                ):
                                    ssl_sync_content = self._read_sync_response_content(response)
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

                                ssl_sync_content = self._read_sync_response_content(response)

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

                    except (ValueError, httpx.InvalidURL, httpx.UnsupportedProtocol):
                        raise

                    except httpx.HTTPStatusError as exc:
                        ssl_last_exc = exc
                        status_code = exc.response.status_code
                        retry_delay = _retry_delay_seconds(exc.response, ssl_attempt)
                        response_host = host

                        if status_code not in _RETRYABLE_STATUS:
                            raise
                        if ssl_attempt < remaining_attempts - 1:
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
                        self._handle_retryable_status(response_host, status_code, retry_delay)
                        raise

                    except DomainCircuitOpenError:
                        raise

                    except httpx.TooManyRedirects:
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
