import json
import os
import threading
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from markdown_ingress.adapters.jobs.sqlite_job_queue import (
    _STOP_WORKER,
    LEGACY_UNKNOWN_TTL_SECONDS,
    PersistentJobQueue,
    _resolve_and_validate_ip,
    _validate_webhook_url,
)


def test_persistent_job_queue_executes_immediately_and_persists_result(tmp_path: Path):
    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=3600)

    job = queue.submit(lambda: {"ok": True, "count": 1}, start_immediately=True)
    stored = queue.get(job.job_id)

    assert stored is not None
    assert stored.status == "completed"
    assert stored.result == {"ok": True, "count": 1}


def test_persistent_job_queue_cleans_up_expired_jobs(tmp_path: Path):
    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=60)
    job = queue.submit(lambda: {"ok": True}, start_immediately=True)
    assert queue.get(job.job_id) is not None

    with closing(queue._connect()) as conn:  # deliberate white-box test
        conn.execute(
            "UPDATE jobs SET completed_at = ? WHERE job_id = ?",
            ("2000-01-01T00:00:00+00:00", job.job_id),
        )
        conn.commit()

    queue.cleanup_expired()
    assert queue.get(job.job_id) is None


def test_persistent_job_queue_preserves_queued_and_running_jobs_during_cleanup(tmp_path: Path):
    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=60)

    with closing(queue._connect()) as conn:
        conn.execute(
            "INSERT INTO jobs (job_id, status, created_at) VALUES (?, ?, ?)",
            ("stale-queued", "queued", "2000-01-01T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO jobs (job_id, status, created_at, started_at) VALUES (?, ?, ?, ?)",
            (
                "stale-running",
                "running",
                "2000-01-01T00:00:00+00:00",
                "2000-01-01T00:10:00+00:00",
            ),
        )
        conn.commit()

    queue.cleanup_expired()

    assert queue.pending_count() == 2
    assert queue.get("stale-queued") is not None
    assert queue.get("stale-running") is not None


def test_persistent_job_queue_marks_orphaned_jobs_failed_on_startup(tmp_path: Path):
    db_path = tmp_path / "jobs.sqlite3"
    first_queue = PersistentJobQueue(str(db_path), worker_count=1, ttl_seconds=3600)

    with closing(first_queue._connect()) as conn:
        conn.execute(
            "INSERT INTO jobs (job_id, status, created_at) VALUES (?, ?, ?)",
            ("queued-orphan", "queued", "2026-03-29T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO jobs (job_id, status, created_at, started_at) VALUES (?, ?, ?, ?)",
            (
                "running-orphan",
                "running",
                "2026-03-29T00:00:00+00:00",
                "2026-03-29T00:01:00+00:00",
            ),
        )
        conn.commit()

    first_queue.close()
    recovered_queue = PersistentJobQueue(str(db_path), worker_count=1, ttl_seconds=3600)

    queued = recovered_queue.get("queued-orphan")
    running = recovered_queue.get("running-orphan")
    assert queued is not None
    assert running is not None
    assert queued.status == "failed"
    assert running.status == "failed"
    assert "process restart" in queued.error
    assert "process restart" in running.error
    assert recovered_queue.pending_count() == 0
    recovered_queue.close()


def test_persistent_job_queue_rejects_second_live_owner(tmp_path: Path):
    db_path = tmp_path / "jobs.sqlite3"
    first_queue = PersistentJobQueue(str(db_path), worker_count=1, ttl_seconds=3600)

    with pytest.raises(RuntimeError, match="already owned by another active instance"):
        PersistentJobQueue(str(db_path), worker_count=1, ttl_seconds=3600)

    first_queue.close()


def test_persistent_job_queue_allows_takeover_when_heartbeat_is_stale(tmp_path: Path):
    db_path = tmp_path / "jobs.sqlite3"
    first_queue = PersistentJobQueue(str(db_path), worker_count=1, ttl_seconds=3600)

    with closing(first_queue._connect()) as conn:
        conn.execute(
            "UPDATE queue_leases SET heartbeat_at = ?, owner_pid = ?, owner_start_time = ? WHERE lease_name = ?",
            ("2000-01-01T00:00:00+00:00", 0, None, "default"),
        )
        conn.commit()

    takeover_queue = PersistentJobQueue(str(db_path), worker_count=1, ttl_seconds=3600)
    assert takeover_queue.instance_id != first_queue.instance_id
    takeover_queue.close()


def test_persistent_job_queue_allows_takeover_when_heartbeat_is_stale_and_naive(tmp_path: Path):
    db_path = tmp_path / "jobs.sqlite3"
    first_queue = PersistentJobQueue(str(db_path), worker_count=1, ttl_seconds=3600)

    with closing(first_queue._connect()) as conn:
        conn.execute(
            "UPDATE queue_leases SET heartbeat_at = ?, owner_pid = ?, owner_start_time = ? WHERE lease_name = ?",
            ("2000-01-01 00:00:00", 0, None, "default"),
        )
        conn.commit()

    takeover_queue = PersistentJobQueue(str(db_path), worker_count=1, ttl_seconds=3600)
    assert takeover_queue.instance_id != first_queue.instance_id
    takeover_queue.close()


def test_persistent_job_queue_rejects_second_owner_when_pid_metadata_is_missing_but_heartbeat_is_fresh(
    tmp_path: Path,
):
    db_path = tmp_path / "jobs.sqlite3"
    first_queue = PersistentJobQueue(str(db_path), worker_count=1, ttl_seconds=3600)

    with closing(first_queue._connect()) as conn:
        conn.execute(
            "UPDATE queue_leases SET owner_pid = ?, owner_start_time = ? WHERE lease_name = ?",
            (0, None, "default"),
        )
        conn.commit()

    takeover_queue = None
    with pytest.raises(RuntimeError, match="already owned by another active instance"):
        takeover_queue = PersistentJobQueue(str(db_path), worker_count=1, ttl_seconds=3600)

    if takeover_queue is not None:
        takeover_queue.close()
    first_queue.close()


def test_persistent_job_queue_rejects_second_owner_when_pid_is_dead_but_heartbeat_is_fresh(
    tmp_path: Path,
):
    db_path = tmp_path / "jobs.sqlite3"
    first_queue = PersistentJobQueue(str(db_path), worker_count=1, ttl_seconds=3600)

    with closing(first_queue._connect()) as conn:
        conn.execute(
            "UPDATE queue_leases SET owner_pid = ?, owner_start_time = ? WHERE lease_name = ?",
            (999999, None, "default"),
        )
        conn.commit()

    takeover_queue = None
    with pytest.raises(RuntimeError, match="already owned by another active instance"):
        takeover_queue = PersistentJobQueue(str(db_path), worker_count=1, ttl_seconds=3600)

    if takeover_queue is not None:
        takeover_queue.close()
    first_queue.close()


def test_persistent_job_queue_old_owner_cannot_submit_after_stale_takeover(tmp_path: Path):
    db_path = tmp_path / "jobs.sqlite3"
    first_queue = PersistentJobQueue(str(db_path), worker_count=1, ttl_seconds=3600)

    with closing(first_queue._connect()) as conn:
        conn.execute(
            "UPDATE queue_leases SET heartbeat_at = ? WHERE lease_name = ?",
            ("2000-01-01T00:00:00+00:00", "default"),
        )
        conn.commit()

    takeover_queue = PersistentJobQueue(str(db_path), worker_count=1, ttl_seconds=3600)

    with pytest.raises(RuntimeError, match="lease was lost"):
        first_queue.submit(lambda: {"ok": True}, start_immediately=False)

    takeover_queue.close()


def test_persistent_job_queue_submit_detects_atomic_lease_loss_before_insert(tmp_path: Path):
    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=3600)

    def steal_lease_in_cleanup():
        with closing(queue._connect()) as conn:
            conn.execute(
                "UPDATE queue_leases SET owner_id = ?, owner_pid = ? WHERE lease_name = ?",
                ("other-owner", 999999, "default"),
            )
            conn.commit()

    queue.cleanup_expired = steal_lease_in_cleanup

    with pytest.raises(RuntimeError, match="lease was lost"):
        queue.submit(lambda: {"ok": True}, start_immediately=False)

    assert _job_ids(queue) == []
    assert queue.state == "lease_lost"


def test_persistent_job_queue_submit_cleans_up_job_if_lease_lost_after_insert(tmp_path: Path):
    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=3600)

    def steal_lease_during_worker_start():
        with closing(queue._connect()) as conn:
            conn.execute(
                "UPDATE queue_leases SET owner_id = ?, owner_pid = ? WHERE lease_name = ?",
                ("other-owner", 999999, "default"),
            )
            conn.commit()

    queue._ensure_workers = steal_lease_during_worker_start

    with pytest.raises(RuntimeError, match="lease was lost"):
        queue.submit(lambda: {"ok": True}, start_immediately=False)

    assert _job_ids(queue) == []
    assert queue.state == "lease_lost"


