"""Focused tests for host backoff and retry behavior in the HTTP fetcher."""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest


def _start_server(handler_cls: type[BaseHTTPRequestHandler]) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def test_fetcher_applies_retry_after_backoff_to_same_host():
    from markdown_ingress.core.fetcher import Fetcher

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
    from markdown_ingress.core.fetcher import Fetcher

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


def test_fetcher_applies_extra_backoff_for_known_host_suffix():
    from markdown_ingress.core.fetcher import Fetcher

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
    from markdown_ingress.core.fetcher import Fetcher

    monkeypatch.setattr(
        "markdown_ingress.core.fetcher.ADVANCED_USER_AGENTS",
        ["UA-1", "UA-2", "UA-3"],
    )

    fetcher = Fetcher(timeout=2.0, domain_request_interval=0.0, rotate_ua=False)
    values = {fetcher.user_agent for _ in range(10)}

    assert len(values) == 1


def test_fetcher_retryable_status_retries_with_different_user_agent(monkeypatch):
    from markdown_ingress.core.fetcher import Fetcher

    monkeypatch.setattr(
        "markdown_ingress.core.fetcher.ADVANCED_USER_AGENTS",
        ["UA-1", "UA-2"],
    )
    monkeypatch.setattr("markdown_ingress.core.fetcher.time.sleep", lambda seconds: None)

    retry_request = httpx.Request("GET", "https://example.com/retry")
    retry_response = httpx.Response(
        403,
        headers={"content-type": "text/html"},
        request=retry_request,
    )
    success_body = b"<html><body><article><p>ok</p></article></body></html>"
    success_response = httpx.Response(
        200,
        headers={
            "content-type": "text/html; charset=utf-8",
            "content-length": str(len(success_body)),
        },
        request=retry_request,
    )

    class MockStreamResponse:
        def __init__(self, response: httpx.Response, body: bytes = b""):
            self._response = response
            self.status_code = response.status_code
            self.headers = response.headers
            self.url = response.url
            self.charset_encoding = None
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            self._response.raise_for_status()

        def read(self):
            return self._body

        def iter_bytes(self):
            if self._body:
                yield self._body

    class SyncClient:
        def __init__(self):
            self.calls = 0
            self.user_agents: list[str] = []

        def stream(self, method: str, url: str, headers=None):
            self.calls += 1
            self.user_agents.append(headers["User-Agent"])
            if self.calls == 1:
                return MockStreamResponse(retry_response)
            return MockStreamResponse(success_response, success_body)

    client = SyncClient()
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


def test_fetcher_instance_state_does_not_leak_between_configs():
    from markdown_ingress.core.fetcher import DomainCircuitOpenError, Fetcher

    strict_fetcher = Fetcher(timeout=2.0, domain_request_interval=0.0, circuit_breaker_threshold=1)
    relaxed_fetcher = Fetcher(timeout=2.0, domain_request_interval=0.0, circuit_breaker_threshold=5)

    strict_fetcher._record_failure("unit.test")

    with pytest.raises(DomainCircuitOpenError):
        strict_fetcher._ensure_circuit_closed("unit.test")

    relaxed_fetcher._ensure_circuit_closed("unit.test")


def test_fetcher_rejects_non_positive_max_response_size():
    from markdown_ingress.core.fetcher import Fetcher

    with pytest.raises(ValueError, match="max_response_size"):
        Fetcher(max_response_size=0)


def test_fetcher_host_key_ignores_userinfo_and_port():
    from markdown_ingress.core.fetcher import Fetcher

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
    from markdown_ingress.core.fetcher import Fetcher

    monkeypatch.delenv("MDI_ALLOW_LOCAL_URLS", raising=False)

    with pytest.raises(ValueError, match="SSRF protection"):
        Fetcher._validate_url(url)


def test_fetcher_can_explicitly_allow_local_urls():
    from markdown_ingress.core.fetcher import Fetcher

    assert (
        Fetcher._validate_url("http://127.0.0.1:8000/", allow_local_urls=True)
        == "http://127.0.0.1:8000/"
    )


def test_fetcher_blocks_hostname_that_resolves_to_private_ip(monkeypatch):
    from markdown_ingress.core.fetcher import Fetcher

    def fake_getaddrinfo(host, port, *args, **kwargs):
        assert host == "public.example"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(ValueError, match="SSRF protection"):
        Fetcher._validate_url("http://public.example/path")


def test_fetcher_allows_hostname_that_resolves_to_public_ip(monkeypatch):
    from markdown_ingress.core.fetcher import Fetcher

    def fake_getaddrinfo(host, port, *args, **kwargs):
        assert host == "public.example"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    # DNS rebinding protection returns IP-pinned URL instead of hostname
    result = Fetcher._validate_url("http://public.example/path")
    assert result == "http://93.184.216.34/path"


def test_fetcher_rejects_unresolvable_hostname(monkeypatch):
    from markdown_ingress.core.fetcher import Fetcher

    def fake_getaddrinfo(host, port, *args, **kwargs):
        raise socket.gaierror("not found")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(ValueError, match="could not be resolved"):
        Fetcher._validate_url("http://missing.example/path")


