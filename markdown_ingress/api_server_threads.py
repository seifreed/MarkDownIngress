"""Thread coordination helpers for API server lifecycle code."""

from __future__ import annotations

import threading
from collections.abc import Mapping


def stop_control_thread(
    name: str,
    thread: threading.Thread | None,
    stop_event: threading.Event | None,
) -> None:
    """Signal and join a control thread, raising if it does not stop promptly."""
    if stop_event is not None:
        stop_event.set()
    if thread is not None and thread.is_alive():
        thread.join(timeout=1.0)
        if thread.is_alive():
            raise RuntimeError(f"{name} did not stop before reload")


def _thread_reference(value: object | None) -> threading.Thread | None:
    return value if isinstance(value, threading.Thread) else None


def _event_reference(value: object | None) -> threading.Event | None:
    return value if isinstance(value, threading.Event) else None


def stop_reloaded_control_thread_pair(
    *,
    module_globals: Mapping[str, object],
    name: str,
    previous_thread_key: str,
    current_thread_key: str,
    previous_stop_key: str,
    current_stop_key: str,
) -> None:
    previous_thread = module_globals.get(previous_thread_key) or module_globals.get(
        current_thread_key
    )
    previous_stop = module_globals.get(previous_stop_key) or module_globals.get(current_stop_key)
    stop_control_thread(
        f"Previous {name}",
        _thread_reference(previous_thread),
        _event_reference(previous_stop),
    )
    stop_control_thread(
        name[:1].upper() + name[1:],
        _thread_reference(module_globals.get(current_thread_key)),
        _event_reference(module_globals.get(current_stop_key)),
    )
