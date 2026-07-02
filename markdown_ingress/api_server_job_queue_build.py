"""Persistent job queue construction for the API server."""

from __future__ import annotations

from collections.abc import Callable


def build_persistent_job_queue[T](
    *,
    queue_class: Callable[..., T],
    db_path: str,
    worker_count: int,
    ttl_seconds: int,
    max_queued_jobs: int,
    webhook_max_retries: int,
    webhook_retry_delay_seconds: float,
    allow_local_webhooks: bool,
    job_timeout_seconds: float | None,
) -> T:
    return queue_class(
        db_path=db_path,
        worker_count=worker_count,
        ttl_seconds=ttl_seconds,
        max_queued_jobs=max_queued_jobs,
        webhook_max_retries=webhook_max_retries,
        webhook_retry_delay_seconds=webhook_retry_delay_seconds,
        allow_local_webhooks=allow_local_webhooks,
        job_timeout_seconds=job_timeout_seconds,
    )
