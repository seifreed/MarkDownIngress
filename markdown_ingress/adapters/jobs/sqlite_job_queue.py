"""SQLite-backed job queue adapter."""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import queue
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from markdown_ingress.adapters.webhooks.http_notifier import HTTPWebhookNotifier
from markdown_ingress.core.interfaces import IWebhookNotifier
from markdown_ingress.core.ssrf import (
    is_blocked_hostname,
    is_blocked_ip_address,
    normalize_hostname,
    normalize_ip_for_ssrf,
    validate_http_url_no_ssrf,
)

_logger = logging.getLogger(__name__)

# Re-export so callers outside this module don't need to import sqlite3 directly.
SQLiteError = sqlite3.Error

_STOP_WORKER = object()
LEGACY_UNKNOWN_TTL_SECONDS = 3600


def _allowed_db_roots() -> list[Path]:
    """Directories where the persistent job DB is allowed to live.

    Defaults to ``$CWD``, ``$HOME/.markdown_ingress`` and the OS temp dir.
    Callers may override via ``MDI_ALLOWED_DB_DIRS`` (``:`` separated on POSIX).
    """
    import tempfile

    override = os.getenv("MDI_ALLOWED_DB_DIRS")
    if override:
        roots: list[Path] = []
        for raw in override.split(os.pathsep):
            raw = raw.strip()
            if not raw:
                continue
            try:
                roots.append(Path(raw).expanduser().resolve())
            except OSError:
                continue
        if roots:
            return roots
    candidates = [
        Path.cwd(),
        Path.home() / ".markdown_ingress",
        Path(tempfile.gettempdir()),
    ]
    # On macOS tempfile returns /var/folders/... which resolves via /private
    # prefix; include both to avoid spurious mismatches.
    resolved: list[Path] = []
    for c in candidates:
        try:
            resolved.append(c.resolve())
        except OSError:
            continue
    return resolved


def _validate_job_db_path(db_path: str | Path) -> Path:
    """Resolve and validate a job-queue DB path against the allowed roots.

    Security fix (S8): ``MDI_API_JOB_DB_PATH`` is user-configurable and previously
    accepted any path (including ``/etc/passwd.sqlite3``). We now require the
    resolved path to live inside an approved directory and refuse obvious
    traversal attempts.
    """
    raw = str(db_path).strip()
    if not raw:
        raise ValueError("Job DB path cannot be empty")
    if "\x00" in raw:
        raise ValueError("Job DB path contains null byte")
    candidate = Path(raw).expanduser()
    resolved = (candidate if candidate.is_absolute() else (Path.cwd() / candidate)).resolve()
    for root in _allowed_db_roots():
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        return resolved
    raise ValueError(
        f"Job DB path {resolved!s} is outside the allowed roots. "
        "Set MDI_ALLOWED_DB_DIRS to customise."
    )

# Blocked URL schemes and networks for SSRF protection
_BLOCKED_SCHEMES = {
    "file",
    "ftp",
    "data",
    "gopher",
    "dict",
    "ldap",
    "ldaps",
    "jar",
    "mailto",
    "news",
    "nntp",
    "irc",
    "mms",
    "rtsp",
    "svn",
    "git",
}


def _is_private_ip(ip_str: str) -> bool:
    """Check if an IP address is in a private network range (IPv4 and IPv6)."""
    import socket

    try:
        for _, _, _, _, sockaddr in socket.getaddrinfo(ip_str, None):
            ip_obj = ipaddress.ip_address(sockaddr[0])
            if _is_private_ip_address(ip_obj):
                return True
        return False
    except (socket.gaierror, OSError, ValueError):
        # If we can't resolve, treat as potentially dangerous
        return True


