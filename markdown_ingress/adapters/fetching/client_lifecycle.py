"""HTTPX client creation and shutdown lifecycle for the fetcher."""

from __future__ import annotations

import asyncio
import logging
import sys
import warnings
from typing import Any, cast

import httpx

logger = logging.getLogger(__name__)


class ClientLifecycleMixin:
    """Lazy HTTPX client creation, context-manager support, and cleanup."""

    _async_client: httpx.AsyncClient | None
    _sync_client: httpx.Client | None
    _async_client_lock: asyncio.Lock | None

    def _track_async_close_task(self: Any, task: asyncio.Task[None]) -> None:
        self._async_close_tasks.add(task)

        def _finalize_close_task(done_task: asyncio.Task[None]) -> None:
            self._async_close_tasks.discard(done_task)
            try:
                done_task.result()
            except Exception as exc:  # noqa: BLE001 - background cleanup is best effort
                logger.debug(
                    "Background async HTTP client close failed: %s",
                    exc,
                    exc_info=True,
                )

        task.add_done_callback(_finalize_close_task)

    def _get_sync_client(self: Any) -> httpx.Client:
        if self._closing:
            raise RuntimeError("Fetcher is closing")
        if self._sync_client is None:
            with self._client_lock:
                if self._closing:
                    raise RuntimeError("Fetcher is closing")
                if self._sync_client is None:
                    self._sync_client = httpx.Client(
                        timeout=self.timeout,
                        follow_redirects=False,
                        max_redirects=self.max_redirects,
                        verify=self._build_ssl_context(),
                        trust_env=False,
                    )
        return cast(httpx.Client, self._sync_client)

    def _get_async_client_lock(self: Any) -> asyncio.Lock:
        with self._async_client_lock_guard:
            if self._async_client_lock is None:
                self._async_client_lock = asyncio.Lock()
            return cast(asyncio.Lock, self._async_client_lock)

    async def _get_async_client(self: Any) -> httpx.AsyncClient:
        if self._closing:
            raise RuntimeError("Fetcher is closing")
        if self._async_client is None:
            async with self._get_async_client_lock():
                if self._closing:
                    raise RuntimeError("Fetcher is closing")
                if self._async_client is None:
                    self._async_client = httpx.AsyncClient(
                        timeout=self.timeout,
                        follow_redirects=False,
                        max_redirects=self.max_redirects,
                        verify=self._build_ssl_context(),
                        trust_env=False,
                    )
        return cast(httpx.AsyncClient, self._async_client)

    def _close_sync_client(self: Any) -> None:
        self._closing = True
        with self._client_lock:
            if self._sync_client is not None:
                self._sync_client.close()
                self._sync_client = None

    def close(self: Any) -> None:
        self._close_sync_client()
        with self._async_client_lock_guard:
            client_to_close = self._async_client
            self._async_client = None
        if client_to_close is None:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            try:
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(client_to_close.aclose())
                finally:
                    loop.close()
            except Exception:  # noqa: BLE001 - sync close fallback must not escape
                logger.warning(
                    "Could not close async HTTP client synchronously; "
                    "use 'async with fetcher:' or 'await fetcher.aclose()' instead."
                )
        else:
            logger.warning(
                "Cannot close async HTTP client from within a running event loop; "
                "use 'async with fetcher:' or 'await fetcher.aclose()' instead. "
                "Scheduling close as background task."
            )
            try:
                loop = asyncio.get_running_loop()
                self._track_async_close_task(loop.create_task(client_to_close.aclose()))
            except Exception as exc:  # noqa: BLE001 - async close scheduling is best effort
                logger.debug(
                    "Failed to schedule async client close as background task: %s",
                    exc,
                    exc_info=True,
                )

    async def aclose(self: Any) -> None:
        self._close_sync_client()
        async with self._get_async_client_lock():
            if self._async_client is not None:
                await self._async_client.aclose()
                self._async_client = None

    def __enter__(self: Any) -> Any:
        return self

    def __exit__(self: Any, *args: Any) -> None:
        self.close()

    async def __aenter__(self: Any) -> Any:
        return self

    async def __aexit__(self: Any, *args: Any) -> None:
        await self.aclose()

    def __del__(self: Any) -> None:
        sync_client_exists = False
        async_client_exists = False
        try:
            with self._client_lock:
                sync_client_exists = self._sync_client is not None
        except Exception as exc:  # noqa: BLE001 - finalizers must tolerate partial init
            logger.debug(
                "Could not inspect sync client during finalization: %s", exc, exc_info=True
            )
        try:
            with self._async_client_lock_guard:
                async_client_exists = getattr(self, "_async_client", None) is not None
        except Exception as exc:  # noqa: BLE001 - finalizers must tolerate partial init
            logger.debug(
                "Could not inspect async client during finalization: %s", exc, exc_info=True
            )

        # Emitting a warning during interpreter shutdown is unreliable (the
        # import machinery may be torn down, raising "import of warnings halted")
        # and pointless — the process is exiting. Skip it then, like the other
        # finalizer blocks below tolerate shutdown-time failures.
        if (sync_client_exists or async_client_exists) and not sys.is_finalizing():
            try:
                warnings.warn(
                    "Fetcher was not properly closed; HTTP client resources may leak. "
                    "Use 'async with fetcher:' or call 'await fetcher.aclose()' explicitly.",
                    ResourceWarning,
                    stacklevel=2,
                )
            except Exception as exc:  # noqa: BLE001 - finalizers must tolerate shutdown
                logger.debug("Could not emit ResourceWarning during finalization: %s", exc)
        try:
            with self._client_lock:
                if self._sync_client is not None:
                    self._sync_client.close()
                    self._sync_client = None
        except Exception as exc:  # noqa: BLE001 - finalizer cleanup is best effort
            logger.debug("Could not close sync client during finalization: %s", exc, exc_info=True)
        try:
            with self._async_client_lock_guard:
                self._async_client = None
                self._async_client_lock = None
        except Exception as exc:  # noqa: BLE001 - finalizer cleanup is best effort
            logger.debug("Could not clear async client during finalization: %s", exc, exc_info=True)
