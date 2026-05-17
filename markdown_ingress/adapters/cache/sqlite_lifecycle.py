"""SQLite cache connection lifecycle helpers."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any


def close_connection_after_init_failure(conn: Any, logger: logging.Logger) -> None:
    """Best-effort close for partially initialized cache connections."""
    try:
        conn.close()
    except sqlite3.Error as exc:
        logger.debug("Cache connection close during init cleanup failed: %s", exc)


def close_connection_for_cache(conn: Any, logger: logging.Logger) -> None:
    """Close an active SQLite connection for explicit cache shutdown."""
    try:
        conn.close()
    except sqlite3.Error as exc:
        logger.warning("Error closing SQLite connection: %s", exc)


def close_connection_from_del(cache: Any, logger: logging.Logger) -> None:
    """Fallback cleanup for SQLiteCache.__del__ without blocking GC."""
    if not all(hasattr(cache, attr) for attr in ("_closed", "conn", "_db_lock")):
        return

    conn = getattr(cache, "conn", None)
    db_lock = getattr(cache, "_db_lock", None)
    if conn is None or db_lock is None:
        return

    if db_lock.acquire(blocking=False):
        try:
            if cache._closed:
                return
            cache._closed = True
            try:
                conn.close()
            except sqlite3.Error as exc:
                logger.debug("SQLite connection close during __del__ failed: %s", exc)
        finally:
            db_lock.release()
    else:
        logger.debug("SQLiteCache.__del__: could not acquire lock, skipping cleanup")
