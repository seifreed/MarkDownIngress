"""Process-level ingestion telemetry shared across application and core layers."""

from __future__ import annotations

import copy
import threading
from typing import Any

_INGEST_STATS_LOCK = threading.Lock()
_MODE_NAMES = ("fast", "render", "auto")


def _blank_ingest_stats() -> dict[str, Any]:
    """Create a fresh process-level stats snapshot."""
    return {
        "requests_total": 0,
        "cache_hits": 0,
        "cache_misses": 0,
        "inflight_followers": 0,
        "leader_executions": 0,
        "mode_counts": {mode: 0 for mode in _MODE_NAMES},
        "mode_timings_ms": {
            mode: {"count": 0, "total": 0.0, "avg": 0.0}
            for mode in _MODE_NAMES
        },
        "mode_results": {
            mode: {"success": 0, "error": 0}
            for mode in _MODE_NAMES
        },
        "stage_timings_ms": {},
        "policy_actions": {"allow": 0, "warn": 0, "block": 0},
        "render_fallbacks": 0,
        "circuit_breaker_rejections": 0,
    }


_INGEST_STATS = _blank_ingest_stats()


def snapshot_ingest_stats() -> dict[str, Any]:
    """Return aggregate in-process ingestion stats."""
    with _INGEST_STATS_LOCK:
        return copy.deepcopy(_INGEST_STATS)


def reset_ingest_stats() -> None:
    """Reset aggregate in-process ingestion stats."""
    with _INGEST_STATS_LOCK:
        _INGEST_STATS.clear()
        _INGEST_STATS.update(_blank_ingest_stats())


def bump_ingest_stat(key: str, amount: int = 1) -> None:
    """Increment a process-level ingestion stat."""
    with _INGEST_STATS_LOCK:
        current = _INGEST_STATS[key]
        if isinstance(current, int):
            _INGEST_STATS[key] = current + amount


def record_mode_request(mode: str) -> None:
    """Track one request against the requested mode."""
    with _INGEST_STATS_LOCK:
        mode_counts = _INGEST_STATS["mode_counts"]
        if isinstance(mode_counts, dict) and mode in mode_counts:
            current = mode_counts[mode]
            if isinstance(current, int):
                mode_counts[mode] = current + 1


def record_mode_timing(mode: str, duration_ms: float) -> None:
    """Track leader execution duration against the requested mode."""
    with _INGEST_STATS_LOCK:
        mode_timings = _INGEST_STATS["mode_timings_ms"]
        if not isinstance(mode_timings, dict) or mode not in mode_timings:
            return
        bucket = mode_timings[mode]
        if not isinstance(bucket, dict):
            return
        count = bucket.get("count", 0)
        total = bucket.get("total", 0.0)
        if not isinstance(count, int) or not isinstance(total, (int, float)):
            return
        new_count = count + 1
        new_total = float(total) + duration_ms
        bucket["count"] = new_count
        bucket["total"] = new_total
        bucket["avg"] = new_total / new_count


def record_mode_result(mode: str, success: bool) -> None:
    """Track request outcome against the requested mode."""
    with _INGEST_STATS_LOCK:
        mode_results = _INGEST_STATS["mode_results"]
        if not isinstance(mode_results, dict) or mode not in mode_results:
            return
        bucket = mode_results[mode]
        if not isinstance(bucket, dict):
            return
        key = "success" if success else "error"
        current = bucket.get(key, 0)
        if isinstance(current, int):
            bucket[key] = current + 1


def record_stage_timing(stage: str, duration_ms: float) -> None:
    """Track aggregate timings for pipeline stages."""
    with _INGEST_STATS_LOCK:
        timings = _INGEST_STATS.setdefault("stage_timings_ms", {})
        bucket = timings.setdefault(stage, {"count": 0, "total": 0.0, "avg": 0.0})
        count = int(bucket.get("count", 0)) + 1
        total = float(bucket.get("total", 0.0)) + duration_ms
        bucket["count"] = count
        bucket["total"] = total
        bucket["avg"] = total / count


def record_policy_action(action: str) -> None:
    """Track emitted policy decisions."""
    with _INGEST_STATS_LOCK:
        policy_actions = _INGEST_STATS.get("policy_actions", {})
        if isinstance(policy_actions, dict) and action in policy_actions:
            current = policy_actions[action]
            if isinstance(current, int):
                policy_actions[action] = current + 1
