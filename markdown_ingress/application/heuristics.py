"""URL and content detection heuristics for the application layer."""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx

from markdown_ingress.core.policy import (
    DomainCircuitOpenError,
    PolicyBlockedError,
    UnsupportedContentTypeError,
)

_NON_HTML_EXTENSIONS = {
    ".7z",
    ".avi",
    ".bin",
    ".csv",
    ".doc",
    ".docx",
    ".epub",
    ".exe",
    ".gz",
    ".iso",
    ".jpeg",
    ".jpg",
    ".json",
    ".mov",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".rar",
    ".rss",
    ".svg",
    ".tar",
    ".tgz",
    ".txt",
    ".wav",
    ".webm",
    ".xml",
    ".xls",
    ".xlsx",
    ".zip",
}
_AUTH_PATH_TOKENS = (
    "account",
    "accounts",
    "auth",
    "login",
    "oauth",
    "signin",
    "sign-in",
    "signup",
    "sign-up",
)


def _looks_like_non_html_resource(url: str) -> bool:
    """Best-effort URL heuristic to avoid launching Playwright for obvious downloads."""
    path = urlsplit(url).path.lower()
    return any(path.endswith(extension) for extension in _NON_HTML_EXTENSIONS)


def _looks_like_auth_interstitial(url: str) -> bool:
    """Skip costly auto-render for account/login flows that rarely improve via Playwright.

    Inspects hostname labels (split by ``"."``) and path segments (split by
    ``"/"``), but NOT query parameters, to avoid false positives like
    ``?tracking=account_id``.
    """
    parsed = urlsplit(url)
    tokens: set[str] = set()
    if parsed.hostname:
        tokens.update(label.lower() for label in parsed.hostname.split("."))
    tokens.update(seg.lower() for seg in parsed.path.split("/") if seg)
    return any(token in tokens for token in _AUTH_PATH_TOKENS)


def _should_attempt_render_fallback(exc: Exception) -> bool:
    """Limit auto-mode render fallback to failures a browser may realistically improve."""
    if isinstance(exc, (DomainCircuitOpenError, UnsupportedContentTypeError, PolicyBlockedError)):
        return False
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {403, 429, 503}
    if isinstance(exc, (httpx.UnsupportedProtocol, httpx.InvalidURL, httpx.ConnectError)):
        return False
    if isinstance(exc, httpx.TimeoutException):
        return False
    message = str(exc).lower()
    url_validation_tokens = (
        "ssrf protection",
        "blocked ip",
        "blocked range",
        "hostname blocked",
        "host could not be resolved",
        "invalid url",
        "url cannot be empty",
        "valid network location",
        "valid host",
        "invalid url scheme",
        "invalid url port",
        "forbidden crlf",
        "null byte",
    )
    if any(token in message for token in url_validation_tokens):
        return False
    if "name or service not known" in message or "nodename nor servname" in message:
        return False
    if "request url has an unsupported protocol" in message:
        return False
    if "unsupported content type" in message:
        return False
    return True


def _should_attempt_fast_degraded_fallback(exc: Exception) -> bool:
    """Allow render mode to degrade to a plain HTTP fetch for transient browser/runtime failures."""
    if isinstance(exc, (httpx.UnsupportedProtocol, httpx.InvalidURL, UnsupportedContentTypeError)):
        return False
    message = str(exc).lower()
    retryable_tokens = (
        "err_failed",
        "err_internet_disconnected",
        "err_network_io_suspended",
        "timeout",
        "page is navigating",
        "page.content",
    )
    return any(token in message for token in retryable_tokens)
