"""Response payload builders for API server endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from markdown_ingress.api_server_snapshot import JobSubsystemSnapshot

SERVICE_NAME = "MarkDownIngress API"


def build_stats_payload(
    *,
    api_version: str,
    ingest_stats: dict[str, Any],
    snapshot: JobSubsystemSnapshot,
) -> dict[str, Any]:
    jobs_payload: dict[str, Any] = {
        "pending": snapshot["pending_visible_total"],
        "ttl_seconds": snapshot["current_ttl_seconds"],
        "ttl_applies_to": "completed_jobs_with_persisted_ttl_or_legacy_compatibility_ttl",
        "max_queued_jobs": snapshot["current_max_queued_jobs"],
    }
    payload: dict[str, Any] = {
        "version": api_version,
        "stats": ingest_stats,
        "job_queue": jobs_payload,
    }
    jobs_payload["current_state"] = snapshot["current_state"]
    jobs_payload["current_db_name"] = Path(snapshot["current_db_path"]).name
    jobs_payload["current_pending"] = snapshot["current_pending"]
    jobs_payload["legacy_pending"] = snapshot["legacy_pending"]
    jobs_payload["pending_visible_total"] = snapshot["pending_visible_total"]
    jobs_payload["legacy_visible_queues"] = snapshot["legacy_visible_queues"]
    jobs_payload["legacy_db_count"] = len(snapshot["legacy_db_paths"])
    jobs_payload["repair_in_progress"] = snapshot["repair_in_progress"]
    jobs_payload["current_unknown_ttl_jobs"] = snapshot["current_unknown_ttl_jobs"]
    jobs_payload["legacy_unknown_ttl_jobs"] = snapshot["legacy_unknown_ttl_jobs"]
    jobs_payload["legacy_unknown_ttl_seconds"] = snapshot["legacy_unknown_ttl_seconds"]
    jobs_payload["unknown_ttl_jobs_total"] = (
        snapshot["current_unknown_ttl_jobs"] + snapshot["legacy_unknown_ttl_jobs"]
    )
    jobs_payload["pending"] = snapshot["pending_visible_total"]
    return payload


def build_health_payload(*, api_version: str, snapshot: JobSubsystemSnapshot) -> dict[str, str]:
    return {
        "status": snapshot["status"],
        "version": api_version,
        "service": SERVICE_NAME,
    }


def build_detailed_health_payload(
    *, api_version: str, snapshot: JobSubsystemSnapshot
) -> dict[str, Any]:
    return {
        "status": snapshot["status"],
        "version": api_version,
        "service": SERVICE_NAME,
        "job_queue": {
            "state": snapshot["current_state"],
            "current_db_path": snapshot["current_db_path"],
            "current_pending": snapshot["current_pending"],
            "legacy_pending": snapshot["legacy_pending"],
            "pending_visible_total": snapshot["pending_visible_total"],
            "legacy_visible_queues": snapshot["legacy_visible_queues"],
            "current_unknown_ttl_jobs": snapshot["current_unknown_ttl_jobs"],
            "legacy_unknown_ttl_jobs": snapshot["legacy_unknown_ttl_jobs"],
            "legacy_unknown_ttl_seconds": snapshot["legacy_unknown_ttl_seconds"],
            "repair_in_progress": snapshot["repair_in_progress"],
        },
    }


def build_root_payload(*, api_version: str) -> dict[str, str]:
    return {"name": "markdown-ingress", "version": api_version, "docs": "/docs"}
