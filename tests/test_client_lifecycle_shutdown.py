"""Regression tests for fetcher client finalization at interpreter shutdown.

``ClientLifecycleMixin.__del__`` emits a ``ResourceWarning`` when a fetcher is
garbage-collected without being closed. During interpreter shutdown the warning
machinery may be torn down, so ``warnings.warn`` (or its former late
``import warnings``) raised "import of warnings halted", printing an
"Exception ignored in __del__" traceback after a one-shot CLI/MCP ingest. The
finalizer must skip the warning while finalizing and still emit it otherwise.
"""

import warnings

import httpx

from markdown_ingress.adapters.fetching import client_lifecycle
from markdown_ingress.adapters.fetching.client_lifecycle import ClientLifecycleMixin


class _Fetcher(ClientLifecycleMixin):
    def __init__(self) -> None:
        import threading

        self._client_lock = threading.Lock()
        self._async_client_lock_guard = threading.Lock()
        self._sync_client = httpx.Client()
        self._async_client = None
        self._async_client_lock = None


def test_del_skips_warning_during_finalization(monkeypatch):
    monkeypatch.setattr(client_lifecycle.sys, "is_finalizing", lambda: True)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("import of warnings halted; None in sys.modules")

    monkeypatch.setattr(client_lifecycle.warnings, "warn", _boom)

    fetcher = _Fetcher()
    sync_client = fetcher._sync_client

    # Must not raise even though warnings.warn would; cleanup still runs.
    fetcher.__del__()

    assert sync_client.is_closed is True
    assert fetcher._sync_client is None


def test_del_emits_resource_warning_when_not_finalizing(monkeypatch):
    monkeypatch.setattr(client_lifecycle.sys, "is_finalizing", lambda: False)

    fetcher = _Fetcher()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        fetcher.__del__()

    assert any(issubclass(w.category, ResourceWarning) for w in caught)
    assert fetcher._sync_client is None