def test_persistent_job_queue_close_waits_until_submit_enqueues_job(tmp_path: Path):
    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=3600)

    class StoppedWorker:
        def join(self, timeout=None):
            return None

        def is_alive(self):
            return False

    queue._workers = [StoppedWorker()]
    queue._workers_started = True

    entered_ensure = threading.Event()
    release_ensure = threading.Event()
    recorded_puts: list[object] = []

    def blocking_ensure_workers():
        entered_ensure.set()
        release_ensure.wait(timeout=5.0)

    def record_put(item):
        recorded_puts.append(item)

    queue._ensure_workers = blocking_ensure_workers
    queue._queue.put = record_put

    submit_thread = threading.Thread(
        target=lambda: queue.submit(lambda: {"ok": True}, start_immediately=False)
    )
    submit_thread.start()

    assert entered_ensure.wait(timeout=5.0)

    close_thread = threading.Thread(target=queue.close)
    close_thread.start()
    time.sleep(0.1)
    assert close_thread.is_alive() is True

    release_ensure.set()
    submit_thread.join(timeout=5.0)
    close_thread.join(timeout=5.0)

    assert submit_thread.is_alive() is False
    assert close_thread.is_alive() is False
    assert len(recorded_puts) == 2
    assert recorded_puts[0] is not _STOP_WORKER
    assert recorded_puts[1] is _STOP_WORKER


def test_persistent_job_queue_detects_lease_loss(tmp_path: Path):
    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=3600)

    with closing(queue._connect()) as conn:
        conn.execute(
            "UPDATE queue_leases SET owner_id = ?, owner_pid = ? WHERE lease_name = ?",
            ("other-owner", 999999, "default"),
        )
        conn.commit()

    with pytest.raises(RuntimeError, match="lease was lost"):
        queue._refresh_lease()

    queue._lease_lost = True
    queue._closed = True
    with pytest.raises(RuntimeError, match="lease was lost"):
        queue.submit(lambda: {"ok": True}, start_immediately=False)


def test_persistent_job_queue_can_close_after_lease_loss(tmp_path: Path):
    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=3600)
    queue._lease_lost = True
    queue._closed = True
    queue._workers = []
    queue._workers_started = True

    queue.close()

    assert queue.state == "lease_lost"
    assert queue._shutdown_complete is True


