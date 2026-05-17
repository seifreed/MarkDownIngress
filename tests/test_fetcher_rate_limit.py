"""Focused tests for host backoff and retry behavior in the HTTP fetcher."""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

_HTML_BODY = b"<html><body><article><p>ok</p></article></body></html>"
_REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}
_SSL_BYPASS_REDIRECT_DNS = {
    "https://example.com/start": "https://93.184.216.34/start",
    "https://next.test/step": "https://93.184.216.35/step",
    "https://final.test/final": "https://93.184.216.36/final",
}


def _start_server(handler_cls: type[BaseHTTPRequestHandler]) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


class _FakeSSLFailureError(Exception):
    pass


class _SyncStreamResponse:
    charset_encoding = "utf-8"

    def __init__(
        self,
        url: str,
        status_code: int,
        headers: dict[str, str] | httpx.Headers,
        body: bytes = b"",
        *,
        response: httpx.Response | None = None,
    ):
        self._response = response
        self._body = body
        self.url = response.url if response is not None else url
        self.status_code = response.status_code if response is not None else status_code
        self.headers = response.headers if response is not None else headers
        self.is_redirect = self.status_code in _REDIRECT_STATUS_CODES

    @classmethod
    def from_response(cls, response: httpx.Response, body: bytes = b"") -> _SyncStreamResponse:
        return cls(
            str(response.url), response.status_code, response.headers, body, response=response
        )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._body

    def raise_for_status(self):
        if self._response is not None:
            self._response.raise_for_status()

    def iter_bytes(self):
        yield self._body


