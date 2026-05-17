"""SQLite cache entry helpers."""

from __future__ import annotations

import math
import time
from typing import Any


def cache_key_label(key: str) -> str:
    """Return a short cache key label for logs."""
    return key[:16] if len(key) > 16 else key


def coerce_expires_at(value: object) -> float | None:
    """Return a finite expiration timestamp or None for corrupt values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    expires_at = float(value)
    if not math.isfinite(expires_at):
        return None
    return expires_at


def is_cache_entry_expired(expires_at: float, *, now: float | None = None) -> bool:
    """Return True when a positive expiration timestamp is in the past."""
    current_time = time.time() if now is None else now
    return expires_at > 0 and current_time >= expires_at


def delete_cache_key(conn: Any, key: str) -> None:
    """Delete one cache entry by key without committing."""
    conn.execute("DELETE FROM cache WHERE key = ?", (key,))


def delete_expired_entries(conn: Any, *, now: float | None = None) -> int:
    """Delete expired cache entries without committing and return rowcount."""
    current_time = time.time() if now is None else now
    cursor = conn.execute(
        "DELETE FROM cache WHERE expires_at > 0 AND expires_at <= ?",
        (current_time,),
    )
    return max(0, int(cursor.rowcount))
