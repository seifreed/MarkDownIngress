"""Regression tests for in-flight cleanup-thread shutdown handling.

On Python 3.13+, ``threading.Thread.join`` raises ``PythonFinalizationError``
(a ``RuntimeError`` subclass) when called during interpreter shutdown. A
non-default ``InFlightRegistry`` is stopped from ``__del__``, which can run at
finalization — so ``stop_periodic_cleanup`` must not propagate that error.
"""

import pytest

from markdown_ingress.core import inflight
from markdown_ingress.core.inflight import InFlightRegistry


def _shutdown_join(*_args, **_kwargs):
    raise RuntimeError("cannot join thread at interpreter shutdown")


def test_stop_periodic_cleanup_swallows_join_error_during_finalization(monkeypatch):
    registry = InFlightRegistry()
    registry.start_periodic_cleanup(interval_seconds=999)
    thread = registry._cleanup_thread
    assert thread is not None

    monkeypatch.setattr(thread, "join", _shutdown_join)
    monkeypatch.setattr(inflight.sys, "is_finalizing", lambda: True)

    # Must return cleanly — a raising __del__/atexit path would crash shutdown.
    registry.stop_periodic_cleanup()

    # Let the real daemon thread exit after the test restores join.
    monkeypatch.undo()
    registry._cleanup_stop.set()


def test_stop_periodic_cleanup_reraises_join_error_when_not_finalizing(monkeypatch):
    """A join failure outside interpreter shutdown is a real bug — don't mask it."""
    registry = InFlightRegistry()
    registry.start_periodic_cleanup(interval_seconds=999)
    thread = registry._cleanup_thread
    assert thread is not None

    monkeypatch.setattr(thread, "join", _shutdown_join)
    monkeypatch.setattr(inflight.sys, "is_finalizing", lambda: False)

    with pytest.raises(RuntimeError):
        registry.stop_periodic_cleanup()

    monkeypatch.undo()
    registry._cleanup_stop.set()