class _AsyncStreamResponse:
    charset_encoding = "utf-8"

    def __init__(
        self,
        url: str,
        status_code: int,
        headers: dict[str, str] | httpx.Headers,
        body: bytes = b"",
        *,
        response: httpx.Response | None = None,
    ):
        self._response = response
        self._body = body
        self.url = response.url if response is not None else url
        self.status_code = response.status_code if response is not None else status_code
        self.headers = response.headers if response is not None else headers
        self.is_redirect = self.status_code in _REDIRECT_STATUS_CODES

    @classmethod
    def from_response(cls, response: httpx.Response, body: bytes = b"") -> _AsyncStreamResponse:
        return cls(
            str(response.url), response.status_code, response.headers, body, response=response
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aread(self):
        return self._body

    def raise_for_status(self):
        if self._response is not None:
            self._response.raise_for_status()

    async def aiter_bytes(self):
        yield self._body


class _SyncSequenceClient:
    def __init__(self, *outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0
        self.requests: list[tuple[str, dict]] = []
        self.user_agents: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def close(self):
        return None

    def stream(self, method: str, url: str, **kwargs):
        self.calls += 1
        self.requests.append((url, kwargs))
        headers = kwargs.get("headers") or {}
        if "User-Agent" in headers:
            self.user_agents.append(headers["User-Agent"])
        outcome = self._outcomes[min(self.calls - 1, len(self._outcomes) - 1)]
        if isinstance(outcome, BaseException):
            raise outcome
        if callable(outcome):
            return outcome(url)
        return outcome


class _AsyncSequenceClient:
    def __init__(self, *outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0
        self.requests: list[tuple[str, dict]] = []
        self.user_agents: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aclose(self):
        return None

    def stream(self, method: str, url: str, **kwargs):
        self.calls += 1
        self.requests.append((url, kwargs))
        headers = kwargs.get("headers") or {}
        if "User-Agent" in headers:
            self.user_agents.append(headers["User-Agent"])
        outcome = self._outcomes[min(self.calls - 1, len(self._outcomes) - 1)]
        if isinstance(outcome, BaseException):
            raise outcome
        if callable(outcome):
            return outcome(url)
        return outcome


class _SyncRoutingClient:
    def __init__(self, routes: dict[str, _SyncStreamResponse]):
        self._routes = routes
        self.calls: list[tuple[str, dict]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def close(self):
        return None

    def stream(self, method: str, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self._routes[url]


class _AsyncRoutingClient:
    def __init__(self, routes: dict[str, _AsyncStreamResponse]):
        self._routes = routes
        self.calls: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aclose(self):
        return None

    def stream(self, method: str, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self._routes[url]


def _sync_status_stream(url: str, status_code: int, body: bytes) -> _SyncStreamResponse:
    response = httpx.Response(
        status_code,
        headers={"content-type": "text/html"},
        request=httpx.Request("GET", url),
        content=body,
    )
    return _SyncStreamResponse.from_response(response, body)


def _async_status_stream(url: str, status_code: int, body: bytes) -> _AsyncStreamResponse:
    response = httpx.Response(
        status_code,
        headers={"content-type": "text/html"},
        request=httpx.Request("GET", url),
        content=body,
    )
    return _AsyncStreamResponse.from_response(response, body)


def _async_client_factory(client):
    async def get_async_client():
        return client

    return get_async_client


async def _expect_async_fetch_error(fetcher, url: str, expected_error: type[Exception]) -> None:
    try:
        with pytest.raises(expected_error):
            await fetcher.fetch(url)
    finally:
        await fetcher.aclose()


def _map_pinned_redirect_url(url: str, *, allow_local_urls: bool = False) -> str:
    if url == "https://target.test:9443/final":
        return "https://203.0.113.10:9443/final"
    return url


def _map_ssl_bypass_redirect_url(url: str, *, allow_local_urls: bool = False) -> str:
    return _SSL_BYPASS_REDIRECT_DNS[url]


def test_fetcher_applies_retry_after_backoff_to_same_host():
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    call_times: list[float] = []

    class Handler(BaseHTTPRequestHandler):
        counter = 0

        def do_GET(self):
            Handler.counter += 1
            call_times.append(time.monotonic())
            if Handler.counter == 1:
                self.send_response(429)
                self.send_header("Retry-After", "1")
                self.end_headers()
                return
            body = b"<html><body><article><p>ok</p></article></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    server, url = _start_server(Handler)
    try:
        fetcher = Fetcher(timeout=3.0, domain_request_interval=0.0)
        first = fetcher.fetch_sync(url)
        second = fetcher.fetch_sync(url)
    finally:
        server.shutdown()
        server.server_close()

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(call_times) >= 3
    assert call_times[1] - call_times[0] >= 0.9


def test_fetcher_does_not_open_circuit_for_repeated_429_before_backoff():
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(429)
            self.send_header("Retry-After", "0")
            self.end_headers()

        def log_message(self, format, *args):
            return

    server, url = _start_server(Handler)
    try:
        fetcher = Fetcher(timeout=2.0, domain_request_interval=0.0, circuit_breaker_threshold=1)
        with pytest.raises(Exception) as first:
            fetcher.fetch_sync(url)
        with pytest.raises(Exception) as second:
            fetcher.fetch_sync(url)
    finally:
        server.shutdown()
        server.server_close()

    assert "429" in str(first.value)
    assert "429" in str(second.value)
    assert "circuit breaker open" not in str(second.value).lower()


def test_fetcher_sync_does_not_open_circuit_for_client_errors():
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/missing":
                self.send_response(404)
                self.end_headers()
                return
            body = b"<html><body><article><p>ok</p></article></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    server, url = _start_server(Handler)
    try:
        fetcher = Fetcher(
            timeout=2.0,
            domain_request_interval=0.0,
            circuit_breaker_threshold=2,
            allow_local_urls=True,
        )
        with pytest.raises(httpx.HTTPStatusError):
            fetcher.fetch_sync(f"{url}/missing")
        with pytest.raises(httpx.HTTPStatusError):
            fetcher.fetch_sync(f"{url}/missing")
        result = fetcher.fetch_sync(f"{url}/ok")
    finally:
        server.shutdown()
        server.server_close()

    assert result.status_code == 200


@pytest.mark.asyncio
async def test_fetcher_async_does_not_open_circuit_for_client_errors():
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/missing":
                self.send_response(404)
                self.end_headers()
                return
            body = b"<html><body><article><p>ok</p></article></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    server, url = _start_server(Handler)
    fetcher = Fetcher(
        timeout=2.0,
        domain_request_interval=0.0,
        circuit_breaker_threshold=2,
        allow_local_urls=True,
    )
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await fetcher.fetch(f"{url}/missing")
        with pytest.raises(httpx.HTTPStatusError):
            await fetcher.fetch(f"{url}/missing")
        result = await fetcher.fetch(f"{url}/ok")
    finally:
        await fetcher.aclose()
        server.shutdown()
        server.server_close()

    assert result.status_code == 200


def test_fetcher_applies_extra_backoff_for_known_host_suffix():
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    delayed = []
    fetcher = Fetcher(timeout=2.0, domain_request_interval=0.0)

    original_defer = Fetcher._defer_host

    def track_defer(self, host: str, delay_seconds: float):
        delayed.append((host, delay_seconds))
        return original_defer(self, host, delay_seconds)

    Fetcher._defer_host = track_defer  # type: ignore[method-assign]
    try:
        fetcher._record_soft_throttle("www.facebook.com", 0.5)
    finally:
        Fetcher._defer_host = original_defer  # type: ignore[method-assign]

    assert delayed
    assert delayed[0][0] == "www.facebook.com"
    assert delayed[0][1] >= 2.0


def test_fetcher_rotate_ua_false_uses_stable_user_agent_per_instance(monkeypatch):
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    monkeypatch.setattr(
        "markdown_ingress.adapters.fetching.httpx_fetcher.ADVANCED_USER_AGENTS",
        ["UA-1", "UA-2", "UA-3"],
    )

    fetcher = Fetcher(timeout=2.0, domain_request_interval=0.0, rotate_ua=False)
    values = {fetcher.user_agent for _ in range(10)}

    assert len(values) == 1


def test_fetcher_retryable_status_retries_with_different_user_agent(monkeypatch):
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    monkeypatch.setattr(
        "markdown_ingress.adapters.fetching.httpx_fetcher.ADVANCED_USER_AGENTS",
        ["UA-1", "UA-2"],
    )
    monkeypatch.setattr(
        "markdown_ingress.adapters.fetching.httpx_fetcher.time.sleep", lambda seconds: None
    )

    retry_request = httpx.Request("GET", "https://example.com/retry")
    retry_response = httpx.Response(
        403,
        headers={"content-type": "text/html"},
        request=retry_request,
    )
    success_response = httpx.Response(
        200,
        headers={
            "content-type": "text/html; charset=utf-8",
            "content-length": str(len(_HTML_BODY)),
        },
        request=retry_request,
    )

    client = _SyncSequenceClient(
        _SyncStreamResponse.from_response(retry_response),
        _SyncStreamResponse.from_response(success_response, _HTML_BODY),
    )
    fetcher = Fetcher(timeout=2.0, domain_request_interval=0.0, rotate_ua=True)
    monkeypatch.setattr(fetcher, "_get_sync_client", lambda: client)
    monkeypatch.setattr(fetcher, "_reserve_domain_slot", lambda host: 0.0)
    monkeypatch.setattr(fetcher, "_defer_host", lambda host, delay_seconds: None)
    monkeypatch.setattr(fetcher, "_validate_url", lambda url, allow_local_urls=False: url)

    result = fetcher.fetch_sync("https://example.com/retry")

    assert result.status_code == 200
    assert client.calls == 2
    assert len(client.user_agents) == 2
    assert client.user_agents[0] != client.user_agents[1]


def test_ssl_bypass_policy_requires_first_ssl_verification_failure():
    from markdown_ingress.adapters.fetching.http_support import (
        should_retry_with_ssl_bypass,
        ssl_bypass_retry_delay,
    )

    class FakeSSLFailureError(Exception):
        pass

    ssl_error = FakeSSLFailureError("handshake failed")
    certificate_error = RuntimeError("certificate verify failed")

    assert should_retry_with_ssl_bypass(
        allow_ssl_bypass=True,
        ssl_retried=False,
        exc=ssl_error,
    )
    assert should_retry_with_ssl_bypass(
        allow_ssl_bypass=True,
        ssl_retried=False,
        exc=certificate_error,
    )
    assert not should_retry_with_ssl_bypass(
        allow_ssl_bypass=False,
        ssl_retried=False,
        exc=ssl_error,
    )
    assert not should_retry_with_ssl_bypass(
        allow_ssl_bypass=True,
        ssl_retried=True,
        exc=ssl_error,
    )
    assert ssl_bypass_retry_delay(0) == 0.5
    assert ssl_bypass_retry_delay(3) == 2.0


@pytest.mark.parametrize("status_code", [500, 502, 504])
def test_fetch_sync_server_errors_open_circuit_breaker(monkeypatch, status_code):
    from markdown_ingress.adapters.fetching.httpx_fetcher import DomainCircuitOpenError, Fetcher

    monkeypatch.setattr(
        "markdown_ingress.adapters.fetching.httpx_fetcher.time.sleep", lambda seconds: None
    )

    request = httpx.Request("GET", "https://example.com/server-error")
    response = httpx.Response(
        status_code,
        headers={"content-type": "text/html"},
        request=request,
    )

    class MockStreamResponse:
        status_code = response.status_code
        headers = response.headers
        url = response.url
        charset_encoding = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b""

        def raise_for_status(self):
            response.raise_for_status()

        def iter_bytes(self):
            yield b""

    class SyncClient:
        def __init__(self):
            self.calls = 0

        def stream(self, method: str, url: str, **kwargs):
            self.calls += 1
            return MockStreamResponse()

    client = SyncClient()
    fetcher = Fetcher(
        timeout=2.0,
        domain_request_interval=0.0,
        circuit_breaker_threshold=1,
    )
    monkeypatch.setattr(fetcher, "_get_sync_client", lambda: client)
    monkeypatch.setattr(fetcher, "_reserve_domain_slot", lambda host: 0.0)
    monkeypatch.setattr(fetcher, "_validate_url", lambda url, allow_local_urls=False: url)

    with pytest.raises(DomainCircuitOpenError):
        fetcher.fetch_sync("https://example.com/server-error")

    assert client.calls == 1
    assert fetcher._open_until_by_host


def test_fetch_async_server_errors_open_circuit_breaker(monkeypatch):
    from markdown_ingress.adapters.fetching.httpx_fetcher import DomainCircuitOpenError, Fetcher

    async def fake_sleep(seconds: float):
        return None

    monkeypatch.setattr(
        "markdown_ingress.adapters.fetching.httpx_fetcher.asyncio.sleep", fake_sleep
    )

    request = httpx.Request("GET", "https://example.com/server-error")
    response = httpx.Response(
        500,
        headers={"content-type": "text/html"},
        request=request,
    )

    client = _AsyncSequenceClient(_AsyncStreamResponse.from_response(response))
    fetcher = Fetcher(
        timeout=2.0,
        domain_request_interval=0.0,
        circuit_breaker_threshold=1,
    )
    monkeypatch.setattr(fetcher, "_get_async_client", _async_client_factory(client))
    monkeypatch.setattr(fetcher, "_reserve_domain_slot", lambda host: 0.0)
    monkeypatch.setattr(fetcher, "_validate_url", lambda url, allow_local_urls=False: url)

    asyncio.run(
        _expect_async_fetch_error(
            fetcher,
            "https://example.com/server-error",
            DomainCircuitOpenError,
        )
    )

    assert client.calls == 1
    assert fetcher._open_until_by_host


def test_fetch_sync_uses_original_hostname_for_sni_after_dns_pinning(monkeypatch):
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    captured: dict[str, object] = {}
    reserved_hosts: list[str] = []

    class StreamResponse:
        status_code = 200
        headers = {"content-type": "text/html"}
        url = "https://93.184.216.34:8443/pinned"
        charset_encoding = "utf-8"

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield b"<html><body><article><p>ok</p></article></body></html>"

    class SyncClient:
        def stream(self, method: str, url: str, **kwargs):
            captured["method"] = method
            captured["url"] = url
            captured["kwargs"] = kwargs
            return StreamResponse()

    fetcher = Fetcher(timeout=2.0, domain_request_interval=0.0, rotate_ua=False)
    monkeypatch.setattr(fetcher, "_get_sync_client", lambda: SyncClient())
    monkeypatch.setattr(
        fetcher,
        "_reserve_domain_slot",
        lambda host: reserved_hosts.append(host) or 0.0,
    )
    monkeypatch.setattr(
        fetcher,
        "_validate_url",
        lambda url, allow_local_urls=False: "https://93.184.216.34:8443/pinned",
    )

    result = fetcher.fetch_sync("https://example.com:8443/pinned")

    kwargs = captured["kwargs"]
    assert result.status_code == 200
    assert captured["url"] == "https://93.184.216.34:8443/pinned"
    assert result.url == "https://example.com:8443/pinned"
    assert result.final_url == "https://example.com:8443/pinned"
    assert kwargs["headers"]["Host"] == "example.com:8443"
    assert kwargs["extensions"]["sni_hostname"] == b"example.com"
    assert reserved_hosts == ["example.com"]


def test_fetch_sync_redirect_uses_validated_pinned_url_and_original_sni(monkeypatch):
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    client = _SyncRoutingClient(
        {
            "https://start.test/page": _SyncStreamResponse(
                "https://start.test/page",
                302,
                {"location": "https://target.test:9443/final"},
            ),
            "https://203.0.113.10:9443/final": _SyncStreamResponse(
                "https://203.0.113.10:9443/final",
                200,
                {"content-type": "text/html"},
                _HTML_BODY,
            ),
        }
    )

    fetcher = Fetcher(timeout=2.0, domain_request_interval=0.0, rotate_ua=False)
    monkeypatch.setattr(fetcher, "_get_sync_client", lambda: client)
    monkeypatch.setattr(fetcher, "_reserve_domain_slot", lambda host: 0.0)
    monkeypatch.setattr(fetcher, "_validate_url", _map_pinned_redirect_url)

    result = fetcher.fetch_sync("https://start.test/page")

    assert result.status_code == 200
    assert result.url == "https://start.test/page"
    assert result.final_url == "https://target.test:9443/final"
    assert client.calls[0][0] == "https://start.test/page"
    assert client.calls[1][0] == "https://203.0.113.10:9443/final"
    assert client.calls[1][1]["headers"]["Host"] == "target.test:9443"
    assert client.calls[1][1]["extensions"]["sni_hostname"] == b"target.test"


def test_fetch_sync_invalid_redirect_location_is_not_retried_or_circuited():
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    class Handler(BaseHTTPRequestHandler):
        count = 0

        def do_GET(self):
            type(self).count += 1
            self.send_response(302)
            self.send_header("Location", "ftp://example.com/hook")
            self.end_headers()

        def log_message(self, format, *args):
            return

    server, url = _start_server(Handler)
    fetcher = Fetcher(
        timeout=2.0,
        domain_request_interval=0.0,
        circuit_breaker_threshold=1,
        circuit_breaker_open_seconds=60.0,
        allow_local_urls=True,
    )
    try:
        for _ in range(2):
            with pytest.raises(ValueError, match="Invalid URL scheme"):
                fetcher.fetch_sync(url)
    finally:
        fetcher.close()
        server.shutdown()
        server.server_close()

    assert Handler.count == 2


@pytest.mark.asyncio
async def test_fetcher_close_inside_running_loop_schedules_async_client_close():
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    closed = asyncio.Event()

    class FakeAsyncClient:
        async def aclose(self):
            closed.set()

    fetcher = Fetcher(timeout=2.0, domain_request_interval=0.0)
    fetcher._async_client = FakeAsyncClient()

    fetcher.close()

    await asyncio.wait_for(closed.wait(), timeout=1.0)
    await asyncio.sleep(0)
    assert fetcher._async_close_tasks == set()


def test_fetch_sync_follow_redirects_false_returns_redirect_response(monkeypatch):
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    calls: list[str] = []

    class StreamResponse:
        status_code = 302
        headers = {"location": "https://target.test/final"}
        url = "https://start.test/page"
        charset_encoding = "utf-8"
        is_redirect = True

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def iter_bytes(self):
            yield b"<html><body>redirect</body></html>"

    class SyncClient:
        def stream(self, method: str, url: str, **kwargs):
            calls.append(url)
            return StreamResponse()

    fetcher = Fetcher(timeout=2.0, follow_redirects=False, domain_request_interval=0.0)
    monkeypatch.setattr(fetcher, "_get_sync_client", lambda: SyncClient())
    monkeypatch.setattr(fetcher, "_reserve_domain_slot", lambda host: 0.0)
    monkeypatch.setattr(fetcher, "_validate_url", lambda url, allow_local_urls=False: url)

    result = fetcher.fetch_sync("https://start.test/page")

    assert result.status_code == 302
    assert calls == ["https://start.test/page"]


def test_fetch_sync_redirect_limit_is_enforced(monkeypatch):
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    class StreamResponse:
        status_code = 302
        headers = {"location": "https://target.test/final"}
        url = "https://start.test/page"
        charset_encoding = "utf-8"
        is_redirect = True

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class SyncClient:
        def stream(self, method: str, url: str, **kwargs):
            return StreamResponse()

    fetcher = Fetcher(timeout=2.0, max_redirects=0, domain_request_interval=0.0)
    monkeypatch.setattr(fetcher, "_get_sync_client", lambda: SyncClient())
    monkeypatch.setattr(fetcher, "_reserve_domain_slot", lambda host: 0.0)
    monkeypatch.setattr(fetcher, "_validate_url", lambda url, allow_local_urls=False: url)

    with pytest.raises(httpx.TooManyRedirects):
        fetcher.fetch_sync("https://start.test/page")


def test_fetch_sync_redirect_body_respects_max_response_size():
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher, ResponseSizeLimitError

    large_body = b"x" * 64

    class Handler(BaseHTTPRequestHandler):
        final_hits = 0

        def do_GET(self):
            if self.path == "/start":
                self.send_response(302)
                self.send_header("Location", "/final")
                self.send_header("Content-Length", str(len(large_body)))
                self.end_headers()
                self.wfile.write(large_body)
                return
            Handler.final_hits += 1
            body = b"<html><body><article><p>ok</p></article></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    server, url = _start_server(Handler)
    try:
        fetcher = Fetcher(
            timeout=2.0,
            domain_request_interval=0.0,
            max_response_size=32,
            allow_local_urls=True,
        )
        with pytest.raises(ResponseSizeLimitError, match="exceeds max_response_size"):
            fetcher.fetch_sync(f"{url}/start")
    finally:
        server.shutdown()
        server.server_close()

    assert Handler.final_hits == 0


def test_fetch_async_redirect_uses_validated_pinned_url_and_original_sni(monkeypatch):
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    client = _AsyncRoutingClient(
        {
            "https://start.test/page": _AsyncStreamResponse(
                "https://start.test/page",
                302,
                {"location": "https://target.test:9443/final"},
            ),
            "https://203.0.113.10:9443/final": _AsyncStreamResponse(
                "https://203.0.113.10:9443/final",
                200,
                {"content-type": "text/html"},
                _HTML_BODY,
            ),
        }
    )

    async def run():
        fetcher = Fetcher(timeout=2.0, domain_request_interval=0.0, rotate_ua=False)
        monkeypatch.setattr(fetcher, "_get_async_client", _async_client_factory(client))
        monkeypatch.setattr(fetcher, "_reserve_domain_slot", lambda host: 0.0)
        monkeypatch.setattr(fetcher, "_validate_url", _map_pinned_redirect_url)
        return await fetcher.fetch("https://start.test/page")

    result = asyncio.run(run())

    assert result.status_code == 200
    assert result.url == "https://start.test/page"
    assert result.final_url == "https://target.test:9443/final"
    assert client.calls[0][0] == "https://start.test/page"
    assert client.calls[1][0] == "https://203.0.113.10:9443/final"
    assert client.calls[1][1]["headers"]["Host"] == "target.test:9443"
    assert client.calls[1][1]["extensions"]["sni_hostname"] == b"target.test"


@pytest.mark.asyncio
async def test_fetch_async_retryable_body_respects_max_response_size():
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher, ResponseSizeLimitError

    large_body = b"x" * 64

    class Handler(BaseHTTPRequestHandler):
        calls = 0

        def do_GET(self):
            Handler.calls += 1
            if Handler.calls == 1:
                self.send_response(429)
                self.send_header("Retry-After", "0")
                self.end_headers()
                self.wfile.write(large_body)
                return
            body = b"<html><body><article><p>ok</p></article></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    server, url = _start_server(Handler)
    try:
        async with Fetcher(
            timeout=2.0,
            domain_request_interval=0.0,
            max_response_size=32,
            allow_local_urls=True,
        ) as fetcher:
            with pytest.raises(ResponseSizeLimitError, match="exceeds max_response_size"):
                await fetcher.fetch(url)
    finally:
        server.shutdown()
        server.server_close()

    assert Handler.calls == 1


def test_fetch_async_invalid_redirect_location_is_not_retried_or_circuited():
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    class Handler(BaseHTTPRequestHandler):
        count = 0

        def do_GET(self):
            type(self).count += 1
            self.send_response(302)
            self.send_header("Location", "ftp://example.com/hook")
            self.end_headers()

        def log_message(self, format, *args):
            return

    async def run(url: str) -> None:
        fetcher = Fetcher(
            timeout=2.0,
            domain_request_interval=0.0,
            circuit_breaker_threshold=1,
            circuit_breaker_open_seconds=60.0,
            allow_local_urls=True,
        )
        try:
            for _ in range(2):
                with pytest.raises(ValueError, match="Invalid URL scheme"):
                    await fetcher.fetch(url)
        finally:
            await fetcher.aclose()

    server, url = _start_server(Handler)
    try:
        asyncio.run(run(url))
    finally:
        server.shutdown()
        server.server_close()

    assert Handler.count == 2


def test_fetcher_instance_state_does_not_leak_between_configs():
    from markdown_ingress.adapters.fetching.httpx_fetcher import DomainCircuitOpenError, Fetcher

    strict_fetcher = Fetcher(timeout=2.0, domain_request_interval=0.0, circuit_breaker_threshold=1)
    relaxed_fetcher = Fetcher(timeout=2.0, domain_request_interval=0.0, circuit_breaker_threshold=5)

    strict_fetcher._record_failure("unit.test")

    with pytest.raises(DomainCircuitOpenError):
        strict_fetcher._ensure_circuit_closed("unit.test")

    relaxed_fetcher._ensure_circuit_closed("unit.test")


def test_fetcher_rejects_non_positive_max_response_size():
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    with pytest.raises(ValueError, match="max_response_size"):
        Fetcher(max_response_size=0)


def test_fetcher_host_key_ignores_userinfo_and_port():
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    assert Fetcher._host_key("https://user:pass@example.com:443/path") == "example.com"
    assert Fetcher._host_key("https://EXAMPLE.com:8443/path") == "example.com"
    assert Fetcher._host_key("https://example.com./path") == "example.com"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/",
        "http://10.0.0.1/",
        "http://localhost/",
        "http://localhost./",
        "http://metadata.google.internal/",
        "http://metadata.azure.net/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
    ],
)
def test_fetcher_blocks_ssrf_targets_by_default(monkeypatch, url: str):
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    monkeypatch.delenv("MDI_ALLOW_LOCAL_URLS", raising=False)

    with pytest.raises(ValueError, match="SSRF protection"):
        Fetcher._validate_url(url)


def test_fetcher_can_explicitly_allow_local_urls():
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    assert (
        Fetcher._validate_url("http://127.0.0.1:8000/", allow_local_urls=True)
        == "http://127.0.0.1:8000/"
    )


def test_fetcher_blocks_hostname_that_resolves_to_private_ip(monkeypatch):
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    def fake_getaddrinfo(host, port, *args, **kwargs):
        assert host == "public.example"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(ValueError, match="SSRF protection"):
        Fetcher._validate_url("http://public.example/path")


def test_fetcher_allows_hostname_that_resolves_to_public_ip(monkeypatch):
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    def fake_getaddrinfo(host, port, *args, **kwargs):
        assert host == "public.example"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    # DNS rebinding protection returns IP-pinned URL instead of hostname
    result = Fetcher._validate_url("http://public.example/path")
    assert result == "http://93.184.216.34/path"


def test_fetcher_rejects_unresolvable_hostname(monkeypatch):
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    def fake_getaddrinfo(host, port, *args, **kwargs):
        raise socket.gaierror("not found")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(ValueError, match="could not be resolved"):
        Fetcher._validate_url("http://missing.example/path")


@pytest.mark.parametrize("url", ["https://:443/path", "https://user:pass@/path"])
def test_fetcher_rejects_urls_without_hostname(url: str):
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    with pytest.raises(ValueError, match="valid host"):
        Fetcher._validate_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com:0/",
        "http://example.com:65536/",
        "http://example.com:abc/",
        "http://[2001:db8::1]:0/",
        "http://[2001:db8::1]:65536/",
        "http://[2001:db8::1]:abc/",
    ],
)
def test_fetcher_rejects_invalid_ports(url: str):
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    with pytest.raises(ValueError, match=r"(?i)port"):
        Fetcher._validate_url(url)


def test_fetcher_applies_soft_throttle_to_final_redirect_host():
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    class FinalHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(429)
            self.send_header("Retry-After", "0")
            self.end_headers()

        def log_message(self, format, *args):
            return

    final_server, final_url = _start_server(FinalHandler)

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", final_url)
            self.end_headers()

        def log_message(self, format, *args):
            return

    redirect_server, redirect_url = _start_server(RedirectHandler)
    final_host = Fetcher._host_key(final_url)
    throttled_hosts: list[str] = []
    original = Fetcher._record_soft_throttle

    def track(self, host: str, delay_seconds: float):
        throttled_hosts.append(host)
        return original(self, host, delay_seconds)

    Fetcher._record_soft_throttle = track  # type: ignore[method-assign]
    try:
        fetcher = Fetcher(timeout=2.0, domain_request_interval=0.0)
        with pytest.raises(Exception):
            fetcher.fetch_sync(redirect_url)
    finally:
        Fetcher._record_soft_throttle = original  # type: ignore[method-assign]
        redirect_server.shutdown()
        redirect_server.server_close()
        final_server.shutdown()
        final_server.server_close()

    assert throttled_hosts
    assert all(host == final_host for host in throttled_hosts)


def test_fetcher_records_success_on_final_redirect_host_async():
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    class FinalHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"<html><body><article><p>ok</p></article></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    final_server, final_url = _start_server(FinalHandler)

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", final_url)
            self.end_headers()

        def log_message(self, format, *args):
            return

    redirect_server, redirect_url = _start_server(RedirectHandler)
    final_host = Fetcher._host_key(final_url)
    success_hosts: list[str] = []
    original = Fetcher._record_success

    def track(self, host: str):
        success_hosts.append(host)
        return original(self, host)

    Fetcher._record_success = track  # type: ignore[method-assign]
    try:

        async def run():
            fetcher = Fetcher(timeout=2.0, domain_request_interval=0.0)
            try:
                return await fetcher.fetch(redirect_url)
            finally:
                await fetcher.aclose()

        result = asyncio.run(run())
    finally:
        Fetcher._record_success = original  # type: ignore[method-assign]
        redirect_server.shutdown()
        redirect_server.server_close()
        final_server.shutdown()
        final_server.server_close()

    assert result.status_code == 200
    assert success_hosts[-1] == final_host


def test_sync_ssl_bypass_retries_with_remaining_attempts(monkeypatch):
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    first_retryable = httpx.Response(
        503,
        headers={"content-type": "text/html"},
        request=httpx.Request("GET", "https://unit.test/ssl"),
    )
    bypass_retryable = httpx.Response(
        503,
        headers={"content-type": "text/html"},
        request=httpx.Request("GET", "https://unit.test/ssl-final"),
    )

    sleep_calls: list[float] = []
    monkeypatch.setattr(
        "markdown_ingress.adapters.fetching.httpx_fetcher.time.sleep",
        lambda seconds: sleep_calls.append(seconds),
    )
    bypass_client = _SyncSequenceClient(_SyncStreamResponse.from_response(bypass_retryable))
    monkeypatch.setattr(
        "markdown_ingress.adapters.fetching.httpx_fetcher.httpx.Client",
        lambda *args, **kwargs: bypass_client,
    )

    fetcher = Fetcher(timeout=2.0, domain_request_interval=0.0, allow_ssl_bypass=True)
    main_client = _SyncSequenceClient(
        _SyncStreamResponse.from_response(first_retryable),
        _FakeSSLFailureError("SSL handshake failed"),
    )
    monkeypatch.setattr(fetcher, "_get_sync_client", lambda: main_client)
    monkeypatch.setattr(fetcher, "_reserve_domain_slot", lambda host: 0.0)
    monkeypatch.setattr(fetcher, "_defer_host", lambda host, delay_seconds: None)

    with pytest.raises(Exception):
        fetcher.fetch_sync("https://unit.test/ssl")

    assert main_client.calls == 2
    assert bypass_client.calls == 1
    assert len(sleep_calls) == 1


def test_sync_ssl_bypass_preserves_sni_after_dns_pinning(monkeypatch):
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    captured: dict[str, object] = {}

    class FakeSSLFailureError(Exception):
        pass

    class MainClient:
        def stream(self, method: str, url: str, **kwargs):
            raise FakeSSLFailureError("SSL handshake failed")

    class StreamResponse:
        status_code = 200
        headers = {"content-type": "text/html"}
        url = "https://93.184.216.34/ssl"
        charset_encoding = "utf-8"

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield b"<html><body><article><p>ok</p></article></body></html>"

    class BypassClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, method: str, url: str, **kwargs):
            captured["method"] = method
            captured["url"] = url
            captured["kwargs"] = kwargs
            return StreamResponse()

    bypass_client = BypassClient()
    monkeypatch.setattr(
        "markdown_ingress.adapters.fetching.httpx_fetcher.httpx.Client",
        lambda *args, **kwargs: bypass_client,
    )

    fetcher = Fetcher(timeout=2.0, domain_request_interval=0.0, allow_ssl_bypass=True)
    monkeypatch.setattr(fetcher, "_get_sync_client", lambda: MainClient())
    monkeypatch.setattr(fetcher, "_reserve_domain_slot", lambda host: 0.0)
    monkeypatch.setattr(
        fetcher,
        "_validate_url",
        lambda url, allow_local_urls=False: "https://93.184.216.34/ssl",
    )

    result = fetcher.fetch_sync("https://example.com/ssl")

    kwargs = captured["kwargs"]
    assert result.status_code == 200
    assert captured["url"] == "https://93.184.216.34/ssl"
    assert result.url == "https://example.com/ssl"
    assert result.final_url == "https://example.com/ssl"
    assert kwargs["headers"]["Host"] == "example.com"
    assert kwargs["extensions"]["sni_hostname"] == b"example.com"


def test_sync_ssl_bypass_does_not_open_circuit_for_client_errors(monkeypatch):
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    class StreamResponse:
        status_code = 404
        headers = {"content-type": "text/html"}
        charset_encoding = "utf-8"

        def __init__(self, url: str):
            self.url = url
            self._response = httpx.Response(
                404,
                request=httpx.Request("GET", url),
                content=b"not found",
            )

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            self._response.raise_for_status()

        def iter_bytes(self):
            yield b"not found"

    class BypassClient:
        calls = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def close(self):
            return None

        def stream(self, method: str, url: str, **kwargs):
            type(self).calls += 1
            return StreamResponse(url)

    monkeypatch.setattr(
        "markdown_ingress.adapters.fetching.httpx_fetcher.httpx.Client",
        lambda *args, **kwargs: BypassClient(),
    )

    fetcher = Fetcher(
        timeout=2.0,
        domain_request_interval=0.0,
        allow_ssl_bypass=True,
        circuit_breaker_threshold=1,
    )
    monkeypatch.setattr(
        fetcher,
        "_prepare_request_url",
        lambda url: (url, url, None, None, "example.com"),
    )
    monkeypatch.setattr(fetcher, "_is_ssl_bypass_active", lambda host: True)

    try:
        with pytest.raises(httpx.HTTPStatusError):
            fetcher.fetch_sync("https://example.com/missing")
        with pytest.raises(httpx.HTTPStatusError):
            fetcher.fetch_sync("https://example.com/missing")
    finally:
        fetcher.close()

    assert BypassClient.calls == 2
    assert fetcher._open_until_by_host == {}


def test_sync_ssl_bypass_opens_circuit_for_server_errors(monkeypatch):
    from markdown_ingress.adapters.fetching.httpx_fetcher import DomainCircuitOpenError, Fetcher

    monkeypatch.setattr(
        "markdown_ingress.adapters.fetching.httpx_fetcher.time.sleep", lambda seconds: None
    )

    bypass_client = _SyncSequenceClient(lambda url: _sync_status_stream(url, 500, b"server error"))
    monkeypatch.setattr(
        "markdown_ingress.adapters.fetching.httpx_fetcher.httpx.Client",
        lambda *args, **kwargs: bypass_client,
    )

    fetcher = Fetcher(
        timeout=2.0,
        domain_request_interval=0.0,
        allow_ssl_bypass=True,
        circuit_breaker_threshold=1,
    )
    monkeypatch.setattr(
        fetcher,
        "_prepare_request_url",
        lambda url: (url, url, None, None, "example.com"),
    )
    monkeypatch.setattr(fetcher, "_is_ssl_bypass_active", lambda host: True)

    try:
        with pytest.raises(DomainCircuitOpenError):
            fetcher.fetch_sync("https://example.com/server-error")
    finally:
        fetcher.close()

    assert bypass_client.calls == 1
    assert fetcher._open_until_by_host


def test_sync_ssl_bypass_redirects_do_not_consume_retry_attempts(monkeypatch):
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    bypass_client = _SyncRoutingClient(
        {
            "https://93.184.216.34/start": _SyncStreamResponse(
                "https://93.184.216.34/start",
                302,
                {"location": "https://next.test/step"},
            ),
            "https://93.184.216.35/step": _SyncStreamResponse(
                "https://93.184.216.35/step",
                302,
                {"location": "https://final.test/final"},
            ),
            "https://93.184.216.36/final": _SyncStreamResponse(
                "https://93.184.216.36/final",
                200,
                {"content-type": "text/html"},
                _HTML_BODY,
            ),
        }
    )

    monkeypatch.setattr(
        "markdown_ingress.adapters.fetching.httpx_fetcher.httpx.Client",
        lambda *args, **kwargs: bypass_client,
    )
    fetcher = Fetcher(timeout=2.0, domain_request_interval=0.0, allow_ssl_bypass=True)
    monkeypatch.setattr(
        fetcher,
        "_get_sync_client",
        lambda: _SyncSequenceClient(_FakeSSLFailureError("SSL handshake failed")),
    )
    monkeypatch.setattr(fetcher, "_reserve_domain_slot", lambda host: 0.0)
    monkeypatch.setattr(fetcher, "_validate_url", _map_ssl_bypass_redirect_url)

    result = fetcher.fetch_sync("https://example.com/start")

    assert result.status_code == 200
    assert result.url == "https://example.com/start"
    assert result.final_url == "https://final.test/final"
    assert [url for url, _ in bypass_client.calls] == [
        "https://93.184.216.34/start",
        "https://93.184.216.35/step",
        "https://93.184.216.36/final",
    ]
    assert bypass_client.calls[-1][1]["headers"]["Host"] == "final.test"
    assert bypass_client.calls[-1][1]["extensions"]["sni_hostname"] == b"final.test"


def test_async_ssl_bypass_retries_with_remaining_attempts(monkeypatch):
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    first_retryable = httpx.Response(
        503,
        headers={"content-type": "text/html"},
        request=httpx.Request("GET", "https://unit.test/ssl"),
    )
    bypass_retryable = httpx.Response(
        503,
        headers={"content-type": "text/html"},
        request=httpx.Request("GET", "https://unit.test/ssl-final"),
    )

    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float):
        sleep_calls.append(seconds)

    bypass_client = _AsyncSequenceClient(_AsyncStreamResponse.from_response(bypass_retryable))
    main_client = _AsyncSequenceClient(
        _AsyncStreamResponse.from_response(first_retryable),
        _FakeSSLFailureError("SSL handshake failed"),
    )
    monkeypatch.setattr(
        "markdown_ingress.adapters.fetching.httpx_fetcher.asyncio.sleep", fake_sleep
    )
    monkeypatch.setattr(
        "markdown_ingress.adapters.fetching.httpx_fetcher.httpx.AsyncClient",
        lambda *args, **kwargs: bypass_client,
    )

    fetcher = Fetcher(timeout=2.0, domain_request_interval=0.0, allow_ssl_bypass=True)
    monkeypatch.setattr(fetcher, "_get_async_client", _async_client_factory(main_client))
    monkeypatch.setattr(fetcher, "_reserve_domain_slot", lambda host: 0.0)
    monkeypatch.setattr(fetcher, "_defer_host", lambda host, delay_seconds: None)

    async def run():
        with pytest.raises(Exception):
            await fetcher.fetch("https://unit.test/ssl")

    asyncio.run(run())

    assert main_client.calls == 2
    assert bypass_client.calls == 1
    assert len(sleep_calls) == 1


