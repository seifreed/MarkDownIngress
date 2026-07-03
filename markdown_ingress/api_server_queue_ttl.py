"""TTL and visibility helpers for API job queues."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import cast

from markdown_ingress.adapters.jobs.sqlite_job_queue import LEGACY_UNKNOWN_TTL_SECONDS
from markdown_ingress.api_server_env import _parse_iso_datetime_utc
from markdown_ingress.api_server_job_queue_states import ACTIVE_JOB_STATUSES as _ACTIVE_JOB_STATUSES


def _legacy_unknown_ttl_expires_at(
    completed_at: str | None, legacy_expires_at: str | None
) -> datetime | None:
    """Return the effective expiry for a legacy completed job."""
    if legacy_expires_at:
        expires_dt = _parse_iso_datetime_utc(legacy_expires_at)
        if expires_dt is not None:
            return expires_dt
        return None
    if completed_at is None:
        return None
    completed_dt = _parse_iso_datetime_utc(completed_at)
    if completed_dt is None:
        return None
    return completed_dt + timedelta(seconds=LEGACY_UNKNOWN_TTL_SECONDS)


def _coerce_positive_ttl_seconds(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        ttl_value = value
    elif isinstance(value, str) and value.strip().isdigit():
        ttl_value = int(value)
    else:
        return None
    if ttl_value <= 0:
        return None
    return ttl_value


def _parse_optional_datetime_utc(value: str | None) -> datetime | None:
    if value is None:
        return None
    return _parse_iso_datetime_utc(value)


def _optional_text(value: object) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _completed_row_with_ttl_is_expired(
    completed_at: str | None,
    ttl_seconds: object,
    now: datetime,
) -> bool:
    ttl_value = _coerce_positive_ttl_seconds(ttl_seconds)
    if ttl_value is None:
        return True
    completed_dt = _parse_optional_datetime_utc(completed_at)
    if completed_dt is None:
        return True
    return completed_dt + timedelta(seconds=ttl_value) <= now


def _completed_row_without_ttl_is_expired(
    completed_at: str | None,
    legacy_expires_at: str | None,
    now: datetime,
) -> bool:
    legacy_expires_dt = _parse_optional_datetime_utc(legacy_expires_at)
    if legacy_expires_dt is not None:
        return legacy_expires_dt <= now
    expires_dt = _legacy_unknown_ttl_expires_at(completed_at, None)
    if expires_dt is None:
        return True
    return expires_dt <= now


def _job_visibility_fields(row: object) -> tuple[object, object, object, object]:
    if isinstance(row, sqlite3.Row):
        return row["status"], row["completed_at"], row["ttl_seconds"], row["legacy_expires_at"]
    row_values = cast(tuple[object, object, object, object], row)
    return row_values[0], row_values[1], row_values[2], row_values[3]


def _completed_job_is_visible(
    completed_at: object,
    ttl_seconds: object,
    legacy_expires_at: object,
    now: datetime,
) -> bool:
    if ttl_seconds is None:
        expires_dt = _legacy_unknown_ttl_expires_at(
            _optional_text(completed_at),
            _optional_text(legacy_expires_at),
        )
        return expires_dt is not None and now <= expires_dt

    if not completed_at:
        return False
    completed_dt = _parse_optional_datetime_utc(_optional_text(completed_at))
    if completed_dt is None:
        return False
    ttl_value = _coerce_positive_ttl_seconds(ttl_seconds)
    if ttl_value is None:
        return False
    return (now - completed_dt).total_seconds() <= ttl_value


def _job_row_is_visible(row: object, now: datetime) -> bool:
    status, completed_at, ttl_seconds, legacy_expires_at = _job_visibility_fields(row)
    if status in _ACTIVE_JOB_STATUSES:
        return True
    return _completed_job_is_visible(completed_at, ttl_seconds, legacy_expires_at, now)


def _job_record_within_api_ttl(job) -> bool:
    status = getattr(job, "status", None)
    completed_at = getattr(job, "completed_at", None)
    if status in _ACTIVE_JOB_STATUSES:
        return True
    ttl_seconds = cast(object | None, getattr(job, "ttl_seconds", None))
    if ttl_seconds is None:
        return _legacy_job_record_within_api_ttl(job, completed_at)
    return _completed_job_record_within_api_ttl(completed_at, ttl_seconds)


def _legacy_job_record_within_api_ttl(job, completed_at) -> bool:
    expires_dt = _legacy_unknown_ttl_expires_at(
        completed_at,
        getattr(job, "legacy_expires_at", None),
    )
    return expires_dt is not None and datetime.now(UTC) <= expires_dt


def _completed_job_record_within_api_ttl(completed_at, ttl_seconds: object) -> bool:
    if completed_at is None:
        return False
    completed_dt = _parse_iso_datetime_utc(completed_at)
    ttl_value = _coerce_positive_ttl_seconds(ttl_seconds)
    if completed_dt is None or ttl_value is None:
        return False
    age_seconds = (datetime.now(UTC) - completed_dt).total_seconds()
    return age_seconds <= ttl_value
