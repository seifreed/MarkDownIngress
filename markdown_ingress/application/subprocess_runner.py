"""Subprocess execution utilities for batch ingestion."""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import os
import queue as queue_module
import sys
from typing import TYPE_CHECKING, Any, cast

from markdown_ingress.application.exceptions import _copy_batch_exception, _make_picklable

if TYPE_CHECKING:
    from markdown_ingress.config_models import IngestConfig
    from markdown_ingress.models import SafeDocument

_logger = logging.getLogger(__name__)

_SUBPROCESS_JOIN_TIMEOUT_S: float = 0.5
_BATCH_POLL_INTERVAL_S: float = 0.1


def _execute_batch_ingest_in_subprocess(
    url: str,
    config: IngestConfig,
    playwright_available: bool,
    queue,
) -> None:
    try:
        from markdown_ingress.application.use_cases import IngestUseCase

        document = IngestUseCase(playwright_available=playwright_available).execute(url, config)
        document.metadata = cast(dict[str, Any], _make_picklable(document.metadata))
        queue.put(("result", document))
    except Exception as exc:  # pragma: no cover - child process path
        try:
            queue.put(("exception", _copy_batch_exception(exc)))
        except Exception:
            queue.put(("exception_payload", {"type": type(exc).__name__, "message": str(exc)}))


def _main_module_file() -> str | None:
    """Return the current main-module path when subprocess respawn is safe."""
    main_module = sys.modules.get("__main__")
    path = getattr(main_module, "__file__", None)
    if not isinstance(path, str) or not path or path.startswith("<"):
        return None
    if not os.path.isfile(path):
        return None
    return path


def _batch_process_context():
    """Return a multiprocessing context suitable for spawning batch workers, or None."""
    if _main_module_file() is None:
        return None
    available = multiprocessing.get_all_start_methods()
    for method in ("forkserver", "spawn", "fork"):
        if method in available:
            return multiprocessing.get_context(method)
    return multiprocessing.get_context()


def _select_execution_strategy(ingest_use_case) -> tuple:
    """Choose between subprocess isolation and in-process execution.

    Returns a (strategy, fallback_reason) tuple where strategy is one of
    "isolated" or "local".
    """
    if not ingest_use_case.uses_default_runtime_dependencies():
        return "local", "custom runtime dependencies"
    if _batch_process_context() is None:
        return "local", "main module is not importable from a file"
    return "isolated", None


def _terminate_batch_process(process) -> None:
    """Terminate a batch subprocess safely."""
    if not process.is_alive():
        process.join(timeout=_SUBPROCESS_JOIN_TIMEOUT_S)
        return
    process.terminate()
    process.join(timeout=1.0)
    if process.is_alive():
        process.kill()
        process.join(timeout=1.0)


async def _poll_subprocess_queue(process, queue, url: str) -> SafeDocument:
    """Poll the subprocess result queue until the worker finishes or an error occurs."""
    from markdown_ingress.models import SafeDocument

    def read_payload(timeout: float | None = None) -> tuple[str, Any] | None:
        try:
            if timeout is not None:
                return cast(tuple[str, Any], queue.get(timeout=timeout))
            return cast(tuple[str, Any], queue.get_nowait())
        except queue_module.Empty:
            return None

    def handle_payload(payload: tuple[str, Any]) -> SafeDocument:
        kind, value = payload
        process.join(timeout=_SUBPROCESS_JOIN_TIMEOUT_S)
        if kind == "result":
            return cast(SafeDocument, value)
        if kind == "exception":
            if isinstance(value, BaseException):
                raise value
            raise RuntimeError(str(value))
        if kind == "exception_payload":
            raise RuntimeError(f"{value['type']}: {value['message']}")
        raise RuntimeError(f"Batch worker returned an unknown payload for {url}")

    while True:
        payload = read_payload()
        if payload is not None:
            return handle_payload(payload)
        if not process.is_alive():
            process.join(timeout=_SUBPROCESS_JOIN_TIMEOUT_S)
            payload = read_payload(timeout=_SUBPROCESS_JOIN_TIMEOUT_S)
            if payload is not None:
                return handle_payload(payload)
            raise RuntimeError(f"Batch worker exited without returning a result for {url}")
        await asyncio.sleep(_BATCH_POLL_INTERVAL_S)