def test_async_ssl_bypass_does_not_open_circuit_for_client_errors(monkeypatch):
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    bypass_client = _AsyncSequenceClient(lambda url: _async_status_stream(url, 404, b"not found"))
    monkeypatch.setattr(
        "markdown_ingress.adapters.fetching.httpx_fetcher.httpx.AsyncClient",
        lambda *args, **kwargs: bypass_client,
    )

    fetcher = Fetcher(
        timeout=2.0,
        domain_request_interval=0.0,
        allow_ssl_bypass=True,
        circuit_breaker_threshold=1,
    )
    monkeypatch.setattr(
        fetcher,
        "_prepare_request_url",
        lambda url: (url, url, None, None, "example.com"),
    )
    monkeypatch.setattr(fetcher, "_is_ssl_bypass_active", lambda host: True)

    async def run():
        try:
            with pytest.raises(httpx.HTTPStatusError):
                await fetcher.fetch("https://example.com/missing")
            with pytest.raises(httpx.HTTPStatusError):
                await fetcher.fetch("https://example.com/missing")
        finally:
            await fetcher.aclose()

    asyncio.run(run())

    assert bypass_client.calls == 2
    assert fetcher._open_until_by_host == {}


def test_async_ssl_bypass_redirects_do_not_consume_retry_attempts(monkeypatch):
    from markdown_ingress.adapters.fetching.httpx_fetcher import Fetcher

    bypass_client = _AsyncRoutingClient(
        {
            "https://93.184.216.34/start": _AsyncStreamResponse(
                "https://93.184.216.34/start",
                302,
                {"location": "https://next.test/step"},
            ),
            "https://93.184.216.35/step": _AsyncStreamResponse(
                "https://93.184.216.35/step",
                302,
                {"location": "https://final.test/final"},
            ),
            "https://93.184.216.36/final": _AsyncStreamResponse(
                "https://93.184.216.36/final",
                200,
                {"content-type": "text/html"},
                _HTML_BODY,
            ),
        }
    )

    monkeypatch.setattr(
        "markdown_ingress.adapters.fetching.httpx_fetcher.httpx.AsyncClient",
        lambda *args, **kwargs: bypass_client,
    )
    fetcher = Fetcher(timeout=2.0, domain_request_interval=0.0, allow_ssl_bypass=True)
    monkeypatch.setattr(
        fetcher,
        "_get_async_client",
        _async_client_factory(_AsyncSequenceClient(_FakeSSLFailureError("SSL handshake failed"))),
    )
    monkeypatch.setattr(fetcher, "_reserve_domain_slot", lambda host: 0.0)
    monkeypatch.setattr(fetcher, "_validate_url", _map_ssl_bypass_redirect_url)

    result = asyncio.run(fetcher.fetch("https://example.com/start"))

    assert result.status_code == 200
    assert result.url == "https://example.com/start"
    assert result.final_url == "https://final.test/final"
    assert [url for url, _ in bypass_client.calls] == [
        "https://93.184.216.34/start",
        "https://93.184.216.35/step",
        "https://93.184.216.36/final",
    ]
    assert bypass_client.calls[-1][1]["headers"]["Host"] == "final.test"
    assert bypass_client.calls[-1][1]["extensions"]["sni_hostname"] == b"final.test"


