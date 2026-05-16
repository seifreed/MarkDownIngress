"""HTTP fetcher adapter using httpx - fast mode implementation."""

import asyncio
import logging
import time
from threading import Lock

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
from markdown_ingress.adapters.fetching.http_support import (
    MAX_RETRIES as _MAX_RETRIES,
)
from markdown_ingress.adapters.fetching.http_support import (
    RETRYABLE_STATUS as _RETRYABLE_STATUS,
)
from markdown_ingress.adapters.fetching.http_support import (
    PreparedRequest as _PreparedRequest,
)
from markdown_ingress.adapters.fetching.http_support import (
    ResponseSizeLimitError,
)
from markdown_ingress.adapters.fetching.http_support import (
    retry_delay_seconds as _retry_delay_seconds,
)
from markdown_ingress.adapters.fetching.http_support import (
    should_retry_with_ssl_bypass as _should_retry_with_ssl_bypass,
)
from markdown_ingress.adapters.fetching.http_support import (
    ssl_bypass_retry_delay as _ssl_bypass_retry_delay,
)
from markdown_ingress.adapters.fetching.http_support import (
    validate_content_type as _validate_content_type,
)
from markdown_ingress.adapters.fetching.request_policy import FetchRequestPolicyMixin
from markdown_ingress.adapters.fetching.response_content import ResponseContentMixin
from markdown_ingress.core.interfaces import IFetcher
from markdown_ingress.core.policy import DomainCircuitOpenError, UnsupportedContentTypeError
from markdown_ingress.core.stealth.browser_config import ADVANCED_USER_AGENTS
from markdown_ingress.models import FetchResult

logger = logging.getLogger(__name__)


class Fetcher(
    ClientLifecycleMixin,
    FetchRequestPolicyMixin,
    ResponseContentMixin,
    DomainStateMixin,
    IFetcher,
):
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

                    self._enforce_declared_response_size(response)

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
                        self._handle_retryable_status(
                            response_host, response.status_code, retry_delay
                        )
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

                if _should_retry_with_ssl_bypass(
                    allow_ssl_bypass=self.allow_ssl_bypass,
                    ssl_retried=ssl_retried,
                    exc=exc,
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

                                self._enforce_declared_response_size(response)

                                if (
                                    self._is_redirect_response(response)
                                    and not self.follow_redirects
                                ):
                                    ssl_content = await self._read_async_response_content(response)
                                    elapsed_ms = (time.perf_counter() - start_time) * 1000
                                    self._record_success(response_host)
                                    self._remember_ssl_bypass_host(host)
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
                            self._remember_ssl_bypass_host(host)
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
                            retry_delay = _ssl_bypass_retry_delay(ssl_attempt)
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

                    self._enforce_declared_response_size(response)

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
                        self._handle_retryable_status(
                            response_host, response.status_code, retry_delay
                        )
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

                if _should_retry_with_ssl_bypass(
                    allow_ssl_bypass=self.allow_ssl_bypass,
                    ssl_retried=ssl_retried,
                    exc=exc,
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

                                self._enforce_declared_response_size(response)

                                if (
                                    self._is_redirect_response(response)
                                    and not self.follow_redirects
                                ):
                                    ssl_sync_content = self._read_sync_response_content(response)
                                    elapsed_ms = (time.perf_counter() - start_time) * 1000
                                    self._record_success(response_host)
                                    self._remember_ssl_bypass_host(host)
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
                            self._remember_ssl_bypass_host(host)
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
                            retry_delay = _ssl_bypass_retry_delay(ssl_attempt)
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
