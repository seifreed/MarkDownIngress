"""Per-domain throttling and circuit-breaker state for the HTTPX fetcher."""

import logging
import time
from threading import Lock
from typing import cast

from markdown_ingress.adapters.fetching.http_support import host_soft_throttle_delay
from markdown_ingress.core.policy import DomainCircuitOpenError

logger = logging.getLogger(__name__)


class DomainStateMixin:
    """Host-level rate limiting, backoff, and circuit-breaker behavior."""

    domain_request_interval: float
    domain_state_ttl: float
    max_hosts: int
    failure_decay_seconds: float | None
    circuit_breaker_threshold: int
    circuit_breaker_open_seconds: float
    _domain_lock: Lock
    _next_allowed_by_host: dict[str, float]
    _domain_state_timestamp: dict[str, float]
    _failure_lock: Lock
    _failures_by_host: dict[str, int]
    _failure_first_seen: dict[str, float]
    _open_until_by_host: dict[str, float]
    _cleanup_lock: Lock
    _last_cleanup: float
    _last_failure_cleanup: float
    _cleanup_running: bool

    def _cleanup_domain_state(self) -> None:
        now = time.monotonic()
        with self._cleanup_lock:
            if now - self._last_cleanup < 60.0 or self._cleanup_running:
                return
            self._last_cleanup = now
            self._cleanup_running = True
        try:
            self._do_cleanup_domain_state(now)
        finally:
            with self._cleanup_lock:
                self._cleanup_running = False

    def _do_cleanup_domain_state(self, now: float) -> None:
        stale_hosts_set: set[str] = set()

        if self.domain_state_ttl > 0:
            with self._domain_lock:
                stale_hosts = [
                    host
                    for host, ts in self._domain_state_timestamp.items()
                    if now - ts > self.domain_state_ttl
                ]
                stale_hosts_set = set(stale_hosts)
                for host in stale_hosts:
                    self._next_allowed_by_host.pop(host, None)
                    self._domain_state_timestamp.pop(host, None)

        with self._domain_lock:
            if len(self._next_allowed_by_host) > self.max_hosts:
                sorted_hosts = sorted(
                    self._domain_state_timestamp.items(),
                    key=lambda x: x[1],
                )
                evict_count = len(self._next_allowed_by_host) - self.max_hosts
                for host, _ in sorted_hosts[:evict_count]:
                    self._next_allowed_by_host.pop(host, None)
                    self._domain_state_timestamp.pop(host, None)
                    stale_hosts_set.add(host)

        with self._failure_lock:
            for host in stale_hosts_set:
                self._failures_by_host.pop(host, None)
                self._failure_first_seen.pop(host, None)

            remaining_stale = [
                host
                for host, ts in self._failure_first_seen.items()
                if now - ts > self.domain_state_ttl
            ]
            for host in remaining_stale:
                self._failures_by_host.pop(host, None)
                self._failure_first_seen.pop(host, None)

    def _apply_failure_decay_locked(self, host: str) -> int:
        if self.failure_decay_seconds is None or self.failure_decay_seconds <= 0:
            return self._failures_by_host.get(host, 0)

        now = time.monotonic()
        first_seen = self._failure_first_seen.get(host, now)
        current: int = self._failures_by_host.get(host, 0)

        if current > 0 and first_seen:
            elapsed = now - first_seen
            if elapsed > self.failure_decay_seconds:
                self._failures_by_host[host] = 0
                self._failure_first_seen[host] = now
                return 0
            failure_decay_seconds = cast(float, self.failure_decay_seconds)
            decay_factor = 0.5 ** (elapsed / failure_decay_seconds)
            decayed: int = round(float(current) * decay_factor)
            return decayed
        return current

    def _apply_failure_decay(self, host: str) -> int:
        with self._failure_lock:
            return self._apply_failure_decay_locked(host)

    def _reserve_domain_slot(self, host: str) -> float:
        if not host:
            logger.warning("Empty host detected - rate limiting bypassed for malformed URL")
            return 0.0

        self._cleanup_domain_state()

        with self._domain_lock:
            now = time.monotonic()
            next_allowed = self._next_allowed_by_host.get(host, 0.0)
            slot = max(now, next_allowed)
            if self.domain_request_interval > 0.0:
                self._next_allowed_by_host[host] = slot + self.domain_request_interval
            else:
                self._next_allowed_by_host[host] = slot
            self._domain_state_timestamp[host] = now
            return max(0.0, slot - now)

    def _defer_host(self, host: str, delay_seconds: float) -> None:
        if not host or delay_seconds <= 0.0:
            return
        with self._domain_lock:
            now = time.monotonic()
            next_allowed = self._next_allowed_by_host.get(host, now)
            self._next_allowed_by_host[host] = max(next_allowed, now + delay_seconds)
            self._domain_state_timestamp[host] = now

    def _ensure_circuit_closed(self, host: str) -> None:
        if not host:
            return
        with self._failure_lock:
            self._apply_failure_decay_locked(host)
            open_until = self._open_until_by_host.get(host, 0.0)
            if open_until > time.monotonic():
                raise DomainCircuitOpenError(f"Circuit breaker open for host: {host}")

    def _record_success(self, host: str) -> None:
        if not host:
            return
        with self._failure_lock:
            self._failures_by_host.pop(host, None)
            self._failure_first_seen.pop(host, None)
            self._open_until_by_host.pop(host, None)

    def _record_failure(self, host: str) -> None:
        if not host:
            return

        now = time.monotonic()
        should_cleanup = False
        with self._cleanup_lock:
            if now - self._last_failure_cleanup > 60.0:
                should_cleanup = True
                self._last_failure_cleanup = now

        with self._failure_lock:
            if host not in self._failure_first_seen:
                self._failure_first_seen[host] = now

            decayed = self._apply_failure_decay_locked(host)
            new_count = decayed + 1
            self._failures_by_host[host] = new_count

            if new_count >= self.circuit_breaker_threshold:
                self._open_until_by_host[host] = now + self.circuit_breaker_open_seconds
                self._failures_by_host[host] = max(1, (self.circuit_breaker_threshold + 1) // 2)

            if should_cleanup:
                self._cleanup_stale_failures_locked(now)

    def _cleanup_stale_failures_locked(self, now: float) -> None:
        if self.domain_state_ttl <= 0:
            return

        stale_hosts = [
            host
            for host, ts in self._failure_first_seen.items()
            if now - ts > self.domain_state_ttl
        ]

        for host in stale_hosts:
            self._failures_by_host.pop(host, None)
            self._failure_first_seen.pop(host, None)
            self._open_until_by_host.pop(host, None)

    def _record_soft_throttle(self, host: str, delay_seconds: float) -> None:
        if not host:
            return
        self._defer_host(host, host_soft_throttle_delay(host, delay_seconds))
        with self._failure_lock:
            self._failures_by_host.pop(host, None)
            self._failure_first_seen.pop(host, None)
