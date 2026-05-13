"""Thread coordination helpers for API server lifecycle code."""

from __future__ import annotations

import threading


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
