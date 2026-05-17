"""Mutable state carried through HTTP fetch attempts."""

from __future__ import annotations

from dataclasses import dataclass

from markdown_ingress.adapters.fetching.ssl_bypass_state import SslBypassFetchState


@dataclass
class HttpFetchState:
    """Mutable request state shared by regular and SSL-bypass fetch paths."""

    url: str
    logical_url: str
    requested_logical_url: str
    host_header: str | None
    sni_hostname: str | None
    host: str
    ssl_retried: bool
    verify: bool | str
    attempt: int = 0
    redirect_count: int = 0
    previous_ua: str | None = None
    last_exc: Exception | None = None

    @classmethod
    def from_prepared_request(
        cls,
        prepared: tuple[str, str, str | None, str | None, str],
        *,
        ssl_retried: bool,
    ) -> HttpFetchState:
        url, logical_url, host_header, sni_hostname, host = prepared
        return cls(
            url=url,
            logical_url=logical_url,
            requested_logical_url=logical_url,
            host_header=host_header,
            sni_hostname=sni_hostname,
            host=host,
            ssl_retried=ssl_retried,
            verify=not ssl_retried,
        )

    def update_redirect_target(
        self,
        redirect_target: tuple[str, str, str | None, str | None, str],
    ) -> None:
        self.url, self.logical_url, self.host_header, self.sni_hostname, self.host = redirect_target
        self.redirect_count += 1

    def mark_ssl_bypass_retry(self) -> None:
        self.verify = False
        self.ssl_retried = True
        self.attempt += 1

    def to_ssl_bypass_state(self) -> SslBypassFetchState:
        return SslBypassFetchState(
            url=self.url,
            logical_url=self.logical_url,
            requested_logical_url=self.requested_logical_url,
            host_header=self.host_header,
            sni_hostname=self.sni_hostname,
            host=self.host,
            redirect_count=self.redirect_count,
            previous_ua=self.previous_ua,
        )


@dataclass(frozen=True)
class HttpFetchAttemptContext:
    """Immutable timing and request metadata for one regular HTTP fetch attempt."""

    state: HttpFetchState
    start_time: float
    user_agent: str
