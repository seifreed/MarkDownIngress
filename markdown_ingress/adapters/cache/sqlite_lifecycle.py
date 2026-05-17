"""SQLite cache connection lifecycle helpers."""

from __future__ import annotations

import logging
from typing import Any


def close_connection_after_init_failure(conn: Any, logger: logging.Logger) -> None:
    """Best-effort close for partially initialized cache connections."""
    try:
        conn.close()
    except Exception as exc:
        logger.debug("Cache connection close during init cleanup failed: %s", exc)


def close_connection_for_cache(conn: Any, logger: logging.Logger) -> None:
    """Close an active SQLite connection for explicit cache shutdown."""
    try:
        conn.close()
    except Exception as exc:
        logger.warning("Error closing SQLite connection: %s", exc)


def close_connection_from_del(cache: Any, logger: logging.Logger) -> None:
    """Fallback cleanup for SQLiteCache.__del__ without blocking GC."""
    if not hasattr(cache, "_closed"):
        return
    if not hasattr(cache, "conn"):
        return
    if not hasattr(cache, "_db_lock"):
        return

    conn = getattr(cache, "conn", None)
    if conn is None:
        return

    db_lock = getattr(cache, "_db_lock", None)
    if db_lock is None:
        return

    if not db_lock.acquire(blocking=False):
        logger.debug("SQLiteCache.__del__: could not acquire lock, skipping cleanup")
        return
    try:
        if cache._closed:
            return
        cache._closed = True
        try:
            conn.close()
        except Exception as exc:
            logger.debug("SQLite connection close during __del__ failed: %s", exc)
    finally:
        db_lock.release()
