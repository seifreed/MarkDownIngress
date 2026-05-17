"""Shared HTTP fetcher policy and response helpers."""

import logging
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import NamedTuple

import httpx

from markdown_ingress.core.policy import UnsupportedContentTypeError

logger = logging.getLogger(__name__)

SAFE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "max-age=0",
    "DNT": "1",
    "Upgrade-Insecure-Requests": "1",
}

RETRYABLE_STATUS = {403, 429, 500, 502, 503, 504}
FOLLOW_REDIRECT_STATUS = {301, 302, 303, 307, 308}
MAX_RETRIES = 3
DEFAULT_DOMAIN_STATE_TTL = 3600
DEFAULT_MAX_HOSTS = 10000
DEFAULT_MAX_RESPONSE_SIZE = 10 * 1024 * 1024

_HTML_CONTENT_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "application/xml",
    "text/xml",
)
_HOST_SOFT_THROTTLE_HINTS = {
    "amazon.com": (3.0, 2.5),
    "bestbuy.com": (2.5, 2.0),
    "bestbuyads.com": (2.5, 2.0),
    "facebook.com": (4.0, 3.0),
    "instagram.com": (4.0, 3.0),
    "sephora.com": (3.0, 2.0),
    "wayfair.com": (3.0, 2.0),
    "walmart.com": (2.5, 2.0),
}


class ResponseSizeLimitError(ValueError):
    """Raised when a response exceeds the configured fetch size limit."""


class PreparedRequest(NamedTuple):
    transport_url: str
    logical_url: str
    host_header: str | None
    sni_hostname: str | None
    logical_host: str


def format_host_header(hostname: str, port: int | None, scheme: str) -> str:
    host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    default_port = 443 if scheme.lower() == "https" else 80
    return f"{host}:{port}" if port is not None and port != default_port else host


def is_supported_html_content_type(content_type: str | None) -> bool:
    if content_type is None:
        logger.debug("Response has no Content-Type header; rejecting as non-HTML")
        return False
    if not content_type.strip():
        logger.debug("Response has empty Content-Type header; rejecting as non-HTML")
        return False
    normalized = content_type.split(";", 1)[0].strip().lower()
    return normalized in _HTML_CONTENT_TYPES


def validate_content_type(response: httpx.Response) -> None:
    content_type = response.headers.get("content-type")
    if is_supported_html_content_type(content_type):
        return
    raise UnsupportedContentTypeError(
        f"Unsupported content type for HTML ingestion: {content_type or 'unknown'}"
    )


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        return max(0.25, float(raw))
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(raw)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None
    return max(0.25, (retry_at - datetime.now(UTC)).total_seconds())


def retry_delay_seconds(response: httpx.Response, attempt: int) -> float:
    retry_after = parse_retry_after(response.headers.get("retry-after"))
    if retry_after is not None:
        return float(min(retry_after, 10.0))
    if response.status_code == 429:
        return float(min(1.5 * (2**attempt), 10.0))
    return float(min(0.5 * (attempt + 1), 2.0))


def is_ssl_verification_error(exc: Exception) -> bool:
    return "SSL" in type(exc).__name__ or "certificate" in str(exc).lower()


def should_retry_with_ssl_bypass(
    *, allow_ssl_bypass: bool, ssl_retried: bool, exc: Exception
) -> bool:
    return not ssl_retried and allow_ssl_bypass and is_ssl_verification_error(exc)


def ssl_bypass_retry_delay(attempt: int) -> float:
    return float(min(0.5 * (2**attempt), 2.0))


def parse_content_length(content_length: str | None) -> int | None:
    if not content_length:
        return None
    try:
        value = int(content_length)
    except ValueError:
        logger.warning("Malformed Content-Length header: %s", content_length)
        return None
    else:
        return value if value >= 0 else None


def host_soft_throttle_delay(host: str, base_delay: float) -> float:
    normalized = host.lower()
    for suffix, (multiplier, minimum_delay) in _HOST_SOFT_THROTTLE_HINTS.items():
        if normalized == suffix or normalized.endswith(f".{suffix}"):
            return max(base_delay * multiplier, minimum_delay)
    return base_delay