@pytest.mark.parametrize("url", ["https://:443/path", "https://user:pass@/path"])
def test_fetcher_rejects_urls_without_hostname(url: str):
    from markdown_ingress.core.fetcher import Fetcher

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
    from markdown_ingress.core.fetcher import Fetcher

    with pytest.raises(ValueError, match=r"(?i)port"):
        Fetcher._validate_url(url)


def test_fetcher_applies_soft_throttle_to_final_redirect_host():
    from markdown_ingress.core.fetcher import Fetcher

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
    from markdown_ingress.core.fetcher import Fetcher

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
    from markdown_ingress.core.fetcher import Fetcher

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

    class FakeSSLFailureError(Exception):
        pass

    class MockStreamResponse:
        """Mock response that supports streaming interface."""

        def __init__(self, response):
            self._response = response
            self.status_code = response.status_code
            self.headers = response.headers
            self.url = response.url
            self.charset_encoding = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            self._response.raise_for_status()

        def iter_bytes(self):
            yield b""

    class MainClient:
        def __init__(self):
            self.calls = 0

        def stream(self, method: str, url: str, headers=None):
            self.calls += 1
            if self.calls == 1:
                return MockStreamResponse(first_retryable)
            raise FakeSSLFailureError("SSL handshake failed")

    class BypassClient:
        def __init__(self):
            self.calls = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, method: str, url: str, headers=None):
            self.calls += 1
            return MockStreamResponse(bypass_retryable)

    sleep_calls: list[float] = []
    monkeypatch.setattr(
        "markdown_ingress.core.fetcher.time.sleep", lambda seconds: sleep_calls.append(seconds)
    )
    bypass_client = BypassClient()
    monkeypatch.setattr(
        "markdown_ingress.core.fetcher.httpx.Client", lambda *args, **kwargs: bypass_client
    )

    fetcher = Fetcher(timeout=2.0, domain_request_interval=0.0, allow_ssl_bypass=True)
    main_client = MainClient()
    monkeypatch.setattr(fetcher, "_get_sync_client", lambda: main_client)
    monkeypatch.setattr(fetcher, "_reserve_domain_slot", lambda host: 0.0)
    monkeypatch.setattr(fetcher, "_defer_host", lambda host, delay_seconds: None)

    with pytest.raises(Exception):
        fetcher.fetch_sync("https://unit.test/ssl")

    assert main_client.calls == 2
    assert bypass_client.calls == 1
    assert len(sleep_calls) == 1


def test_async_ssl_bypass_retries_with_remaining_attempts(monkeypatch):
    from markdown_ingress.core.fetcher import Fetcher

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

    class FakeSSLFailureError(Exception):
        pass

    class MockAsyncStreamResponse:
        """Mock response that supports async streaming interface."""

        def __init__(self, response):
            self._response = response
            self.status_code = response.status_code
            self.headers = response.headers
            self.url = response.url
            self.charset_encoding = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            self._response.raise_for_status()

        async def aiter_bytes(self):
            yield b""

    class MainClient:
        def __init__(self):
            self.calls = 0

        def stream(self, method: str, url: str, headers=None):
            self.calls += 1
            if self.calls == 1:
                return MockAsyncStreamResponse(first_retryable)
            raise FakeSSLFailureError("SSL handshake failed")

    class BypassClient:
        def __init__(self):
            self.calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method: str, url: str, headers=None):
            self.calls += 1
            return MockAsyncStreamResponse(bypass_retryable)

    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float):
        sleep_calls.append(seconds)

    async def get_async_client():
        return main_client

    bypass_client = BypassClient()
    main_client = MainClient()
    monkeypatch.setattr("markdown_ingress.core.fetcher.asyncio.sleep", fake_sleep)
    monkeypatch.setattr(
        "markdown_ingress.core.fetcher.httpx.AsyncClient", lambda *args, **kwargs: bypass_client
    )

    fetcher = Fetcher(timeout=2.0, domain_request_interval=0.0, allow_ssl_bypass=True)
    monkeypatch.setattr(fetcher, "_get_async_client", get_async_client)
    monkeypatch.setattr(fetcher, "_reserve_domain_slot", lambda host: 0.0)
    monkeypatch.setattr(fetcher, "_defer_host", lambda host, delay_seconds: None)

    async def run():
        with pytest.raises(Exception):
            await fetcher.fetch("https://unit.test/ssl")

    asyncio.run(run())

    assert main_client.calls == 2
    assert bypass_client.calls == 1
    assert len(sleep_calls) == 1


def test_fetcher_sync_rechecks_circuit_breaker_after_rate_limit_sleep(monkeypatch):
    from markdown_ingress.core.fetcher import DomainCircuitOpenError, Fetcher

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

    monkeypatch.setattr("markdown_ingress.core.fetcher.time.sleep", fake_sleep)

    with pytest.raises(DomainCircuitOpenError):
        fetcher.fetch_sync(url)

    assert client.calls == 0


def test_circuit_breaker_opens_with_spaced_failures(monkeypatch):
    """Circuit breaker must open even when failures are spread over time."""
    from markdown_ingress.core.fetcher import DomainCircuitOpenError, Fetcher

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
