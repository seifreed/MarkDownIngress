"""In-flight request deduplication helpers for ingestion."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import threading
import time
import weakref
from collections import OrderedDict
from dataclasses import dataclass, field

from markdown_ingress.config_models import DomainPolicy, IngestConfig
from markdown_ingress.models import SafeDocument

logger = logging.getLogger(__name__)

# Maximum number of in-flight requests before LRU eviction kicks in.
_INFLIGHT_MAX_SIZE = 1000

# TTL (time-to-live) for in-flight entries in seconds.
_INFLIGHT_TTL_SECONDS = 900  # 15 minutes

_INFLIGHT_WAIT_TIMEOUT = 600  # seconds — prevents infinite hang if leader crashes

_ALL_INFLIGHT_REGISTRIES = weakref.WeakSet()
_ALL_INFLIGHT_REGISTRIES_LOCK = threading.Lock()


@dataclass
class InFlightEntry:
    """Track an in-progress ingestion so duplicate callers can await the same result."""

    condition: threading.Condition = field(default_factory=threading.Condition)
    followers: int = 0
    done: bool = False
    completing: bool = False
    leader_active: bool = True
    document: SafeDocument | None = None
    error: Exception | None = None
    created_at: float = field(default_factory=time.monotonic)
    request_key: str = ""


class InFlightRegistry:
    """Isolated in-flight registry owned by one runtime/orchestrator."""

    def __init__(self) -> None:
        self._requests: OrderedDict[str, InFlightEntry] = OrderedDict()
        self._lock = threading.Lock()
        with _ALL_INFLIGHT_REGISTRIES_LOCK:
            _ALL_INFLIGHT_REGISTRIES.add(self)

    def _cleanup_orphaned_entries_locked(self) -> int:
        now = time.monotonic()
        keys_to_remove = []

        for key, entry in self._requests.items():
            age = now - entry.created_at
            if age > _INFLIGHT_TTL_SECONDS:
                keys_to_remove.append(key)

        for key in keys_to_remove:
            entry = self._requests.pop(key, None)
            if entry is not None:
                with entry.condition:
                    entry.leader_active = False
                    entry.condition.notify_all()
                logger.warning(
                    "Cleaned up orphaned in-flight entry (key=%s, age=%.1fs, followers=%d)",
                    key[:16] + "...",
                    now - entry.created_at,
                    entry.followers,
                )

        return len(keys_to_remove)

    def _evict_lru_entries_locked(self) -> int:
        evicted = 0
        while len(self._requests) >= _INFLIGHT_MAX_SIZE:
            evictable_key = None
            evictable_entry = None
            for key, entry in self._requests.items():
                if entry.done or not entry.leader_active:
                    evictable_key = key
                    evictable_entry = entry
                    break

            if evictable_key is None or evictable_entry is None:
                break

            self._requests.pop(evictable_key, None)
            key, entry = evictable_key, evictable_entry
            evicted += 1
            with entry.condition:
                entry.leader_active = False
                entry.condition.notify_all()
            logger.warning(
                "Evicted in-flight entry due to max size (key=%s, followers=%d, age=%.1fs)",
                key[:16] + "...",
                entry.followers,
                time.monotonic() - entry.created_at,
            )
        return evicted

    def active_count(self) -> int:
        with self._lock:
            self._cleanup_orphaned_entries_locked()
            return len(self._requests)

    def acquire(self, request_key: str) -> InFlightEntry | None:
        with self._lock:
            self._cleanup_orphaned_entries_locked()
            existing = self._requests.get(request_key)
            if existing is not None:
                existing.followers += 1
                self._requests.move_to_end(request_key)
                return existing

            self._evict_lru_entries_locked()
            if len(self._requests) >= _INFLIGHT_MAX_SIZE:
                logger.warning(
                    "In-flight registry saturated; skipping dedup registration for key=%s",
                    request_key[:16] + "...",
                )
                return None
            self._requests[request_key] = InFlightEntry(request_key=request_key)
            return None

    def await_result(self, entry: InFlightEntry, request_key: str) -> SafeDocument:
        with entry.condition:
            while not entry.done:
                if not entry.leader_active:
                    raise RuntimeError("In-flight ingestion failed - leader was marked inactive")

                if not entry.condition.wait(timeout=_INFLIGHT_WAIT_TIMEOUT):
                    entry.condition.release()
                    try:
                        with self._lock:
                            current_entry = self._requests.get(request_key)
                            if current_entry is not None and current_entry is entry:
                                entry.leader_active = False
                                logger.error(
                                    "Marked in-flight entry as inactive after timeout (key=%s, followers=%d)",
                                    request_key[:16] + "...",
                                    entry.followers,
                                )
                    finally:
                        entry.condition.acquire()

                    if entry.done:
                        break

                    raise RuntimeError(
                        "In-flight ingestion timed out waiting for leader — "
                        "the leader may have crashed without calling release_inflight()"
                    )

            if entry.error is not None:
                raise entry.error
            if entry.document is None:
                raise RuntimeError("In-flight ingestion finished without result")
            return copy.deepcopy(entry.document)

    def release(
        self,
        request_key: str,
        *,
        document: SafeDocument | None = None,
        error: Exception | None = None,
    ) -> int:
        with self._lock:
            self._cleanup_orphaned_entries_locked()
            entry = self._requests.get(request_key)

        if entry is None:
            return 0

        with entry.condition:
            shared_count = entry.followers
            entry.completing = True
            try:
                entry.document = copy.deepcopy(document) if document is not None else None
                if entry.document is not None:
                    entry.document.metadata["inflight_shared_count"] = shared_count
                if error is not None:
                    try:
                        entry.error = copy.deepcopy(error)
                    except Exception:
                        try:
                            entry.error = type(error)(str(error))
                        except Exception:
                            entry.error = RuntimeError(f"{type(error).__name__}: {error}")
            except Exception as exc:
                entry.error = exc
            finally:
                entry.done = True
                entry.condition.notify_all()

        with self._lock:
            current = self._requests.get(request_key)
            if current is entry:
                self._requests.pop(request_key, None)

        return shared_count


def inflight_active_count(registry: InFlightRegistry | None = None) -> int:
    """Return the current number of leader requests still in progress."""
    if registry is not None:
        return registry.active_count()
    with _ALL_INFLIGHT_REGISTRIES_LOCK:
        registries = list(_ALL_INFLIGHT_REGISTRIES)
    return sum(entry.active_count() for entry in registries)


def build_request_identity(
    url: str,
    config: IngestConfig,
    matched_domain_policy: DomainPolicy | None = None,
) -> dict[str, object]:
    """Build a stable identity payload for logically equivalent ingestions."""
    key_payload: dict[str, object] = {
        "url": url,
        "mode": config.mode,
        "strict": config.strict,
        "allow_local_urls": config.allow_local_urls,
        "model": config.model,
        "timeout": config.timeout,
        "auto_render_threshold": config.auto_render_threshold,
        "stealth": config.stealth,
        "disable_http2": config.disable_http2,
        "extreme_mode": config.extreme_mode,
        "screenshot": config.screenshot,
        "extract_metadata": config.extract_metadata,
        "extract_links": config.extract_links,
        "advanced_security": config.advanced_security,
        "use_llm": config.use_llm,
        "policy_name": config.policy_name,
        "custom_patterns": list(config.custom_patterns),
        "plugin_dirs": list(config.plugin_dirs),
        "output_profile": config.output_profile,
        "extract_blocks": config.extract_blocks,
        "chunking_strategy": config.chunking_strategy,
        "chunk_size": config.chunk_size,
        "chunk_overlap": config.chunk_overlap,
        "detect_language": config.detect_language,
        "normalize_multilingual": config.normalize_multilingual,
        "include_security_explanation": config.include_security_explanation,
        "include_observability": config.include_observability,
        "domain_request_interval": config.domain_request_interval,
        "circuit_breaker_threshold": config.circuit_breaker_threshold,
        "circuit_breaker_open_seconds": config.circuit_breaker_open_seconds,
        "render_cost_budget": config.render_cost_budget,
        "output_formats": list(config.output_formats),
    }
    if matched_domain_policy is not None:
        key_payload["matched_domain_policy"] = {
            "domain": matched_domain_policy.domain,
            "include_subdomains": matched_domain_policy.include_subdomains,
            "mode": matched_domain_policy.mode,
            "timeout": matched_domain_policy.timeout,
            "auto_render_threshold": matched_domain_policy.auto_render_threshold,
            "strict": matched_domain_policy.strict,
            "policy_name": matched_domain_policy.policy_name,
            "block_threshold": matched_domain_policy.block_threshold,
            "warn_threshold": matched_domain_policy.warn_threshold,
            "request_interval": matched_domain_policy.request_interval,
            "render_cost_budget": matched_domain_policy.render_cost_budget,
            "extract_metadata": matched_domain_policy.extract_metadata,
            "extract_links": matched_domain_policy.extract_links,
            "output_profile": matched_domain_policy.output_profile,
            "allowed_tags": list(matched_domain_policy.allowed_tags),
            "blocked_tags": list(matched_domain_policy.blocked_tags),
            "blocked_selectors": list(matched_domain_policy.blocked_selectors),
            "unwrap_selectors": list(matched_domain_policy.unwrap_selectors),
        }
    return key_payload


def make_request_key(
    url: str,
    config: IngestConfig,
    matched_domain_policy: DomainPolicy | None = None,
) -> str:
    """Build a stable deduplication key for logically equivalent ingestions."""
    key_payload = build_request_identity(url, config, matched_domain_policy)
    serialized = json.dumps(key_payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


_DEFAULT_INFLIGHT_REGISTRY = InFlightRegistry()


def acquire_inflight(
    request_key: str,
    *,
    registry: InFlightRegistry | None = None,
) -> InFlightEntry | None:
    return (registry or _DEFAULT_INFLIGHT_REGISTRY).acquire(request_key)


def await_inflight(
    entry: InFlightEntry,
    request_key: str,
    *,
    registry: InFlightRegistry | None = None,
) -> SafeDocument:
    return (registry or _DEFAULT_INFLIGHT_REGISTRY).await_result(entry, request_key)


def release_inflight(
    request_key: str,
    *,
    document: SafeDocument | None = None,
    error: Exception | None = None,
    registry: InFlightRegistry | None = None,
) -> int:
    return (registry or _DEFAULT_INFLIGHT_REGISTRY).release(
        request_key,
        document=document,
        error=error,
    )


def clone_cached_document(document: SafeDocument) -> SafeDocument:
    """Return an isolated cached document copy with cache metadata reset."""
    cached_copy = copy.deepcopy(document)
    cached_copy.metadata["cache_hit"] = True
    cached_copy.metadata["inflight_deduplicated"] = False
    cached_copy.metadata["inflight_shared_count"] = 0
    return cached_copy