def test_persistent_job_queue_fails_job_if_lease_lost_before_persist(tmp_path: Path):
    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=3600)

    def lose_lease_and_return_result():
        with closing(queue._connect()) as conn:
            conn.execute(
                "UPDATE queue_leases SET owner_id = ?, owner_pid = ? WHERE lease_name = ?",
                ("other-owner", 999999, "default"),
            )
            conn.commit()
        return {"ok": True}

    with pytest.raises(RuntimeError, match="lease was lost before result persistence"):
        queue.submit(lose_lease_and_return_result, start_immediately=True)

    with closing(queue._connect()) as conn:
        row = conn.execute("SELECT job_id FROM jobs").fetchone()
    assert row is not None
    stored = queue.get(row["job_id"])
    assert stored is not None
    assert stored.status == "failed"
    assert stored.error == "Job queue lease was lost before result persistence"
    assert stored.result == {"ok": True}


def test_worker_loop_marks_queued_job_failed_if_lease_lost_before_execution(tmp_path: Path):
    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=3600)

    with closing(queue._connect()) as conn:
        conn.execute(
            "INSERT INTO jobs (job_id, status, created_at) VALUES (?, ?, ?)",
            ("queued-job", "queued", datetime.now(UTC).isoformat()),
        )
        conn.commit()

    queue._lease_lost = True
    queue._queue.put(("queued-job", lambda: {"ok": True}))
    queue._queue.put(_STOP_WORKER)

    worker = threading.Thread(target=queue._worker_loop, daemon=True)
    worker.start()
    worker.join(timeout=5.0)

    stored = queue.get("queued-job", cleanup_expired=False)
    assert stored is not None
    assert stored.status == "failed"
    assert "lease was lost" in (stored.error or "")