def test_fetcher_sync_rechecks_circuit_breaker_after_rate_limit_sleep(monkeypatch):
    from markdown_ingress.adapters.fetching.httpx_fetcher import DomainCircuitOpenError, Fetcher

    url = "https://example.com/recheck"
    host = Fetcher._host_key(url)

    class MockStreamResponse:
        """Mock response that supports streaming interface."""

        def __init__(self, content, status_code, headers, url):
            self._content = content
            self.status_code = status_code
            self.headers = headers
            self._url = url
            self.charset_encoding = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            pass

        def iter_bytes(self):
            yield self._content

    class SyncClient:
        def __init__(self):
            self.calls = 0

        def stream(self, method: str, url: str, headers=None):
            self.calls += 1
            body = b"<html><body><article><p>ok</p></article></body></html>"
            return MockStreamResponse(
                body,
                200,
                {"content-type": "text/html", "content-length": str(len(body))},
                url,
            )

    client = SyncClient()
    fetcher = Fetcher(timeout=2.0, domain_request_interval=0.0, circuit_breaker_threshold=1)
    monkeypatch.setattr(fetcher, "_get_sync_client", lambda: client)
    monkeypatch.setattr(fetcher, "_reserve_domain_slot", lambda host: 0.1)

    def fake_sleep(seconds: float):
        with fetcher._failure_lock:
            fetcher._open_until_by_host[host] = time.monotonic() + 60.0

    monkeypatch.setattr("markdown_ingress.adapters.fetching.httpx_fetcher.time.sleep", fake_sleep)

    with pytest.raises(DomainCircuitOpenError):
        fetcher.fetch_sync(url)

    assert client.calls == 0


def test_circuit_breaker_opens_with_spaced_failures(monkeypatch):
    """Circuit breaker must open even when failures are spread over time."""
    from markdown_ingress.adapters.fetching.httpx_fetcher import DomainCircuitOpenError, Fetcher

    fetcher = Fetcher(
        timeout=2.0,
        domain_request_interval=0.0,
        circuit_breaker_threshold=3,
        failure_decay_seconds=300.0,
    )
    host = "slow-fail.test"

    # Simulate failures spaced 5 seconds apart using monotonic time patches
    fake_now = [0.0]

    def patched_monotonic():
        return fake_now[0]

    monkeypatch.setattr(time, "monotonic", patched_monotonic)

    fake_now[0] = 1000.0
    fetcher._record_failure(host)

    fake_now[0] = 1005.0
    fetcher._record_failure(host)

    fake_now[0] = 1010.0
    fetcher._record_failure(host)

    with pytest.raises(DomainCircuitOpenError):
        fetcher._ensure_circuit_closed(host)
