"""Tests for the HTTP webhook notifier adapter."""

from __future__ import annotations

import json

import httpx
import pytest

from markdown_ingress.adapters.webhooks import http_notifier as http_notifier_module
from markdown_ingress.adapters.webhooks.http_notifier import HTTPWebhookNotifier


def test_format_host_header_preserves_default_ports_and_ipv6():
    assert http_notifier_module._format_host_header("example.com", 443, "https") == "example.com"
    assert (
        http_notifier_module._format_host_header("example.com", 8443, "https") == "example.com:8443"
    )
    assert http_notifier_module._format_host_header("2001:db8::1", 443, "https") == "[2001:db8::1]"
    assert (
        http_notifier_module._format_host_header("2001:db8::1", 8443, "https")
        == "[2001:db8::1]:8443"
    )


def test_https_dns_pinning_uses_original_hostname_for_sni(monkeypatch):
    captured: dict[str, object] = {}

    class FakeSocket:
        def close(self):
            captured["socket_closed"] = True

    class FakeContext:
        def wrap_socket(self, sock, server_hostname=None):
            captured["server_hostname"] = server_hostname
            return sock

    class FakeResponse:
        status = 204

    def fake_create_connection(address, timeout=None, source_address=None):
        captured["address"] = address
        captured["timeout"] = timeout
        captured["source_address"] = source_address
        return FakeSocket()

    def fake_send(self, data):
        if self.sock is None:
            self.connect()
        captured["sent"] = True

    def fake_getresponse(self):
        return FakeResponse()

    monkeypatch.setattr(http_notifier_module.socket, "create_connection", fake_create_connection)
    monkeypatch.setattr(http_notifier_module.ssl, "create_default_context", lambda: FakeContext())
    monkeypatch.setattr(http_notifier_module.HTTPSConnection, "send", fake_send)
    monkeypatch.setattr(http_notifier_module.HTTPSConnection, "getresponse", fake_getresponse)

    notifier = HTTPWebhookNotifier(max_retries=1, retry_delay_seconds=0.0, timeout_seconds=3.0)
    notifier.notify("https://EXAMPLE.COM./hook?x=1", {"ok": True}, validated_ip="203.0.113.10")

    assert captured["address"] == ("203.0.113.10", 443)
    assert captured["server_hostname"] == "example.com"
    assert captured["sent"] is True
    assert captured["socket_closed"] is True


def test_https_dns_pinning_rejects_invalid_port_zero():
    notifier = HTTPWebhookNotifier(max_retries=1, retry_delay_seconds=0.0, timeout_seconds=3.0)

    try:
        notifier.notify("https://example.com:0/hook", {"ok": True}, validated_ip="203.0.113.10")
    except ValueError as exc:
        assert "port" in str(exc).lower()
    else:
        raise AssertionError("expected ValueError for invalid webhook port 0")


def test_notify_uses_validated_dns_pinned_url_when_validation_rewrites_host(monkeypatch):
    captured: dict[str, object] = {}

    def fake_validate(url, *, allow_local=False, resolve_dns=True):
        captured["validated_url_input"] = url
        captured["allow_local"] = allow_local
        captured["resolve_dns"] = resolve_dns
        return "https://203.0.113.10/hook?x=1"

    def fake_notify_with_dns_pinning(self, webhook_url, data, validated_ip):
        captured["webhook_url"] = webhook_url
        captured["data"] = data
        captured["validated_ip"] = validated_ip

    monkeypatch.setattr(http_notifier_module, "validate_http_url_no_ssrf", fake_validate)
    monkeypatch.setattr(
        HTTPWebhookNotifier,
        "_notify_with_dns_pinning",
        fake_notify_with_dns_pinning,
    )

    notifier = HTTPWebhookNotifier(max_retries=0, retry_delay_seconds=0.0, timeout_seconds=3.0)
    notifier.notify("https://example.com/hook?x=1", {"ok": True})

    assert captured["validated_url_input"] == "https://example.com/hook?x=1"
    assert captured["allow_local"] is False
    assert captured["resolve_dns"] is True
    assert captured["webhook_url"] == "https://example.com/hook?x=1"
    assert captured["validated_ip"] == "203.0.113.10"
    assert json.loads(captured["data"].decode("utf-8")) == {"ok": True}


def test_notify_uses_max_retries_as_additional_retries(monkeypatch):
    calls = {"count": 0}

    def fake_validate(url, *, allow_local=False, resolve_dns=True):
        return url

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def post(self, url, content=None, headers=None):
            calls["count"] += 1
            raise httpx.ConnectError("temporary failure")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(http_notifier_module, "validate_http_url_no_ssrf", fake_validate)
    monkeypatch.setattr(http_notifier_module.httpx, "Client", FakeClient)

    notifier = HTTPWebhookNotifier(max_retries=2, retry_delay_seconds=0.0, timeout_seconds=3.0)

    with pytest.raises(RuntimeError, match="after 3 attempts"):
        notifier.notify("https://example.com/hook", {"ok": True})

    assert calls["count"] == 3


def test_notify_allows_zero_retries_for_single_attempt(monkeypatch):
    calls = {"count": 0}

    def fake_validate(url, *, allow_local=False, resolve_dns=True):
        return url

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def post(self, url, content=None, headers=None):
            calls["count"] += 1
            raise httpx.ConnectError("temporary failure")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(http_notifier_module, "validate_http_url_no_ssrf", fake_validate)
    monkeypatch.setattr(http_notifier_module.httpx, "Client", FakeClient)

    notifier = HTTPWebhookNotifier(max_retries=0, retry_delay_seconds=0.0, timeout_seconds=3.0)

    with pytest.raises(RuntimeError, match="after 1 attempts"):
        notifier.notify("https://example.com/hook", {"ok": True})

    assert calls["count"] == 1