def test_execute_job_rejects_duplicate_running_job_without_reprocessing(tmp_path: Path):
    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=3600)

    with closing(queue._connect()) as conn:
        conn.execute(
            """
            INSERT INTO jobs (job_id, status, created_at, started_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                "running-job",
                "running",
                datetime.now(UTC).isoformat(),
                datetime.now(UTC).isoformat(),
            ),
        )
        conn.commit()

    calls = {"count": 0}

    def task():
        calls["count"] += 1
        return {"ok": True}

    with pytest.raises(RuntimeError, match="already running"):
        queue._execute_job("running-job", task)

    stored = queue.get("running-job", cleanup_expired=False)
    assert stored is not None
    assert stored.status == "running"
    assert calls["count"] == 0


def test_external_owner_get_hides_expired_rows_when_cleanup_requested(tmp_path: Path):
    from markdown_ingress.api_server import _ExternalOwnerJobQueue

    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=60)
    job = queue.submit(lambda: {"ok": True}, start_immediately=True)

    with closing(queue._connect()) as conn:
        conn.execute(
            "UPDATE jobs SET completed_at = ? WHERE job_id = ?",
            ("2000-01-01T00:00:00+00:00", job.job_id),
        )
        conn.commit()

    external = _ExternalOwnerJobQueue(str(tmp_path / "jobs.sqlite3"))

    assert external.get(job.job_id, cleanup_expired=True) is None
    assert external.get(job.job_id, cleanup_expired=False) is not None


def test_persistent_job_queue_preserves_empty_result_if_lease_lost_before_persist(tmp_path: Path):
    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=3600)

    def lose_lease_and_return_empty_result():
        with closing(queue._connect()) as conn:
            conn.execute(
                "UPDATE queue_leases SET owner_id = ?, owner_pid = ? WHERE lease_name = ?",
                ("other-owner", 999999, "default"),
            )
            conn.commit()
        return {}

    with pytest.raises(RuntimeError, match="lease was lost before result persistence"):
        queue.submit(lose_lease_and_return_empty_result, start_immediately=True)

    with closing(queue._connect()) as conn:
        row = conn.execute("SELECT job_id FROM jobs").fetchone()
    assert row is not None
    stored = queue.get(row["job_id"])
    assert stored is not None
    assert stored.status == "failed"
    assert stored.result == {}


def test_persistent_job_queue_close_raises_if_worker_does_not_stop(tmp_path: Path):
    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=3600)

    class StuckWorker:
        def join(self, timeout=None):
            return None

        def is_alive(self):
            return True

    queue._workers = [StuckWorker()]
    queue._workers_started = True

    with pytest.raises(RuntimeError, match="did not stop before lease release"):
        queue.close()


def test_persistent_job_queue_close_can_be_retried_after_workers_finish(tmp_path: Path):
    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=3600)

    class FlakyWorker:
        def __init__(self):
            self.calls = 0

        def join(self, timeout=None):
            self.calls += 1
            return None

        def is_alive(self):
            return self.calls < 2

    queue._workers = [FlakyWorker()]
    queue._workers_started = True

    with pytest.raises(RuntimeError, match="did not stop before lease release"):
        queue.close()

    assert queue._closed is False
    assert queue._closing is True

    queue.close()
    assert queue._closed is True


def test_persistent_job_queue_exposes_public_state_transitions(tmp_path: Path):
    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=3600)
    assert queue.state == "open"

    class StuckWorker:
        def join(self, timeout=None):
            return None

        def is_alive(self):
            return True

    queue._workers = [StuckWorker()]
    queue._workers_started = True

    with pytest.raises(RuntimeError):
        queue.close()

    assert queue.state == "closing"


def test_persistent_job_queue_close_releases_lease(tmp_path: Path):
    db_path = tmp_path / "jobs.sqlite3"
    queue = PersistentJobQueue(str(db_path), worker_count=1, ttl_seconds=3600)

    queue.close()

    reopened = PersistentJobQueue(str(db_path), worker_count=1, ttl_seconds=3600)
    reopened.close()


def test_persistent_job_queue_close_keeps_lease_alive_while_draining_workers(tmp_path: Path):
    db_path = tmp_path / "jobs.sqlite3"
    queue = PersistentJobQueue(str(db_path), worker_count=1, ttl_seconds=3600)
    queue.lease_timeout_seconds = 0.2
    queue.heartbeat_interval_seconds = 0.05

    release = threading.Event()

    job = queue.submit(
        lambda: (release.wait(), {"ok": True})[1],
        start_immediately=False,
    )

    deadline = time.time() + 5.0
    stored = queue.get(job.job_id)
    while stored is not None and stored.status != "running" and time.time() < deadline:
        time.sleep(0.05)
        stored = queue.get(job.job_id)

    assert stored is not None
    assert stored.status == "running"

    close_errors: list[str] = []

    def close_queue() -> None:
        try:
            queue.close()
        except RuntimeError as exc:
            close_errors.append(str(exc))

    closer = threading.Thread(target=close_queue)
    closer.start()

    time.sleep(0.35)
    with pytest.raises(RuntimeError, match="already owned by another active instance"):
        PersistentJobQueue(str(db_path), worker_count=1, ttl_seconds=3600)

    release.set()
    closer.join(timeout=5.0)

    assert closer.is_alive() is False
    assert close_errors == []

    reopened = PersistentJobQueue(str(db_path), worker_count=1, ttl_seconds=3600)
    reopened.close()


def test_persistent_job_queue_marks_failed_jobs(tmp_path: Path):
    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=3600)

    with pytest.raises(RuntimeError):
        queue.submit(lambda: (_ for _ in ()).throw(RuntimeError("boom")), start_immediately=True)

    # start_immediately executes inline, so verify the persisted row was marked failed
    with closing(queue._connect()) as conn:
        row = conn.execute("SELECT status, error FROM jobs").fetchone()
    assert row is not None
    assert row["status"] == "failed"
    assert "boom" in row["error"]


def test_persistent_job_queue_rejects_when_full(tmp_path: Path):
    queue = PersistentJobQueue(
        str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=3600, max_queued_jobs=1
    )
    current_iso = datetime.now(UTC).isoformat()
    with closing(queue._connect()) as conn:
        conn.execute(
            "INSERT INTO jobs (job_id, status, created_at) VALUES (?, ?, ?)",
            ("queued-job", "queued", current_iso),
        )
        conn.commit()

    with pytest.raises(RuntimeError, match="Job queue is full"):
        queue.submit(lambda: {"ok": True}, start_immediately=False)


def test_persistent_job_queue_retries_webhook(tmp_path: Path, monkeypatch):
    """Test that webhook retries work correctly.

    Note: This test uses a mock notifier that implements retry logic internally,
    similar to HTTPWebhookNotifier.
    """
    calls = {"count": 0}

    class RetryNotifier:
        def __init__(self):
            self.max_retries = 3
            self.retry_delay_seconds = 0.0

        def notify(self, webhook_url, payload, *, validated_ip=None):
            # Simulate retry logic like HTTPWebhookNotifier
            last_error = None
            for attempt in range(self.max_retries):
                calls["count"] += 1
                if calls["count"] >= 3:
                    return  # Success on third attempt
                # Simulate failure
                last_error = RuntimeError("temporary failure")
                if attempt < self.max_retries - 1:
                    continue  # Retry
            # All retries failed
            raise RuntimeError(
                f"Webhook delivery failed after {self.max_retries} attempts for {webhook_url}"
            ) from last_error

    queue = PersistentJobQueue(
        str(tmp_path / "jobs.sqlite3"),
        worker_count=1,
        ttl_seconds=3600,
        notifier=RetryNotifier(),
        allow_local_webhooks=True,  # Allow example.com for DNS validation
    )

    job = queue.submit(
        lambda: {"ok": True}, webhook_url="https://example.com/hook", start_immediately=True
    )
    stored = queue.get(job.job_id)

    assert stored is not None
    assert stored.status == "completed"
    # The notifier simulates retry internally, called 3 times then succeeds
    assert calls["count"] == 3


def test_persistent_job_queue_preserves_result_when_webhook_fails(tmp_path: Path):
    """Test that result is preserved when webhook fails (bug #6 fix).

    Previously, the result was lost when webhook failed. Now it's preserved
    so callers can still retrieve the computed result.
    """

    class FailingNotifier:
        def notify(self, webhook_url, payload, *, validated_ip=None):
            raise RuntimeError("downstream unavailable")

    queue = PersistentJobQueue(
        str(tmp_path / "jobs.sqlite3"),
        worker_count=1,
        ttl_seconds=3600,
        notifier=FailingNotifier(),
        allow_local_webhooks=True,
    )

    with pytest.raises(RuntimeError, match="downstream unavailable"):
        queue.submit(
            lambda: {"ok": True, "count": 1},
            webhook_url="https://example.com/hook",
            start_immediately=True,
        )

    stored = queue.get(next(iter(_job_ids(queue))))
    assert stored is not None
    assert stored.status == "failed"
    # Result is now preserved even though webhook failed
    assert stored.result == {"ok": True, "count": 1}
    assert "Webhook delivery failed" in stored.error


def test_persistent_job_queue_submit_limit_is_atomic(tmp_path: Path):
    queue = PersistentJobQueue(
        str(tmp_path / "jobs.sqlite3"),
        worker_count=1,
        ttl_seconds=3600,
        max_queued_jobs=1,
    )
    queue._ensure_workers = lambda: None  # keep queued jobs pending for deterministic counting

    barrier = threading.Barrier(2)
    errors: list[str] = []
    accepted: list[str] = []

    def submit_job() -> None:
        barrier.wait()
        try:
            job = queue.submit(lambda: {"ok": True}, start_immediately=False)
            accepted.append(job.job_id)
        except RuntimeError as exc:
            errors.append(str(exc))

    first = threading.Thread(target=submit_job)
    second = threading.Thread(target=submit_job)
    first.start()
    second.start()
    first.join()
    second.join()

    assert len(accepted) == 1
    assert errors == ["Job queue is full"]
    assert queue.pending_count() == 1


def test_worker_loop_preserves_result_and_webhook_failure_message(tmp_path: Path):
    """Test that result is preserved and error message is specific when webhook fails."""

    class FailingNotifier:
        def notify(self, webhook_url, payload, *, validated_ip=None):
            raise RuntimeError("downstream unavailable")

    queue = PersistentJobQueue(
        str(tmp_path / "jobs.sqlite3"),
        worker_count=1,
        ttl_seconds=3600,
        notifier=FailingNotifier(),
        allow_local_webhooks=True,
    )

    job = queue.submit(
        lambda: {"ok": True, "count": 1},
        webhook_url="https://example.com/hook",
        start_immediately=False,
    )

    deadline = time.time() + 5.0
    stored = queue.get(job.job_id)
    while stored is not None and stored.status in {"queued", "running"} and time.time() < deadline:
        time.sleep(0.05)
        stored = queue.get(job.job_id)

    assert stored is not None
    assert stored.status == "failed"
    # Result is now preserved even though webhook failed
    assert stored.result == {"ok": True, "count": 1}
    assert stored.error == "Webhook delivery failed: downstream unavailable"


@pytest.mark.skipif(os.name != "posix", reason="hard timeout uses POSIX fork support")
def test_execute_with_timeout_does_not_leave_background_thread_running(tmp_path: Path):
    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=3600)
    before = len(threading.enumerate())

    with pytest.raises(RuntimeError, match="timed out"):
        queue._execute_with_timeout(lambda: (time.sleep(0.5), {"ok": True})[1], 0.05)

    time.sleep(0.1)
    after = len(threading.enumerate())

    assert after == before


def test_validate_webhook_url_resolves_dns_on_submit(tmp_path: Path, monkeypatch):
    """Submit-time DNS resolution is now enabled for SSRF defense-in-depth."""
    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=3600)

    resolved = False

    def mock_getaddrinfo(*args, **kwargs):
        nonlocal resolved
        resolved = True
        import socket as _socket
        return [(_socket.AF_INET, _socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr("socket.getaddrinfo", mock_getaddrinfo)

    queue.submit(
        lambda: {"ok": True},
        webhook_url="https://hooks.example.test/notify",
        start_immediately=False,
    )

    assert resolved


def test_validate_webhook_url_blocks_private_ipv6_literals(tmp_path: Path):
    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=3600)

    with pytest.raises(ValueError, match="private IP address"):
        queue.submit(
            lambda: {"ok": True},
            webhook_url="https://[fc00::1]/notify",
            start_immediately=False,
        )


def test_validate_webhook_url_requires_hostname():
    for url in ("https://:443/hook", "https://user:pass@/hook", "https:///hook"):
        with pytest.raises(ValueError, match="hostname"):
            _validate_webhook_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com:0/hook",
        "https://example.com:65536/hook",
        "https://example.com:abc/hook",
        "https://[2001:db8::1]:0/hook",
        "https://[2001:db8::1]:65536/hook",
        "https://[2001:db8::1]:abc/hook",
    ],
)
def test_validate_webhook_url_rejects_invalid_ports(url: str):
    with pytest.raises(ValueError, match=r"(?i)port"):
        _validate_webhook_url(url)


def test_validate_webhook_url_blocks_crlf_and_null_bytes():
    for url in ("https://example.com\r\nX-Test: 1", "https://example.com\x00payload"):
        with pytest.raises(ValueError, match="blocked"):
            _validate_webhook_url(url)


def test_validate_webhook_url_blocks_trailing_dot_hostnames():
    for url in (
        "https://localhost./hook",
        "https://metadata.google.internal./hook",
        "https://metadata.azure.net./hook",
        "https://metadata.oracle.internal./hook",
    ):
        with pytest.raises(ValueError, match="not allowed"):
            _validate_webhook_url(url)


def test_execute_job_keeps_persistence_errors_visible(tmp_path: Path, monkeypatch):
    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=3600)
    job = SimpleNamespace(
        job_id="job-1",
        webhook_url="https:///hook",
        status="completed",
        result={"ok": True},
        error=None,
        completed_at="2026-04-08T00:00:00+00:00",
    )

    monkeypatch.setattr(queue, "_assert_queue_usable", lambda **kwargs: None)
    monkeypatch.setattr(queue, "_mark_running", lambda job_id: None)
    monkeypatch.setattr(queue, "_mark_completed", lambda job_id, result: None)
    monkeypatch.setattr(queue, "get", lambda job_id, cleanup_expired=False: job)
    monkeypatch.setattr(queue, "_still_owns_lease", lambda: True)

    calls: list[str] = []

    def fail_mark_webhook(job_id, error):
        calls.append(error)
        raise RuntimeError("db failure")

    monkeypatch.setattr(queue, "_mark_webhook_failed", fail_mark_webhook)

    with pytest.raises(RuntimeError, match="db failure"):
        queue._execute_job("job-1", lambda: {"ok": True})

    assert calls == ["Webhook URL has no hostname"]


def test_resolve_and_validate_ip_rejects_mixed_public_private_answers(monkeypatch):
    def fake_getaddrinfo(hostname, port):
        return [
            (0, 0, 0, "", ("93.184.216.34", 0)),
            (0, 0, 0, "", ("127.0.0.1", 0)),
        ]

    monkeypatch.setattr("socket.getaddrinfo", fake_getaddrinfo)

    assert _resolve_and_validate_ip("mixed.example.test") is None


def test_api_batch_jobs_submit_uses_async_queue_path(monkeypatch):
    captured = {}

    async def fake_handle_batch_submit(request, ingest_many_func, job_queue, job_ttl_seconds):
        captured["called"] = True
        captured["ttl"] = job_ttl_seconds
        return {
            "job_id": "job-1",
            "status": "queued",
            "created_at": "2026-03-29T00:00:00+00:00",
            "poll_url": "/api/v1/jobs/job-1",
            "expires_in_seconds": job_ttl_seconds,
            "ttl_applies_to": "completed_jobs",
        }

    class ReusedQueue:
        state = "open"
        ttl_seconds = 123

    monkeypatch.setattr("markdown_ingress.api_server.handle_batch_submit", fake_handle_batch_submit)
    monkeypatch.setattr("markdown_ingress.api_server.JOB_QUEUE", ReusedQueue())
    from markdown_ingress.api_server import app

    client = TestClient(app)
    response = client.post(
        "/api/v1/jobs/batch", json={"urls": ["https://example.com"], "mode": "fast"}
    )

    assert response.status_code == 200
    assert captured["called"] is True
    assert captured["ttl"] == 123


def test_api_batch_jobs_submit_returns_500_for_internal_runtime_error(monkeypatch):
    async def fake_handle_batch_submit(request, ingest_many_func, job_queue, job_ttl_seconds):
        raise Exception("should not be called")

    class FailingQueue:
        def submit(self, *args, **kwargs):
            raise RuntimeError("sqlite write failed")

    monkeypatch.setattr("markdown_ingress.api_server.JOB_QUEUE", FailingQueue())
    from markdown_ingress.api_server import app

    client = TestClient(app)
    response = client.post(
        "/api/v1/jobs/batch", json={"urls": ["https://example.com"], "mode": "fast"}
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"


def test_persistent_job_queue_close_waits_for_inline_start_immediately_job(tmp_path: Path):
    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=3600)
    started = threading.Event()
    release = threading.Event()

    def inline_task():
        started.set()
        release.wait(timeout=5.0)
        return {"ok": True}

    submit_thread = threading.Thread(
        target=lambda: queue.submit(inline_task, start_immediately=True),
    )
    submit_thread.start()

    assert started.wait(timeout=5.0) is True

    close_thread = threading.Thread(target=queue.close)
    close_thread.start()
    time.sleep(0.1)
    assert close_thread.is_alive() is True
    assert queue.state == "closing"

    release.set()
    submit_thread.join(timeout=5.0)
    close_thread.join(timeout=5.0)

    assert submit_thread.is_alive() is False
    assert close_thread.is_alive() is False
    assert queue.state == "closed"


def test_persistent_job_queue_repair_probe_does_not_flip_state_with_inline_job(tmp_path: Path):
    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=3600)
    release = threading.Event()

    submit_thread = threading.Thread(
        target=lambda: queue.submit(
            lambda: (release.wait(), {"ok": True})[1], start_immediately=True
        ),
    )
    submit_thread.start()

    deadline = time.time() + 5.0
    while queue._inline_jobs_running == 0 and time.time() < deadline:
        time.sleep(0.01)

    with pytest.raises(RuntimeError, match="inline jobs did not stop"):
        queue.close(inline_wait_timeout=0.0, preserve_state_on_inline_timeout=True)

    assert queue.state == "open"
    assert queue._worker_stop_requested is False

    release.set()
    submit_thread.join(timeout=5.0)
    queue.close()


def test_persistent_job_queue_repair_probe_does_not_flip_state_with_live_workers(tmp_path: Path):
    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=3600)

    class LiveWorker:
        def is_alive(self):
            return True

        def join(self, timeout=None):
            return None

    queue._workers = [LiveWorker()]
    queue._workers_started = True

    with pytest.raises(RuntimeError, match="workers did not stop"):
        queue.close(inline_wait_timeout=0.0, preserve_state_on_inline_timeout=True)

    assert queue.state == "open"
    assert queue._worker_stop_requested is False


def test_persistent_job_queue_expires_legacy_rows_with_unknown_ttl_via_compatibility_ttl(
    tmp_path: Path,
):
    db_path = tmp_path / "jobs.sqlite3"
    first_queue = PersistentJobQueue(str(db_path), worker_count=1, ttl_seconds=120)
    completed_at = datetime.now(UTC).replace(microsecond=0)

    with closing(first_queue._connect()) as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                job_id, status, created_at, completed_at, result_json, error, webhook_url, ttl_seconds
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-job",
                "completed",
                (completed_at - timedelta(seconds=60)).isoformat(),
                completed_at.isoformat(),
                json.dumps({"ok": True}),
                None,
                None,
                None,
            ),
        )
        conn.commit()

    first_queue.close()
    reopened = PersistentJobQueue(str(db_path), worker_count=1, ttl_seconds=300)
    stored = reopened.get("legacy-job", cleanup_expired=False)

    assert stored is not None
    assert stored.ttl_seconds is None
    assert stored.legacy_expires_at is not None
    assert (
        stored.legacy_expires_at
        == (completed_at + timedelta(seconds=LEGACY_UNKNOWN_TTL_SECONDS)).isoformat()
    )
    reopened.cleanup_expired()
    assert reopened.get("legacy-job", cleanup_expired=False) is not None
    with closing(reopened._connect()) as conn:
        conn.execute(
            "UPDATE jobs SET legacy_expires_at = ? WHERE job_id = ?",
            ("2026-03-30T00:00:10+00:00", "legacy-job"),
        )
        conn.commit()
    reopened.cleanup_expired()
    assert reopened.get("legacy-job", cleanup_expired=False) is None
    reopened.close()


def test_cleanup_expired_drops_legacy_rows_with_invalid_legacy_expires_at(tmp_path: Path):
    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=120)
    with closing(queue._connect()) as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                job_id, status, created_at, completed_at, result_json, error, webhook_url, ttl_seconds, legacy_expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-invalid",
                "completed",
                "2026-03-30T00:00:00+00:00",
                "2026-03-30T00:01:00+00:00",
                json.dumps({"ok": True}),
                None,
                None,
                None,
                "not-a-date",
            ),
        )
        conn.commit()

    queue.cleanup_expired()

    assert queue.get("legacy-invalid", cleanup_expired=False) is None
    queue.close()


def test_cleanup_expired_sets_missing_legacy_expiry_from_completed_at_for_recent_rows(
    tmp_path: Path,
):
    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=120)
    completed_at = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=30)
    with closing(queue._connect()) as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                job_id, status, created_at, completed_at, result_json, error, webhook_url, ttl_seconds, legacy_expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-missing-expiry-recent",
                "completed",
                (completed_at - timedelta(seconds=30)).isoformat(),
                completed_at.isoformat(),
                json.dumps({"ok": True}),
                None,
                None,
                None,
                None,
            ),
        )
        conn.commit()

    queue.cleanup_expired()

    stored = queue.get("legacy-missing-expiry-recent", cleanup_expired=False)
    assert stored is not None
    assert stored.ttl_seconds == LEGACY_UNKNOWN_TTL_SECONDS
    assert (
        stored.legacy_expires_at
        == (completed_at + timedelta(seconds=LEGACY_UNKNOWN_TTL_SECONDS)).isoformat()
    )
    queue.close()


def test_cleanup_expired_drops_legacy_rows_missing_legacy_expires_at_when_already_expired(
    tmp_path: Path,
):
    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=120)
    completed_at = datetime.now(UTC).replace(microsecond=0) - timedelta(
        seconds=LEGACY_UNKNOWN_TTL_SECONDS + 60
    )
    with closing(queue._connect()) as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                job_id, status, created_at, completed_at, result_json, error, webhook_url, ttl_seconds, legacy_expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-expired-without-explicit-expiry",
                "completed",
                (completed_at - timedelta(seconds=30)).isoformat(),
                completed_at.isoformat(),
                json.dumps({"ok": True}),
                None,
                None,
                None,
                None,
            ),
        )
        conn.commit()

    queue.cleanup_expired()

    assert queue.get("legacy-expired-without-explicit-expiry", cleanup_expired=False) is None
    queue.close()


def test_cleanup_expired_drops_legacy_rows_missing_legacy_expires_at_with_invalid_completed_at(
    tmp_path: Path,
):
    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=120)
    with closing(queue._connect()) as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                job_id, status, created_at, completed_at, result_json, error, webhook_url, ttl_seconds, legacy_expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-missing-expiry-invalid-completed",
                "completed",
                "2026-03-30T00:00:00+00:00",
                "not-a-date",
                json.dumps({"ok": True}),
                None,
                None,
                None,
                None,
            ),
        )
        conn.commit()

    queue.cleanup_expired()

    assert queue.get("legacy-missing-expiry-invalid-completed", cleanup_expired=False) is None
    queue.close()


def test_cleanup_expired_sets_missing_legacy_expiry_from_completed_at(tmp_path: Path):
    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=120)
    completed_at = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=30)
    with closing(queue._connect()) as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                job_id, status, created_at, completed_at, result_json, error, webhook_url, ttl_seconds, legacy_expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-missing-expiry",
                "completed",
                (completed_at - timedelta(seconds=30)).isoformat(),
                completed_at.isoformat(),
                json.dumps({"ok": True}),
                None,
                None,
                None,
                None,
            ),
        )
        conn.commit()

    queue.cleanup_expired()

    stored = queue.get("legacy-missing-expiry", cleanup_expired=False)
    assert stored is not None
    assert stored.ttl_seconds == LEGACY_UNKNOWN_TTL_SECONDS
    assert (
        stored.legacy_expires_at
        == (completed_at + timedelta(seconds=LEGACY_UNKNOWN_TTL_SECONDS)).isoformat()
    )
    queue.close()


def test_migration_keeps_legacy_expiry_null_when_completed_at_is_invalid(tmp_path: Path):
    db_path = tmp_path / "jobs.sqlite3"
    first_queue = PersistentJobQueue(str(db_path), worker_count=1, ttl_seconds=300)

    with closing(first_queue._connect()) as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                job_id, status, created_at, completed_at, result_json, error, webhook_url, ttl_seconds, legacy_expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-invalid-completed-at",
                "completed",
                "2026-03-30T00:00:00+00:00",
                "not-a-date",
                json.dumps({"ok": True}),
                None,
                None,
                None,
                None,
            ),
        )
        conn.commit()

    first_queue.close()
    reopened = PersistentJobQueue(str(db_path), worker_count=1, ttl_seconds=300)
    stored = reopened.get("legacy-invalid-completed-at", cleanup_expired=False)

    assert stored is not None
    assert stored.ttl_seconds is None
    assert stored.legacy_expires_at is None
    reopened.close()


def test_migration_normalizes_naive_completed_at_to_utc_for_legacy_expiry(tmp_path: Path):
    db_path = tmp_path / "jobs.sqlite3"
    first_queue = PersistentJobQueue(str(db_path), worker_count=1, ttl_seconds=300)

    with closing(first_queue._connect()) as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                job_id, status, created_at, completed_at, result_json, error, webhook_url, ttl_seconds, legacy_expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-naive-completed-at",
                "completed",
                "2026-03-30T00:00:00",
                "2026-03-30T01:00:00",
                json.dumps({"ok": True}),
                None,
                None,
                None,
                None,
            ),
        )
        conn.commit()

    first_queue.close()
    reopened = PersistentJobQueue(str(db_path), worker_count=1, ttl_seconds=300)
    stored = reopened.get("legacy-naive-completed-at", cleanup_expired=False)

    assert stored is not None
    assert stored.ttl_seconds is None
    assert stored.legacy_expires_at == "2026-03-30T02:00:00+00:00"
    reopened.close()


def test_cleanup_expired_drops_rows_with_invalid_completed_at_and_persisted_ttl(tmp_path: Path):
    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=120)
    with closing(queue._connect()) as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                job_id, status, created_at, completed_at, result_json, error, webhook_url, ttl_seconds, legacy_expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "persisted-ttl-invalid-completed-at",
                "completed",
                "2026-03-30T00:00:00+00:00",
                "not-a-date",
                json.dumps({"ok": True}),
                None,
                None,
                120,
                None,
            ),
        )
        conn.commit()

    queue.cleanup_expired()

    assert queue.get("persisted-ttl-invalid-completed-at", cleanup_expired=False) is None
    queue.close()


def test_cleanup_expired_drops_rows_with_missing_completed_at_and_persisted_ttl(
    tmp_path: Path,
):
    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=120)
    with closing(queue._connect()) as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                job_id, status, created_at, completed_at, result_json, error, webhook_url, ttl_seconds, legacy_expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "persisted-ttl-missing-completed-at",
                "completed",
                "2026-03-30T00:00:00+00:00",
                None,
                json.dumps({"ok": True}),
                None,
                None,
                120,
                None,
            ),
        )
        conn.commit()

    queue.cleanup_expired()

    assert queue.get("persisted-ttl-missing-completed-at", cleanup_expired=False) is None
    queue.close()


def test_cleanup_expired_drops_legacy_rows_with_missing_completed_at_and_expired_legacy_expiry(
    tmp_path: Path,
):
    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=120)
    with closing(queue._connect()) as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                job_id, status, created_at, completed_at, result_json, error, webhook_url, ttl_seconds, legacy_expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-missing-completed-at-expired",
                "completed",
                "2026-03-30T00:00:00+00:00",
                None,
                json.dumps({"ok": True}),
                None,
                None,
                None,
                "2026-03-30T00:00:00+00:00",
            ),
        )
        conn.commit()

    queue.cleanup_expired()

    assert queue.get("legacy-missing-completed-at-expired", cleanup_expired=False) is None
    queue.close()


def test_cleanup_expired_preserves_legacy_rows_with_missing_completed_at_and_future_expiry(
    tmp_path: Path,
):
    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=120)
    with closing(queue._connect()) as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                job_id, status, created_at, completed_at, result_json, error, webhook_url, ttl_seconds, legacy_expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-missing-completed-at-future-expiry",
                "completed",
                "2026-03-30T00:00:00+00:00",
                None,
                json.dumps({"ok": True}),
                None,
                None,
                None,
                "2999-01-01 00:00:00",
            ),
        )
        conn.commit()

    queue.cleanup_expired()

    stored = queue.get("legacy-missing-completed-at-future-expiry", cleanup_expired=False)
    assert stored is not None
    assert stored.legacy_expires_at == "2999-01-01 00:00:00"
    queue.close()


def test_cleanup_expired_preserves_space_separated_completed_at_until_ttl_expires(tmp_path: Path):
    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=120)
    now = datetime.now(UTC).replace(microsecond=0)
    created_at = (now - timedelta(seconds=40)).isoformat(sep=" ")
    completed_at = (now - timedelta(seconds=30)).isoformat(sep=" ")

    with closing(queue._connect()) as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                job_id, status, created_at, completed_at, result_json, error, webhook_url, ttl_seconds, legacy_expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "space-ttl-job",
                "completed",
                created_at,
                completed_at,
                json.dumps({"ok": True}),
                None,
                None,
                120,
                None,
            ),
        )
        conn.commit()

    queue.cleanup_expired()

    stored = queue.get("space-ttl-job", cleanup_expired=False)
    assert stored is not None
    assert stored.completed_at == completed_at
    queue.close()


def test_cleanup_expired_preserves_space_separated_legacy_expiry_until_expiry(tmp_path: Path):
    queue = PersistentJobQueue(str(tmp_path / "jobs.sqlite3"), worker_count=1, ttl_seconds=120)
    now = datetime.now(UTC).replace(microsecond=0)
    created_at = (now - timedelta(seconds=40)).isoformat(sep=" ")
    completed_at = (now - timedelta(seconds=30)).isoformat(sep=" ")
    legacy_expires_at = (now + timedelta(seconds=60)).isoformat(sep=" ")

    with closing(queue._connect()) as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                job_id, status, created_at, completed_at, result_json, error, webhook_url, ttl_seconds, legacy_expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "space-legacy-job",
                "completed",
                created_at,
                completed_at,
                json.dumps({"ok": True}),
                None,
                None,
                None,
                legacy_expires_at,
            ),
        )
        conn.commit()

    queue.cleanup_expired()

    stored = queue.get("space-legacy-job", cleanup_expired=False)
    assert stored is not None
    assert stored.legacy_expires_at == legacy_expires_at
    queue.close()


def _job_ids(queue: PersistentJobQueue) -> list[str]:
    with closing(queue._connect()) as conn:
        rows = conn.execute("SELECT job_id FROM jobs ORDER BY created_at").fetchall()
    return [row["job_id"] for row in rows]
