"""Async HTTPX fetch path."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, cast

import httpx

from markdown_ingress.adapters.fetching.http_fetch_state import HttpFetchState
from markdown_ingress.adapters.fetching.http_support import (
    MAX_RETRIES,
    RETRYABLE_STATUS,
    ResponseSizeLimitError,
    retry_delay_seconds,
    should_retry_with_ssl_bypass,
    validate_content_type,
)
from markdown_ingress.core.policy import DomainCircuitOpenError, UnsupportedContentTypeError
from markdown_ingress.models import FetchResult

logger = logging.getLogger(__name__)


class AsyncHttpxFetchMixin:
    """Async HTTPX fetch implementation."""

    async def fetch(self: Any, url: str) -> FetchResult:
        prepared = self._prepare_request_url_with_dns_retry(url)
        state = HttpFetchState.from_prepared_request(
            prepared,
            ssl_retried=self._is_ssl_bypass_active(prepared[4]),
        )
        client = await self._get_async_client()
        while state.attempt < MAX_RETRIES and not state.ssl_retried:
            result = await self._run_async_fetch_attempt(client, state)
            if result is not None:
                return cast(FetchResult, result)

        if state.ssl_retried and state.verify is False:
            return cast(
                FetchResult,
                await self._fetch_with_ssl_bypass(
                    state.to_ssl_bypass_state(),
                    consumed_attempts=state.attempt,
                    last_exc=state.last_exc,
                ),
            )

        if state.last_exc is not None:
            raise state.last_exc
        raise RuntimeError(f"Fetch failed for {state.url} after {MAX_RETRIES} attempts")

    async def _prepare_async_fetch_attempt(self: Any, state: HttpFetchState) -> tuple[str, float]:
        ua = self._next_user_agent(previous=state.previous_ua)
        state.previous_ua = ua
        self._ensure_circuit_closed(state.host)
        sleep_for = self._reserve_domain_slot(state.host)
        if sleep_for > 0:
            await asyncio.sleep(sleep_for)
            self._ensure_circuit_closed(state.host)
        return ua, time.perf_counter()

    async def _run_async_fetch_attempt(
        self: Any, client: Any, state: HttpFetchState
    ) -> FetchResult | None:
        ua, start_time = await self._prepare_async_fetch_attempt(state)
        try:
            headers = self._build_headers(ua, host_header=state.host_header)
            stream = self._open_stream(
                client,
                state.url,
                headers=headers,
                sni_hostname=state.sni_hostname,
            )
            async with stream as response:
                result = await self._handle_async_fetch_response(response, state, start_time, ua)
                return cast(FetchResult | None, result)
        except (UnsupportedContentTypeError, ResponseSizeLimitError):
            raise
        except (ValueError, httpx.InvalidURL, httpx.UnsupportedProtocol):
            raise
        except httpx.HTTPStatusError as exc:
            await self._handle_async_status_error(state, exc)
        except (DomainCircuitOpenError, httpx.TooManyRedirects):
            raise
        except Exception as exc:
            self._handle_async_generic_fetch_error(state, exc)
        return None

    async def _handle_async_fetch_response(
        self: Any,
        response: Any,
        state: HttpFetchState,
        start_time: float,
        ua: str,
    ) -> FetchResult | None:
        response_host = state.host
        if self._should_follow_redirect(response):
            await self._follow_async_redirect(response, state)
            return None
        self._enforce_declared_response_size(response)
        if self._is_redirect_response(response) and not self.follow_redirects:
            content = await self._read_async_response_content(response)
            return cast(
                FetchResult,
                self._finish_async_fetch_result(
                    response, state, response_host, start_time, ua, content
                ),
            )
        if response.status_code in RETRYABLE_STATUS and state.attempt < MAX_RETRIES - 1:
            await self._retry_async_response_status(response, state, response_host)
            return None
        response.raise_for_status()
        validate_content_type(response)
        content = await self._read_async_response_content(response)
        return cast(
            FetchResult,
            self._finish_async_fetch_result(
                response, state, response_host, start_time, ua, content
            ),
        )

    async def _follow_async_redirect(self: Any, response: Any, state: HttpFetchState) -> None:
        redirect_target = self._prepare_redirect_url(
            response,
            state.logical_url,
            state.redirect_count,
        )
        if redirect_target is None:
            raise RuntimeError("Redirect response missing Location header")
        await self._drain_async_response_for_reuse(
            response, "Failed to read redirect response body"
        )
        state.update_redirect_target(redirect_target)

    async def _retry_async_response_status(
        self: Any, response: Any, state: HttpFetchState, response_host: str
    ) -> None:
        retry_delay = retry_delay_seconds(response, state.attempt)
        self._handle_retryable_status(response_host, response.status_code, retry_delay)
        await self._drain_async_response_for_reuse(
            response, "Failed to read response body during retry"
        )
        await asyncio.sleep(retry_delay)
        state.attempt += 1

    def _finish_async_fetch_result(
        self: Any,
        response: Any,
        state: HttpFetchState,
        response_host: str,
        start_time: float,
        ua: str,
        content: bytes,
    ) -> FetchResult:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        self._record_success(response_host)
        return cast(
            FetchResult,
            self._make_fetch_result(
                content,
                state.requested_logical_url,
                state.logical_url,
                response,
                elapsed_ms,
                ua,
                state.attempt,
            ),
        )

    async def _handle_async_status_error(
        self: Any, state: HttpFetchState, exc: httpx.HTTPStatusError
    ) -> None:
        state.last_exc = exc
        status_code = exc.response.status_code
        if status_code not in RETRYABLE_STATUS:
            raise exc
        retry_delay = retry_delay_seconds(exc.response, state.attempt)
        self._handle_retryable_status(state.host, status_code, retry_delay)
        if state.attempt >= MAX_RETRIES - 1:
            raise exc
        await asyncio.sleep(retry_delay)
        state.attempt += 1

    def _handle_async_generic_fetch_error(self: Any, state: HttpFetchState, exc: Exception) -> None:
        state.last_exc = exc
        self._record_failure(state.host)
        if should_retry_with_ssl_bypass(
            allow_ssl_bypass=self.allow_ssl_bypass,
            ssl_retried=state.ssl_retried,
            exc=exc,
        ):
            logger.warning(
                "SSL verification failed for %s, retrying with certificate verification "
                "disabled. "
                "This bypass is insecure and should only be used for testing.",
                state.url,
            )
            state.mark_ssl_bypass_retry()
            return
        state.attempt += 1