def _is_private_ip_address(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Check if an IP address object is in a private/reserved range."""
    return is_blocked_ip_address(normalize_ip_for_ssrf(ip_obj))


def _resolve_and_validate_ip(hostname: str, *, allow_local: bool = False) -> str | None:
    """Resolve hostname and validate the IP is not private/blocked.

    Returns the resolved IP address if valid, None if blocked.
    This should be called at webhook delivery time to prevent DNS rebinding attacks.
    """
    import socket

    try:
        normalized_hostname = normalize_hostname(hostname)
        # Get all addresses for the hostname
        addr_info = socket.getaddrinfo(normalized_hostname, None)
        if not addr_info:
            return None
        validated_ip: str | None = None
        for family, _, _, _, sockaddr in addr_info:
            ip_str = str(sockaddr[0])
            try:
                ip_obj = normalize_ip_for_ssrf(ipaddress.ip_address(ip_str))
            except ValueError:
                continue
            # Check blocked hosts
            if is_blocked_hostname(ip_str) or is_blocked_hostname(normalized_hostname):
                if not allow_local:
                    return None
            # Check private ranges
            if not allow_local and is_blocked_ip_address(ip_obj):
                return None
            if validated_ip is None:
                validated_ip = ip_str
        return validated_ip
    except (socket.gaierror, OSError):
        return None


def _validate_webhook_url(url: str | None, *, allow_local: bool = False) -> None:
    """Validate webhook URL to prevent SSRF attacks.

    Args:
        url: The webhook URL to validate
        allow_local: If True, allow localhost/private network addresses (for testing)

    Raises:
        ValueError: If the URL is invalid or potentially dangerous
    """
    if url is None:
        return
    if not isinstance(url, str):
        raise ValueError(f"webhook_url must be a string, got {type(url).__name__}")
    if len(url) > 2048:
        raise ValueError("webhook_url exceeds maximum length of 2048 characters")
    try:
        # Resolve DNS at submit time to provide defense-in-depth against SSRF.
        # The delivery-time re-check in _execute_job() provides a second layer.
        validate_http_url_no_ssrf(url, allow_local=allow_local, resolve_dns=True)
    except Exception as exc:
        message = str(exc)
        if "valid network location" in message or "valid host" in message:
            raise ValueError("webhook_url must include a hostname") from exc
        if "hostname blocked" in message:
            parsed = urlparse(url)
            hostname = normalize_hostname(parsed.hostname or "")
            raise ValueError(f"webhook_url blocked: hostname {hostname!r} is not allowed") from exc
        if "blocked range" in message:
            parsed = urlparse(url)
            hostname = normalize_hostname(parsed.hostname or "")
            raise ValueError(
                f"webhook_url blocked: hostname {hostname!r} is a private IP address"
            ) from exc
        raise ValueError(f"webhook_url blocked: {message.removeprefix('URL ')}") from exc
    # validate_http_url_no_ssrf already calls validate_hostname_for_ssrf internally;
    # a second call would perform a redundant DNS lookup for no added safety.


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _execute_task_in_subprocess(
    task: Callable[[], dict[str, Any]],
    conn,
) -> None:
    try:
        conn.send(("result", task()))
    except BaseException as exc:  # pragma: no cover - child process path
        try:
            conn.send(("exception", exc))
        except Exception:
            conn.send(("exception_payload", {"type": type(exc).__name__, "message": str(exc)}))
    finally:
        conn.close()


@dataclass
class JobRecord:
    job_id: str
    status: str
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    webhook_url: str | None = None
    ttl_seconds: int | None = None
    legacy_expires_at: str | None = None


class JobAlreadyRunningError(RuntimeError):
    """Raised when a queued task is dispatched for a job that is already running."""


class PersistentJobQueue:
    """SQLite-backed worker queue for long-running API jobs."""

    def __init__(
        self,
        db_path: str,
        worker_count: int = 2,
        ttl_seconds: int = 3600,
        max_queued_jobs: int = 100,
        webhook_max_retries: int = 2,
        webhook_retry_delay_seconds: float = 0.25,
        notifier: IWebhookNotifier | None = None,
        allow_local_webhooks: bool = False,
        job_timeout_seconds: float | None = None,
    ):
        self.db_path = _validate_job_db_path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.worker_count = max(1, worker_count)
        self.ttl_seconds = max(60, ttl_seconds)
        self.max_queued_jobs = max(1, max_queued_jobs)
        self.instance_id = str(uuid.uuid4())
        self.owner_pid = os.getpid()
        self.owner_start_time = self._get_process_start_time()
        self.lease_timeout_seconds = 30.0
        self.heartbeat_interval_seconds = 5.0
        self.lease_acquire_max_retries = 5
        self.lease_acquire_base_delay = 0.1
        self.lease_acquire_max_delay = 5.0
        self.notifier = notifier or HTTPWebhookNotifier(
            max_retries=webhook_max_retries,
            retry_delay_seconds=webhook_retry_delay_seconds,
            allow_local_webhooks=allow_local_webhooks,
        )
        self.allow_local_webhooks = allow_local_webhooks
        # Apply allow_local_webhooks to an injected notifier too so the
        # defense-in-depth SSRF check respects the queue's policy.
        if hasattr(self.notifier, "allow_local_webhooks"):
            self.notifier.allow_local_webhooks = allow_local_webhooks
        self.job_timeout_seconds = job_timeout_seconds
        self._queue: queue.Queue[tuple[str, Callable[[], dict[str, Any]]] | object] = queue.Queue()
        self._lock = threading.RLock()
        self._state_changed = threading.Condition(self._lock)
        self._workers: list[threading.Thread] = []
        self._workers_started = False
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._closed = False
        self._closing = False
        self._lease_lost = False
        self._shutdown_complete = False
        self._recovered_orphaned_jobs = False
        self._worker_stop_requested = False
        self._inline_jobs_running = 0
        self._init_db()
        self._acquire_lease()
        self._recover_orphaned_jobs()
        self._start_lease_heartbeat()

    def _get_process_start_time(self) -> float | None:
        """Get the current process start time for PID recycling detection."""
        try:
            # Try /proc filesystem (Linux)
            proc_path = f"/proc/{os.getpid()}/stat"
            if os.path.exists(proc_path):
                with open(proc_path) as f:
                    stat_line = f.read()
                parts = stat_line.split()
                if len(parts) > 21:
                    start_ticks = int(parts[21])
                    clk_tck = os.sysconf("SC_CLK_TCK")
                    if clk_tck > 0:
                        return start_ticks / clk_tck
        except (OSError, ValueError, IndexError) as e:
            _logger.debug("Could not read process start time from /proc: %s", e)
        # No reliable start time available — return None so callers
        # fall back to PID-only existence checks.
        return None

    @property
    def state(self) -> str:
        """Expose the queue lifecycle state for callers coordinating shutdown/retry."""
        if self._lease_lost:
            return "lease_lost"
        if self._shutdown_complete or self._closed:
            return "closed"
        if self._closing:
            return "closing"
        return "open"

    def _connect(self) -> sqlite3.Connection:
        # Re-validate path at open time to close the TOCTOU window between validation
        # in __init__ and the actual sqlite3.connect() call.
        validated = _validate_job_db_path(self.db_path)
        if validated != self.db_path:
            raise ValueError(
                f"Job DB path changed between validation and open: {self.db_path!r} -> {validated!r}"
            )
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""
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
                """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS queue_leases (
                    lease_name TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL,
                    owner_pid INTEGER NOT NULL DEFAULT 0,
                    owner_start_time REAL
                )
                """)
            lease_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(queue_leases)").fetchall()
            }
            if "owner_pid" not in lease_columns:
                conn.execute(
                    "ALTER TABLE queue_leases ADD COLUMN owner_pid INTEGER NOT NULL DEFAULT 0"
                )
            if "owner_start_time" not in lease_columns:
                conn.execute("ALTER TABLE queue_leases ADD COLUMN owner_start_time REAL")
            job_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
            }
            if "ttl_seconds" not in job_columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN ttl_seconds INTEGER")
            if "legacy_expires_at" not in job_columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN legacy_expires_at TEXT")
            legacy_rows = conn.execute("""
                SELECT job_id, completed_at
                FROM jobs
                WHERE completed_at IS NOT NULL
                  AND ttl_seconds IS NULL
                  AND legacy_expires_at IS NULL
                """).fetchall()
            if legacy_rows:
                updates = []
                for row in legacy_rows:
                    completed_at = row["completed_at"]
                    legacy_expires_at = None
                    if completed_at:
                        try:
                            completed_dt = self._parse_iso(completed_at)
                            legacy_expires_at = (
                                completed_dt + timedelta(seconds=LEGACY_UNKNOWN_TTL_SECONDS)
                            ).isoformat()
                        except ValueError:
                            legacy_expires_at = None
                    updates.append((legacy_expires_at, row["job_id"]))
                conn.executemany(
                    "UPDATE jobs SET legacy_expires_at = ? WHERE job_id = ?",
                    updates,
                )
            conn.commit()

    def _parse_iso(self, value: str) -> datetime:
        """Parse an ISO datetime string and normalize it to UTC.

        Legacy lease rows may omit timezone information. For lease comparisons
        we interpret naive timestamps as UTC instead of letting aware/naive
        arithmetic crash takeover logic.
        """
        try:
            parsed = datetime.fromisoformat(value)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid ISO datetime format: {value!r}") from exc
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _is_stale_heartbeat(self, heartbeat_at: str) -> bool:
        age_seconds = (datetime.now(UTC) - self._parse_iso(heartbeat_at)).total_seconds()
        return age_seconds > self.lease_timeout_seconds

    def _is_owner_process_alive(
        self, owner_pid: int, owner_start_time: float | None = None
    ) -> bool:
        """Check if a process with the given PID is alive.

        To prevent PID recycling vulnerabilities, this also checks process start time
        when available (on Unix systems with /proc filesystem).

        Args:
            owner_pid: The PID to check
            owner_start_time: Optional start time of the owner process (epoch seconds)

        Returns:
            True if the process appears to be the same owner, False otherwise
        """
        if owner_pid <= 0:
            return False
        try:
            os.kill(owner_pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            # Process exists but we can't signal it
            # Fall through to start time check
            pass

        # Additional check: verify process start time to detect PID recycling
        if owner_start_time is not None:
            try:
                # Try /proc filesystem (Linux/macOS)
                proc_path = f"/proc/{owner_pid}/stat"
                if os.path.exists(proc_path):
                    with open(proc_path) as f:
                        stat_line = f.read()
                    # The 22nd field (0-indexed: 21) is starttime in clock ticks
                    parts = stat_line.split()
                    if len(parts) > 21:
                        start_ticks = int(parts[21])
                        # Convert clock ticks to seconds
                        clk_tck = os.sysconf("SC_CLK_TCK")
                        if clk_tck > 0:
                            start_seconds = start_ticks / clk_tck
                            # Check if start time matches expected (within 1 second tolerance)
                            if abs(start_seconds - owner_start_time) > 1.0:
                                return False
            except (OSError, ValueError, IndexError) as e:
                # If we can't read /proc, fall back to just PID existence check
                _logger.debug("Could not verify owner process start time: %s", e)
        return True

    def _assert_queue_usable(
        self, *, require_lease: bool = False, allow_closing: bool = False
    ) -> None:
        if self._lease_lost:
            raise RuntimeError(
                "Job queue lease was lost; this instance can no longer accept or execute jobs"
            )
        if self._closed:
            raise RuntimeError("Job queue is closed")
        if self._closing and not allow_closing:
            raise RuntimeError("Job queue is closing")
        if require_lease and not self._still_owns_lease():
            self._lease_lost = True
            raise RuntimeError(
                "Job queue lease was lost; this instance can no longer accept or execute jobs"
            )

    def _still_owns_lease(self) -> bool:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT owner_id FROM queue_leases WHERE lease_name = ?",
                ("default",),
            ).fetchone()
        return row is not None and row["owner_id"] == self.instance_id

    def _acquire_lease(self) -> None:
        """Acquire lease with exponential backoff retry for lock contention.

        This prevents crashes when multiple instances start simultaneously.
        """
        last_error: Exception | None = None
        for attempt in range(self.lease_acquire_max_retries):
            try:
                with closing(self._connect()) as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    row = conn.execute(
                        "SELECT owner_id, heartbeat_at, owner_pid, owner_start_time FROM queue_leases WHERE lease_name = ?",
                        ("default",),
                    ).fetchone()
                    now_iso = _utcnow()
                    if row is None:
                        conn.execute(
                            "INSERT INTO queue_leases (lease_name, owner_id, heartbeat_at, owner_pid, owner_start_time) VALUES (?, ?, ?, ?, ?)",
                            (
                                "default",
                                self.instance_id,
                                now_iso,
                                self.owner_pid,
                                self.owner_start_time,
                            ),
                        )
                    else:
                        owner_id = row["owner_id"]
                        heartbeat_at = row["heartbeat_at"]
                        previous_heartbeat_stale = self._is_stale_heartbeat(heartbeat_at)
                        if owner_id != self.instance_id and not previous_heartbeat_stale:
                            # If the heartbeat is still fresh, it is the authoritative lease signal.
                            # Do not allow takeover just because PID metadata is missing or stale.
                            # Heartbeat freshness is what protects the lease from split-brain.
                            conn.rollback()
                            raise RuntimeError(
                                "Job queue DB is already owned by another active instance"
                            )
                        self._recovered_orphaned_jobs = owner_id == self.instance_id
                        conn.execute(
                            "UPDATE queue_leases SET owner_id = ?, heartbeat_at = ?, owner_pid = ?, owner_start_time = ? WHERE lease_name = ?",
                            (
                                self.instance_id,
                                now_iso,
                                self.owner_pid,
                                self.owner_start_time,
                                "default",
                            ),
                        )
                    conn.commit()
                return  # Success
            except sqlite3.OperationalError as e:
                # Database is locked - retry with exponential backoff
                last_error = e
                if attempt < self.lease_acquire_max_retries - 1:
                    delay = min(
                        self.lease_acquire_base_delay * (2**attempt),
                        self.lease_acquire_max_delay,
                    )
                    time.sleep(delay)
                    continue
        # All retries exhausted
        raise RuntimeError(
            f"Failed to acquire lease after {self.lease_acquire_max_retries} attempts: {last_error}"
        ) from last_error

    def _refresh_lease(self) -> None:
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                UPDATE queue_leases
                SET heartbeat_at = ?, owner_pid = ?, owner_start_time = ?
                WHERE lease_name = ? AND owner_id = ?
                """,
                (_utcnow(), self.owner_pid, self.owner_start_time, "default", self.instance_id),
            )
            conn.commit()
            # Read rowcount BEFORE closing the connection to avoid
            # use-after-close of the cursor.
            updated = cursor.rowcount
        # rowcount == 0 means the lease row was taken by another instance.
        # rowcount may be -1 on older sqlite3 builds ("unknown"), which we treat as success.
        if updated == 0:
            raise RuntimeError("Job queue lease was lost")

    def _start_lease_heartbeat(self) -> None:
        # Heartbeat retry configuration
        max_heartbeat_retries = 3
        heartbeat_base_delay = 1.0  # Base delay for exponential backoff
        heartbeat_max_delay = 10.0  # Maximum delay between retries

        def heartbeat_loop() -> None:
            consecutive_failures = 0
            skip_interval = False
            while True:
                if not skip_interval:
                    if self._heartbeat_stop.wait(self.heartbeat_interval_seconds):
                        break
                skip_interval = False
                try:
                    self._refresh_lease()
                    consecutive_failures = 0  # Reset on success
                except (
                    sqlite3.OperationalError,
                    sqlite3.InterfaceError,
                    sqlite3.NotSupportedError,
                ):
                    # Runtime fix (L4): transient DB errors (locks, timeouts,
                    # network-mount I/O hiccups surfacing as InterfaceError or
                    # NotSupportedError) must retry with backoff. Previously
                    # only OperationalError was retried, so a transient network
                    # glitch forced immediate shutdown.
                    consecutive_failures += 1
                    if consecutive_failures > max_heartbeat_retries:
                        # Max retries exceeded - initiate graceful shutdown
                        with self._lock:
                            self._lease_lost = True
                            self._closed = True
                            self._closing = True
                            if not self._worker_stop_requested:
                                worker_count = len(self._workers)
                                for _ in range(worker_count):
                                    self._queue.put(_STOP_WORKER)
                                self._worker_stop_requested = True
                        return
                    # Exponential backoff: 1s, 2s, 4s, etc. (capped at max_delay)
                    delay = min(
                        heartbeat_base_delay * (2 ** (consecutive_failures - 1)),
                        heartbeat_max_delay,
                    )
                    # Wait for either the backoff delay or stop signal
                    if self._heartbeat_stop.wait(timeout=delay):
                        break
                    skip_interval = True
                except Exception:  # pragma: no cover
                    # Non-transient errors - immediate shutdown
                    _logger.critical(
                        "Heartbeat loop fatal error, shutting down", exc_info=True
                    )
                    with self._lock:
                        self._lease_lost = True
                        self._closed = True
                        self._closing = True
                        if not self._worker_stop_requested:
                            worker_count = len(self._workers)
                            for _ in range(worker_count):
                                self._queue.put(_STOP_WORKER)
                            self._worker_stop_requested = True
                    return

        self._heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

    def close(
        self,
        *,
        inline_wait_timeout: float | None = None,
        preserve_state_on_inline_timeout: bool = False,
    ) -> None:
        # Runtime fix (L5): guard against concurrent close() callers racing on
        # the worker-join loop. ``_close_in_progress`` tracks a _currently-running_
        # close() (separate from ``_closing`` which the heartbeat also sets on
        # lease loss) so a second thread waits for the first to publish
        # ``_shutdown_complete`` instead of re-running the teardown. The flag
        # is reset on every exit path so a caller can retry close() after a
        # transient failure (e.g., workers that did not stop in time).
        if self._shutdown_complete:
            return
        claimed_close = False
        with self._lock:
            if self._shutdown_complete:
                return
            if getattr(self, "_close_in_progress", False):
                while not self._shutdown_complete:
                    self._state_changed.wait(timeout=0.1)
                return
            if preserve_state_on_inline_timeout and self._inline_jobs_running > 0:
                raise RuntimeError("Job queue inline jobs did not stop before lease release")
            if preserve_state_on_inline_timeout and any(
                worker.is_alive() for worker in self._workers
            ):
                raise RuntimeError("Job queue workers did not stop before lease release")
            self._close_in_progress = True
            claimed_close = True
            self._closing = True
            if not self._worker_stop_requested:
                worker_count = len(self._workers)
                for _ in range(worker_count):
                    self._queue.put(_STOP_WORKER)
                self._worker_stop_requested = True
            deadline = (
                None if inline_wait_timeout is None else time.monotonic() + inline_wait_timeout
            )
            while self._inline_jobs_running > 0:
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError(
                            "Job queue inline jobs did not stop before lease release"
                        )
                    self._state_changed.wait(timeout=min(0.1, remaining))
                else:
                    self._state_changed.wait(timeout=0.1)
        try:
            for worker in list(self._workers):
                worker.join(timeout=2.0)
            still_alive = [worker for worker in self._workers if worker.is_alive()]
            if still_alive:
                raise RuntimeError("Job queue workers did not stop before lease release")
            self._heartbeat_stop.set()
            if self._heartbeat_thread is not None:
                self._heartbeat_thread.join(timeout=2.0)
            with closing(self._connect()) as conn:
                if not self._lease_lost:
                    conn.execute(
                        "DELETE FROM queue_leases WHERE lease_name = ? AND owner_id = ?",
                        ("default", self.instance_id),
                    )
                conn.commit()
            with self._lock:
                self._closed = True
                self._shutdown_complete = True
                self._state_changed.notify_all()
        finally:
            if claimed_close and not self._shutdown_complete:
                # The teardown raised partway; release the claim so a retry
                # can pick up where this attempt left off.
                with self._lock:
                    self._close_in_progress = False
                    self._state_changed.notify_all()

    def _recover_orphaned_jobs(self) -> None:
        """Fail queued/running jobs from a previous process instance.

        The queue persists job state in SQLite, but executable callables only live in the
        current process. After a restart there is no safe way to replay queued/running jobs,
        so they must be marked failed instead of remaining pending forever.
        """
        if self._recovered_orphaned_jobs:
            return
        with closing(self._connect()) as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'failed',
                    completed_at = ?,
                    result_json = NULL,
                    ttl_seconds = COALESCE(ttl_seconds, ?),
                    error = CASE
                        WHEN status = 'running'
                            THEN 'Job interrupted by process restart; persisted task payload is not recoverable'
                        ELSE 'Job abandoned after process restart; persisted task payload is not recoverable'
                    END
                WHERE status IN ('queued', 'running')
                """,
                (_utcnow(), self.ttl_seconds),
            )
            conn.commit()
        self._recovered_orphaned_jobs = True

    def _ensure_workers(self) -> None:
        with self._lock:
            if self._workers_started:
                return
            for _ in range(self.worker_count):
                worker = threading.Thread(target=self._worker_loop, daemon=True)
                worker.start()
                self._workers.append(worker)
            self._workers_started = True

    def submit(
        self,
        task: Callable[[], dict[str, Any]],
        webhook_url: str | None = None,
        start_immediately: bool = False,
    ) -> JobRecord:
        self._assert_queue_usable(require_lease=True)
        # Validate webhook_url for SSRF protection
        _validate_webhook_url(webhook_url, allow_local=self.allow_local_webhooks)
        job_id = str(uuid.uuid4())
        record = JobRecord(
            job_id=job_id,
            status="queued",
            created_at=_utcnow(),
            webhook_url=webhook_url,
            ttl_seconds=self.ttl_seconds,
        )
        run_inline = False
        job_inserted = False
        with self._lock:
            self._assert_queue_usable(require_lease=True)
            self.cleanup_expired()
            with closing(self._connect()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                lease_row = conn.execute(
                    "SELECT owner_id FROM queue_leases WHERE lease_name = ?",
                    ("default",),
                ).fetchone()
                if lease_row is None or lease_row["owner_id"] != self.instance_id:
                    conn.rollback()
                    self._lease_lost = True
                    raise RuntimeError(
                        "Job queue lease was lost; this instance can no longer accept or execute jobs"
                    )
                row = conn.execute(
                    "SELECT COUNT(*) AS count FROM jobs WHERE status IN ('queued','running')"
                ).fetchone()
                pending = int(row["count"]) if row else 0
                if pending >= self.max_queued_jobs:
                    conn.rollback()
                    raise RuntimeError("Job queue is full")
                conn.execute(
                    """
                    INSERT INTO jobs (job_id, status, created_at, webhook_url, ttl_seconds)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        record.job_id,
                        record.status,
                        record.created_at,
                        record.webhook_url,
                        record.ttl_seconds,
                    ),
                )
                conn.commit()
            job_inserted = True
            try:
                self._assert_queue_usable(require_lease=True)
                if start_immediately:
                    self._inline_jobs_running += 1
                    run_inline = True
                else:
                    self._ensure_workers()
                    self._queue.put((job_id, task))
                    self._assert_queue_usable(require_lease=True, allow_closing=True)
            except Exception:
                if job_inserted:
                    self._delete_queued_job(job_id)
                raise
        if run_inline:
            try:
                self._execute_job(job_id, task)
            finally:
                with self._lock:
                    self._inline_jobs_running -= 1
                    self._state_changed.notify_all()
        return record

    def pending_count(self, *, cleanup_expired: bool = True) -> int:
        if cleanup_expired:
            self.cleanup_expired()
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM jobs WHERE status IN ('queued','running')"
            ).fetchone()
        return int(row["count"]) if row else 0

    @staticmethod
    def _safe_json_loads(raw: str | None):
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            _logger.warning("Corrupt result_json in job record, returning None")
            return None

    def get(self, job_id: str, *, cleanup_expired: bool = True) -> JobRecord | None:
        if cleanup_expired:
            self.cleanup_expired()
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        return JobRecord(
            job_id=row["job_id"],
            status=row["status"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            result=self._safe_json_loads(row["result_json"]),
            error=row["error"],
            webhook_url=row["webhook_url"],
            ttl_seconds=row["ttl_seconds"],
            legacy_expires_at=row["legacy_expires_at"],
        )

    def _delete_ttl_expired_jobs(self, conn, now_iso: str) -> None:
        conn.execute(
            """
            DELETE FROM jobs
            WHERE status NOT IN ('queued', 'running')
              AND ttl_seconds IS NOT NULL
              AND (
                  completed_at IS NULL
                  OR julianday(completed_at) IS NULL
                  OR julianday(?) > julianday(completed_at) + (ttl_seconds / 86400.0)
              )
            """,
            (now_iso,),
        )

    def _delete_legacy_expired_jobs(self, conn, now_iso: str) -> None:
        conn.execute(
            """
            DELETE FROM jobs
            WHERE status NOT IN ('queued', 'running')
              AND ttl_seconds IS NULL
              AND legacy_expires_at IS NOT NULL
              AND (
                  julianday(legacy_expires_at) IS NULL
                  OR julianday(?) > julianday(legacy_expires_at)
              )
            """,
            (now_iso,),
        )

    def _delete_corrupt_legacy_jobs(self, conn) -> None:
        conn.execute("""
            DELETE FROM jobs
            WHERE status NOT IN ('queued', 'running')
              AND ttl_seconds IS NULL
              AND legacy_expires_at IS NULL
              AND (completed_at IS NULL OR julianday(completed_at) IS NULL)
            """)

    def _compute_legacy_ttl_updates(
        self, conn, now_dt
    ) -> tuple[list[tuple[int, str, str]], list[str]]:
        rows = conn.execute("""
            SELECT job_id, completed_at
            FROM jobs
            WHERE status NOT IN ('queued', 'running')
              AND completed_at IS NOT NULL
              AND ttl_seconds IS NULL
              AND legacy_expires_at IS NULL
            """).fetchall()
        updates: list[tuple[int, str, str]] = []
        expired_ids: list[str] = []
        for row in rows:
            try:
                completed_dt = self._parse_iso(row["completed_at"])
            except ValueError:
                continue
            expires_at = (completed_dt + timedelta(seconds=LEGACY_UNKNOWN_TTL_SECONDS)).isoformat()
            if completed_dt + timedelta(seconds=LEGACY_UNKNOWN_TTL_SECONDS) <= now_dt:
                expired_ids.append(row["job_id"])
            else:
                updates.append((LEGACY_UNKNOWN_TTL_SECONDS, expires_at, row["job_id"]))
        return updates, expired_ids

    def _apply_legacy_ttl_backfill(
        self, conn, updates: list[tuple[int, str, str]], expired_ids: list[str]
    ) -> None:
        if expired_ids:
            conn.executemany(
                "DELETE FROM jobs WHERE job_id = ?",
                ((job_id,) for job_id in expired_ids),
            )
        if updates:
            conn.executemany(
                """
                UPDATE jobs
                SET ttl_seconds = ?,
                    legacy_expires_at = ?
                WHERE job_id = ?
                """,
                updates,
            )

    def cleanup_expired(self) -> None:
        now_iso = _utcnow()
        now_dt = datetime.now(UTC)
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._delete_ttl_expired_jobs(conn, now_iso)
            self._delete_legacy_expired_jobs(conn, now_iso)
            self._delete_corrupt_legacy_jobs(conn)
            updates, expired_ids = self._compute_legacy_ttl_updates(conn, now_dt)
            self._apply_legacy_ttl_backfill(conn, updates, expired_ids)
            conn.commit()

    def _worker_loop(self) -> None:
        while True:
            item = self._queue.get()
            job_id = None
            try:
                if item is _STOP_WORKER:
                    return
                job_id, task = cast(tuple[str, Callable[[], dict[str, Any]]], item)
                self._assert_queue_usable(require_lease=True, allow_closing=True)
                self._execute_job(job_id, task)
            except JobAlreadyRunningError as exc:
                _logger.info("Skipping duplicate execution for job %s: %s", job_id, exc)
            except Exception as exc:  # pragma: no cover
                _logger.warning("Worker loop exception for job %s: %s", job_id, exc)
                if job_id is not None:
                    try:
                        job = self.get(job_id, cleanup_expired=False)
                        if job is not None and job.status not in {"failed", "completed"}:
                            self._mark_failed(job_id, str(exc))
                    except Exception as mark_exc:
                        _logger.warning(
                            "Could not mark job %s as failed: %s",
                            job_id,
                            mark_exc,
                            exc_info=True,
                        )
            finally:
                self._queue.task_done()

    def _delete_queued_job(self, job_id: str) -> None:
        """Remove a queued job that was persisted but never successfully accepted."""
        with closing(self._connect()) as conn:
            conn.execute(
                "DELETE FROM jobs WHERE job_id = ? AND status = 'queued'",
                (job_id,),
            )
            conn.commit()

    def _execute_job(self, job_id: str, task: Callable[[], dict[str, Any]]) -> None:
        self._assert_queue_usable(require_lease=True, allow_closing=True)
        self._mark_running(job_id)
        try:
            # Execute task with optional timeout
            if self.job_timeout_seconds is not None:
                result = self._execute_with_timeout(task, self.job_timeout_seconds)
            else:
                result = task()
            if result is None:
                raise RuntimeError("Task returned None result")
            if not isinstance(result, dict):
                raise RuntimeError("Task returned non-dict result")
        except Exception as exc:
            # Try to mark the job as failed.  If _mark_failed itself raises
            # (e.g. DB lock contention), log the secondary failure but always
            # re-raise the *original* exception so the worker loop and inline
            # callers see the real error.
            try:
                self._mark_failed(job_id, str(exc))
            except Exception:
                _logger.warning(
                    "Failed to mark job %s as failed (original error: %s)",
                    job_id,
                    exc,
                )
            raise
        if not self._still_owns_lease():
            self._lease_lost = True
            self._closed = True
            # Job result computed but not yet persisted.
            # Try to preserve the result while marking as failed so callers can retrieve it.
            # This is similar to _mark_webhook_failed but for lease loss scenarios.
            try:
                self._mark_completed_preserve_result(
                    job_id, result, "Job queue lease was lost before result persistence"
                )
            except Exception as e:
                # If preservation fails, fall back to marking as failed without result
                _logger.debug("Failed to preserve job result on lease loss: %s", e)
                self._mark_failed(job_id, "Job queue lease was lost before result persistence")
            raise RuntimeError("Job queue lease was lost before result persistence")
        self._mark_completed(job_id, result)
        job = self.get(job_id, cleanup_expired=False)
        if job and job.webhook_url:
            if not self._still_owns_lease():
                self._lease_lost = True
                self._closed = True
                # Job was already marked completed successfully in the database.
                # The result is persisted, but webhook delivery cannot proceed.
                # This is acceptable: callers polling the queue can still retrieve results.
                # Do NOT mark as failed - the job completed successfully.
                raise RuntimeError("Job queue lease was lost before webhook delivery")
            # Re-validate webhook URL at delivery time to prevent DNS rebinding attacks
            validated_ip: str | None = None
            if not self.allow_local_webhooks:
                # BUG FIX: Use proper URL parsing instead of fragile string splitting
                # to prevent IndexError on malformed URLs
                try:
                    parsed = urlparse(job.webhook_url)
                    hostname = parsed.hostname
                except Exception as e:
                    _logger.warning("Failed to parse webhook URL: %s", str(e)[:100])
                    self._mark_webhook_failed(job_id, f"Invalid webhook URL: {e}")
                    raise RuntimeError(f"Invalid webhook URL: {e}") from e
                if hostname is None:
                    _logger.warning("Webhook URL has no hostname: %s", job.webhook_url[:100])
                    self._mark_webhook_failed(job_id, "Webhook URL has no hostname")
                    raise RuntimeError("Webhook URL has no hostname")
                validated_ip = _resolve_and_validate_ip(hostname)
                if validated_ip is None:
                    # DNS rebinding detected or private IP resolution at request time
                    self._mark_webhook_failed(
                        job_id, "Webhook URL resolved to blocked/private IP at delivery time"
                    )
                    raise RuntimeError(
                        "Webhook URL resolved to blocked/private IP at delivery time"
                    )
            try:
                # BUG FIX: Pass validated_ip to notifier for DNS pinning
                # This prevents TOCTOU race where DNS changes between validation and connection
                self.notifier.notify(
                    job.webhook_url,
                    {
                        "job_id": job.job_id,
                        "status": job.status,
                        "completed_at": job.completed_at,
                        "result": job.result,
                        "error": job.error,
                    },
                    validated_ip=validated_ip,
                )
            except Exception as exc:
                # Webhook failure: preserve result but mark as failed
                # The job result is preserved so callers can still retrieve it
                self._mark_webhook_failed(job_id, f"Webhook delivery failed: {exc}")
                raise

    def _execute_with_timeout(
        self, task: Callable[[], dict[str, Any]], timeout_seconds: float
    ) -> dict[str, Any]:
        """Execute a task and return control to the caller when the timeout expires.

        Timed-out tasks run in a daemon thread and may continue until the task
        returns; the queue observes the timeout deadline instead of waiting for
        that background work to finish.

        Args:
            task: Callable that returns a result dict
            timeout_seconds: Maximum time to wait for completion

        Returns:
            The result from task()

        Raises:
            RuntimeError: If task times out or returns None
            Exception: Any exception raised by the task
        """
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        if os.name != "posix":
            return self._execute_with_timeout_thread_fallback(task, timeout_seconds)
        return self._execute_with_timeout_process(task, timeout_seconds)

    def _execute_with_timeout_process(
        self,
        task: Callable[[], dict[str, Any]],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        # Use the same daemon-thread timeout path on POSIX. ThreadPoolExecutor
        # shutdown waits for the worker by default, which defeats the timeout.
        return self._execute_with_timeout_thread_fallback(task, timeout_seconds)

    def _execute_with_timeout_thread_fallback(
        self,
        task: Callable[[], dict[str, Any]],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """Execute a task in a daemon thread with a caller-visible timeout.

        Python cannot hard-cancel worker threads, so timed-out work may continue
        briefly in the background until the task returns.
        """
        result_container: list[dict[str, Any] | None] = [None]
        exception_container: list[Exception | None] = [None]

        def run_task() -> None:
            try:
                result_container[0] = task()
            except Exception as e:
                exception_container[0] = e

        thread = threading.Thread(target=run_task, daemon=True)
        thread.start()
        thread.join(timeout=timeout_seconds)

        if thread.is_alive():
            _logger.warning(
                "Job execution timed out after %s seconds; "
                "background thread may continue running until the task returns.",
                timeout_seconds,
            )
            raise RuntimeError(f"Job execution timed out after {timeout_seconds} seconds")

        if exception_container[0] is not None:
            raise exception_container[0]

        if result_container[0] is None:
            raise RuntimeError("Task returned None result")

        return result_container[0]

    # =========================================================================
    # Job State Machine
    # =========================================================================
    # Valid state transitions:
    #   queued -> running   : Job picked up by worker
    #   queued -> failed    : Orphaned job recovery on startup
    #   running -> completed: Job finished successfully
    #   running -> failed   : Job finished with error
    #   completed -> failed : Webhook delivery failed (result preserved)
    #
    # State validation ensures:
    #   - Jobs cannot transition from 'completed' to 'failed' (except for webhook failure)
    #   - Jobs cannot transition from 'failed' to 'completed'
    #   - Jobs cannot be marked 'running' if already terminal (completed/failed)
    #
    # All state transitions use atomic UPDATE with WHERE clause to prevent TOCTOU races.
    # =========================================================================

    def _mark_running(self, job_id: str) -> None:
        """Transition job from 'queued' to 'running'.

        Uses atomic UPDATE with WHERE clause to prevent TOCTOU race conditions.
        Raises:
            RuntimeError: If job is in a terminal state (completed/failed) or not found.
            JobAlreadyRunningError: If another worker already owns execution for this job.
        """
        with closing(self._connect()) as conn:
            # Atomic UPDATE with status check in WHERE clause prevents TOCTOU race
            cursor = conn.execute(
                "UPDATE jobs SET status = ?, started_at = ? WHERE job_id = ? AND status = ?",
                ("running", _utcnow(), job_id, "queued"),
            )
            conn.commit()
            if cursor.rowcount == 0:
                # Job wasn't in 'queued' state - find current state for error message.
                # Note: this SELECT is a best-effort diagnostic — the status may have
                # changed between the commit above and this read (TOCTOU window).
                row = conn.execute(
                    "SELECT status FROM jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
                if row is None:
                    raise RuntimeError(f"Job {job_id} not found")
                if row["status"] == "running":
                    raise JobAlreadyRunningError(
                        f"Job {job_id} is already running and cannot be executed twice"
                    )
                raise RuntimeError(
                    f"Invalid state transition: job {job_id} is '{row['status']}', expected 'queued'"
                )

    def _mark_completed(self, job_id: str, result: dict[str, Any]) -> None:
        """Transition job from 'running' to 'completed'.

        Uses atomic UPDATE with WHERE clause to prevent TOCTOU race conditions.
        This is idempotent: if job is already 'completed', this is a no-op.

        Raises:
            RuntimeError: If job is in a terminal state (failed) or not found.
        """
        with closing(self._connect()) as conn:
            # Atomic UPDATE with status check in WHERE clause prevents TOCTOU race
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status = ?, completed_at = ?, result_json = ?, error = NULL
                WHERE job_id = ? AND status = ?
                """,
                ("completed", _utcnow(), json.dumps(result), job_id, "running"),
            )
            conn.commit()
            if cursor.rowcount == 0:
                row = conn.execute(
                    "SELECT status FROM jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
                if row is None:
                    raise RuntimeError(f"Job {job_id} not found")
                # If already completed, this is likely a duplicate call - idempotent no-op
                if row["status"] == "completed":
                    return
                raise RuntimeError(
                    f"Invalid state transition: job {job_id} is '{row['status']}', expected 'running'"
                )

    def _mark_failed(self, job_id: str, error: str) -> None:
        """Transition job from 'queued' or 'running' to 'failed'.

        Uses atomic UPDATE with WHERE clause to prevent TOCTOU race conditions.
        This is idempotent: if job is already 'failed', this is a no-op.

        Note: Use _mark_webhook_failed() for webhook failures where the job result
        was computed but notification failed. This method clears result_json to
        maintain consistency with the failed state.

        BUG FIX: Removed 'completed' from allowed source states to avoid confusion.
        Jobs should only transition to 'failed' from 'queued' or 'running'.
        Completed jobs that need to transition to 'failed' should use
        _mark_webhook_failed() which preserves the result.
        """
        with closing(self._connect()) as conn:
            # Atomic UPDATE with status check in WHERE clause prevents TOCTOU race
            # BUG FIX: Only allow transition from queued or running to failed
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status = ?, completed_at = ?, result_json = NULL, error = ?
                WHERE job_id = ? AND status IN ('queued', 'running')
                """,
                ("failed", _utcnow(), error, job_id),
            )
            conn.commit()
            if cursor.rowcount == 0:
                # Check if job exists and is already failed (idempotent)
                row = conn.execute(
                    "SELECT status FROM jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
                if row is None:
                    return  # Job doesn't exist, nothing to fail
                if row["status"] == "failed":
                    return  # Already failed - idempotent no-op
                # Job is in an unexpected state
                raise RuntimeError(
                    f"Invalid state transition: job {job_id} is '{row['status']}', cannot transition to 'failed'"
                )

    def _mark_webhook_failed(self, job_id: str, error: str) -> None:
        """Transition job from 'completed' to 'failed' while preserving the result.

        This is specifically for webhook failures where the job computation succeeded
        but the notification failed. The result is preserved so callers can still
        retrieve it via the API.

        Uses atomic UPDATE with WHERE clause to prevent TOCTOU race conditions.
        This is idempotent: if job is already 'failed', this is a no-op.
        """
        with closing(self._connect()) as conn:
            # First, update the job status to failed while preserving result_json
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status = ?, error = ?
                WHERE job_id = ? AND status = 'completed'
                """,
                ("failed", error, job_id),
            )
            conn.commit()
            if cursor.rowcount == 0:
                # Check if job exists and is already failed (idempotent)
                row = conn.execute(
                    "SELECT status FROM jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
                if row is None:
                    return  # Job doesn't exist, nothing to fail
                if row["status"] == "failed":
                    return  # Already failed - idempotent no-op
                # Job is in an unexpected state (e.g., still running)
                raise RuntimeError(
                    f"Invalid state transition: job {job_id} is '{row['status']}', expected 'completed'"
                )

    def _mark_completed_preserve_result(
        self, job_id: str, result: dict[str, Any], error: str
    ) -> None:
        """Try to preserve job result while marking as failed due to lease loss.

        This is called when a job completes but the lease was lost before persistence.
        We attempt to save the result while marking status as 'failed' so callers
        can still retrieve the computed result.

        Uses atomic UPDATE with WHERE clause to prevent TOCTOU race conditions.
        """
        result_json = json.dumps(result) if result is not None else None
        with closing(self._connect()) as conn:
            # Try to mark as failed while preserving result
            # Only update if still in 'running' state (lease might have been taken by another process)
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status = ?, completed_at = ?, result_json = ?, error = ?
                WHERE job_id = ? AND status = 'running'
                """,
                ("failed", _utcnow(), result_json, error, job_id),
            )
            conn.commit()
            if cursor.rowcount == 0:
                # Job was already transitioned by another process - nothing to do
                return


def check_external_owner_still_owns(
    db_path: Path,
    is_stale_fn: Callable[[str], bool],
    on_backend_error: Callable[[str], None] | None = None,
) -> bool:
    """Read the queue lease table to determine if an external owner's lease is still valid.

    Accepts ``is_stale_fn`` so the caller's heartbeat-staleness logic is injected
    rather than duplicated, keeping this module free of presentation-layer imports.
    Returns True if the lease is still held (not stale), False otherwise.
    Raises RuntimeError on unrecoverable backend errors.
    """
    db_uri = f"{db_path.resolve().as_uri()}?mode=ro"
    try:
        with closing(sqlite3.connect(db_uri, timeout=0.0, uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT owner_id, heartbeat_at, owner_pid FROM queue_leases WHERE lease_name = ?",
                ("default",),
            ).fetchone()
    except sqlite3.Error as exc:
        message = str(exc).lower()
        if isinstance(exc, sqlite3.OperationalError) and ("locked" in message or "busy" in message):
            return True
        if on_backend_error is not None:
            on_backend_error("backend_error")
        raise RuntimeError(f"Job queue backend read failed during repair: {exc}")
    if row is None:
        return False
    heartbeat_at = row["heartbeat_at"]
    return not is_stale_fn(heartbeat_at)
