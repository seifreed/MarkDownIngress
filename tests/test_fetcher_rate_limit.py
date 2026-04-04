"""Focused tests for host backoff and retry behavior in the HTTP fetcher."""

from __future__ import annotations

import asyncio
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

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

    assert Fetcher._validate_url("http://127.0.0.1:8000/", allow_local_urls=True) == "http://127.0.0.1:8000/"


@pytest.mark.parametrize("url", ["https://:443/path", "https://user:pass@/path"])
def test_fetcher_rejects_urls_without_hostname(url: str):
    from markdown_ingress.core.fetcher import Fetcher

    with pytest.raises(ValueError, match="valid host"):
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
            return await fetcher.fetch(redirect_url)

        result = asyncio.run(run())
    finally:
        Fetcher._record_success = original  # type: ignore[method-assign]
        redirect_server.shutdown()
        redirect_server.server_close()
        final_server.shutdown()
        final_server.server_close()

    assert result.status_code == 200
    assert success_hosts[-1] == final_host


def test_sync_ssl_bypass_does_not_sleep_when_no_attempts_remain(monkeypatch):
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

    class FakeSSLFailure(Exception):
        pass

    class MainClient:
        def __init__(self):
            self.calls = 0

        def get(self, url: str, headers=None):
            self.calls += 1
            if self.calls == 1:
                return first_retryable
            raise FakeSSLFailure("SSL handshake failed")

    class BypassClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url: str, headers=None):
            return bypass_retryable

    sleep_calls: list[float] = []
    monkeypatch.setattr("markdown_ingress.core.fetcher.time.sleep", lambda seconds: sleep_calls.append(seconds))
    monkeypatch.setattr("markdown_ingress.core.fetcher.httpx.Client", lambda *args, **kwargs: BypassClient())

    fetcher = Fetcher(timeout=2.0, domain_request_interval=0.0, allow_ssl_bypass=True)
    monkeypatch.setattr(fetcher, "_get_sync_client", lambda: MainClient())
    monkeypatch.setattr(fetcher, "_reserve_domain_slot", lambda host: 0.0)
    monkeypatch.setattr(fetcher, "_defer_host", lambda host, delay_seconds: None)

    with pytest.raises(Exception):
        fetcher.fetch_sync("https://unit.test/ssl")

    assert len(sleep_calls) == 1


def test_fetcher_sync_rechecks_circuit_breaker_after_rate_limit_sleep(monkeypatch):
    from markdown_ingress.core.fetcher import DomainCircuitOpenError, Fetcher

    url = "https://example.com/recheck"
    host = Fetcher._host_key(url)

    class SyncClient:
        def __init__(self):
            self.calls = 0

        def get(self, url: str, headers=None):
            self.calls += 1
            body = b"<html><body><article><p>ok</p></article></body></html>"
            return httpx.Response(
                200,
                content=body,
                headers={"content-type": "text/html", "content-length": str(len(body))},
                request=httpx.Request("GET", url),
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
