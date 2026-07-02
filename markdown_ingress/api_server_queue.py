"""
Job-queue helper classes and pure utilities for api_server.py.

Contains classes and stateless helper functions that have no dependency on
api_server's mutable module-level globals, extracted to keep api_server.py
focused on routing, middleware, and stateful queue management.

Symbols that depend on mutable globals (JOB_QUEUE, _JOB_QUEUE_HISTORY, etc.)
remain in api_server.py so that monkeypatch.setattr(api_server, ...) works
correctly in tests.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime

from markdown_ingress.api_server_env import _parse_iso_datetime_utc
from markdown_ingress.api_server_external_owner_queue import (
    _ExternalOwnerJobQueue as _ExternalOwnerJobQueue,
)
from markdown_ingress.api_server_queue_ttl import (
    _job_record_within_api_ttl as _job_record_within_api_ttl,
)
from markdown_ingress.api_server_queue_ttl import (
    _job_row_is_visible,
)
from markdown_ingress.api_server_queue_ttl import (
    _legacy_unknown_ttl_expires_at as _legacy_unknown_ttl_expires_at,
)

# ---------------------------------------------------------------------------
# Timeout / threshold constants
# ---------------------------------------------------------------------------

_QUEUE_LEASE_TIMEOUT_SECONDS = 30.0
_LEGACY_QUEUE_PRUNE_ERROR_THRESHOLD = 3
# ---------------------------------------------------------------------------
# Exception types
# ---------------------------------------------------------------------------


class _TransientLegacyQueueReadError(RuntimeError):
    """Signal that a legacy queue could not be inspected due to a transient read problem."""


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------


def _is_active_owner_error(exc: RuntimeError) -> bool:
    return str(exc) == "Job queue DB is already owned by another active instance"


def _is_stale_heartbeat(heartbeat_at: str) -> bool:
    heartbeat_dt = _parse_iso_datetime_utc(heartbeat_at)
    if heartbeat_dt is None:
        return True
    age_seconds = (datetime.now(UTC) - heartbeat_dt).total_seconds()
    return age_seconds > _QUEUE_LEASE_TIMEOUT_SECONDS


def _close_queue_for_repair(queue: object) -> None:
    close = getattr(queue, "close", None)
    if not callable(close):
        if getattr(queue, "state", None) == "external_owner":
            return
        raise RuntimeError("Job queue cannot be closed for repair")
    try:
        close(inline_wait_timeout=0.0, preserve_state_on_inline_timeout=True)
    except TypeError:
        close()


def _read_job_from_queue(queue, job_id: str):
    try:
        return queue.get(job_id, cleanup_expired=False)
    except TypeError:
        try:
            return queue.get(job_id)
        except sqlite3.Error as exc:
            raise RuntimeError(f"Job queue backend read failed: {exc}") from exc
    except sqlite3.Error as exc:
        raise RuntimeError(f"Job queue backend read failed: {exc}") from exc


def _queue_still_has_visible_jobs(queue) -> bool:
    connect = getattr(queue, "_connect", None)
    if not callable(connect):
        return True
    try:
        with closing(connect()) as conn:
            rows = conn.execute("""
                SELECT status, completed_at, ttl_seconds, legacy_expires_at
                FROM jobs
                """).fetchall()
    except sqlite3.Error as exc:
        raise _TransientLegacyQueueReadError(str(exc)) from exc
    except (AttributeError, TypeError, KeyError) as exc:
        raise _TransientLegacyQueueReadError(f"legacy queue inspection failed: {exc}") from exc
    now = datetime.now(UTC)
    return any(_job_row_is_visible(row, now) for row in rows)
