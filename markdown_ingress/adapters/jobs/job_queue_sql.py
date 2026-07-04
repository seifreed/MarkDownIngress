"""SQL statement constants for SQLite job queue operations."""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# Lease-table statements
# ---------------------------------------------------------------------------

SQL_BEGIN_IMMEDIATE: Final[str] = "BEGIN IMMEDIATE"

SQL_LEASE_SELECT_OWNER: Final[str] = "SELECT owner_id FROM queue_leases WHERE lease_name = ?"
SQL_LEASE_SELECT_OWNER_AND_HEARTBEAT: Final[str] = (
    "SELECT owner_id, heartbeat_at FROM queue_leases WHERE lease_name = ?"
)
SQL_LEASE_INSERT: Final[str] = (
    "INSERT INTO queue_leases (lease_name, owner_id, heartbeat_at) " "VALUES (?, ?, ?)"
)
SQL_LEASE_UPSERT_OWNER: Final[str] = (
    "UPDATE queue_leases " "SET owner_id = ?, heartbeat_at = ? WHERE lease_name = ?"
)
SQL_LEASE_UPDATE_HEARTBEAT: Final[str] = (
    "UPDATE queue_leases " "SET heartbeat_at = ? WHERE lease_name = ? AND owner_id = ?"
)
SQL_LEASE_DELETE_BY_NAME_AND_OWNER: Final[str] = (
    "DELETE FROM queue_leases WHERE lease_name = ? AND owner_id = ?"
)

# ---------------------------------------------------------------------------
# Job-table statements
# ---------------------------------------------------------------------------

SQL_JOBS_SELECT_BY_ID: Final[str] = "SELECT * FROM jobs WHERE job_id = ?"
SQL_JOBS_SELECT_LEGACY_TTL_ROWS: Final[str] = """
SELECT job_id, completed_at
FROM jobs
WHERE completed_at IS NOT NULL
  AND ttl_seconds IS NULL
  AND legacy_expires_at IS NULL
"""
SQL_JOBS_SELECT_STATUS_TTL_FIELDS: Final[str] = (
    "SELECT status, completed_at, ttl_seconds, legacy_expires_at FROM jobs"
)
SQL_JOBS_SELECT_STATUS: Final[str] = "SELECT status FROM jobs WHERE job_id = ?"
SQL_JOBS_SELECT_ACTIVE_COUNT: Final[str] = (
    "SELECT COUNT(*) AS count FROM jobs WHERE status IN (?, ?)"
)
SQL_JOBS_INSERT: Final[str] = (
    "INSERT INTO jobs (job_id, status, created_at, webhook_url, ttl_seconds) "
    "VALUES (?, ?, ?, ?, ?)"
)
SQL_JOBS_DELETE_BY_ID_AND_STATUS: Final[str] = "DELETE FROM jobs WHERE job_id = ? AND status = ?"
SQL_JOBS_SELECT_COMPLETED_WITHOUT_TTL: Final[str] = (
    "SELECT job_id, completed_at FROM jobs "
    "WHERE status IN (?, ?) AND completed_at IS NOT NULL "
    "AND ttl_seconds IS NULL AND legacy_expires_at IS NULL"
)
SQL_JOBS_UPDATE_RUNNING_TO_COMPLETED: Final[str] = (
    "UPDATE jobs "
    "SET status = ?, completed_at = ?, result_json = ?, error = NULL "
    "WHERE job_id = ? AND status = ?"
)
SQL_JOBS_UPDATE_FAIL_STANDARD: Final[str] = (
    "UPDATE jobs "
    "SET status = ?, completed_at = ?, result_json = NULL, error = ? "
    "WHERE job_id = ? AND status IN (?, ?)"
)
SQL_JOBS_UPDATE_WEBHOOK_FAILED: Final[str] = (
    "UPDATE jobs " "SET status = ?, error = ? " "WHERE job_id = ? AND status = ?"
)
SQL_JOBS_UPDATE_COMPLETE_PRESERVE_RESULT: Final[str] = (
    "UPDATE jobs "
    "SET status = ?, completed_at = ?, result_json = ?, error = ? "
    "WHERE job_id = ? AND status = ?"
)
SQL_JOBS_UPDATE_FORCE_FAIL_WHILE_RUNNING: Final[str] = (
    "UPDATE jobs " "SET status = ?, error = ?, " "completed_at = ? WHERE job_id = ? AND status = ?"
)

