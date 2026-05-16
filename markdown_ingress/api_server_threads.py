"""Thread coordination helpers for API server lifecycle code."""

from __future__ import annotations

import threading
from collections.abc import Mapping


def previous_or_current_reference(
    module_globals: Mapping[str, object],
    previous_key: str,
    current_key: str,
) -> object | None:
    """Resolve a previous reload reference, falling back to the active reference."""
    previous_value = module_globals.get(previous_key)
    return previous_value if previous_value is not None else module_globals.get(current_key)


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