SQL_JOBS_UPDATE_RUNNING: Final[str] = (
    "UPDATE jobs SET status = ?, started_at = ? " "WHERE job_id = ? AND status = ?"
)
SQL_JOBS_UPDATE_ORPHANED: Final[str] = """
UPDATE jobs
SET status = ?,
    completed_at = ?,
    result_json = NULL,
    ttl_seconds = COALESCE(ttl_seconds, ?),
    error = CASE
        WHEN status = ?
            THEN 'Job interrupted by process restart; persisted task payload is not recoverable'
        ELSE 'Job abandoned after process restart; persisted task payload is not recoverable'
    END
WHERE status IN (?, ?)
"""
SQL_JOBS_UPDATE_LEGACY_TTL: Final[str] = "UPDATE jobs SET legacy_expires_at = ? WHERE job_id = ?"
SQL_JOBS_UPDATE_LEGACY_TTL_WITH_TTL: Final[str] = (
    "UPDATE jobs SET ttl_seconds = ?, legacy_expires_at = ? WHERE job_id = ?"
)

SQL_JOBS_DELETE_TTL_EXPIRED: Final[str] = """
DELETE FROM jobs
WHERE status NOT IN (?, ?)
  AND ttl_seconds IS NOT NULL
  AND (
      completed_at IS NULL
      OR julianday(completed_at) IS NULL
      OR julianday(?) > julianday(completed_at) + (ttl_seconds / 86400.0)
  )
"""
SQL_JOBS_DELETE_CORRUPT_TTL: Final[str] = """
DELETE FROM jobs
WHERE status NOT IN (?, ?)
  AND ttl_seconds IS NOT NULL
  AND (
      typeof(ttl_seconds) != 'integer'
      OR ttl_seconds <= 0
  )
"""
SQL_JOBS_DELETE_LEGACY_EXPIRED: Final[str] = """
DELETE FROM jobs
WHERE status NOT IN (?, ?)
  AND ttl_seconds IS NULL
  AND legacy_expires_at IS NOT NULL
  AND (
      julianday(legacy_expires_at) IS NULL
      OR julianday(?) > julianday(legacy_expires_at)
  )
"""
SQL_JOBS_DELETE_CORRUPT_LEGACY: Final[str] = """
DELETE FROM jobs
WHERE status NOT IN (?, ?)
  AND ttl_seconds IS NULL
  AND legacy_expires_at IS NULL
  AND (completed_at IS NULL OR julianday(completed_at) IS NULL)
"""
SQL_JOBS_DELETE_BY_ID: Final[str] = "DELETE FROM jobs WHERE job_id = ?"

# ---------------------------------------------------------------------------
# DDL / migration statements
# ---------------------------------------------------------------------------

SQL_JOBS_TABLE_SCHEMA: Final[str] = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    result_json TEXT,
    error TEXT,
    webhook_url TEXT,
    ttl_seconds INTEGER,
    legacy_expires_at TEXT
)
"""
SQL_JOBS_TTL_COLUMN_EXISTS: Final[str] = "ttl_seconds"
SQL_JOBS_LEGACY_EXPIRES_AT_COLUMN_EXISTS: Final[str] = "legacy_expires_at"
SQL_LEASE_TABLE_SCHEMA: Final[str] = """
CREATE TABLE IF NOT EXISTS queue_leases (
    lease_name TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL
)
"""
SQL_JOBS_ADD_TTL_SECONDS_COLUMN: Final[str] = "ALTER TABLE jobs ADD COLUMN ttl_seconds INTEGER"
SQL_JOBS_ADD_LEGACY_EXPIRES_AT_COLUMN: Final[str] = (
    "ALTER TABLE jobs ADD COLUMN legacy_expires_at TEXT"
)
SQL_JOBS_PRAGMA_TABLE_INFO: Final[str] = "PRAGMA table_info(jobs)"
