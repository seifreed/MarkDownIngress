"""URL and content detection heuristics for the application layer."""

from __future__ import annotations

import re
from html import unescape
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
_RENDER_FALLBACK_BLOCK_TOKENS = (
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
    "name or service not known",
    "nodename nor servname",
    "request url has an unsupported protocol",
    "unsupported content type",
)
_SCRIPT_BLOCK_RE = re.compile(r"(?is)<\s*(script|style|template|svg)\b[^>]*>.*?</\s*\1\s*>")
_COMMENT_RE = re.compile(r"(?is)<!--.*?-->")
_TAG_RE = re.compile(r"(?is)<[^>]+>")
_SCRIPT_OPEN_RE = re.compile(r"(?is)<\s*script\b")
_SCRIPT_CONTENT_RE = re.compile(r"(?is)<\s*script\b[^>]*>(.*?)</\s*script\s*>")
_SCRIPT_SRC_OR_MODULE_RE = re.compile(
    r"(?is)<\s*script\b[^>]*(?:\bsrc\s*=|\btype\s*=\s*['\"]module)"
)
_CLIENT_REDIRECT_RE = re.compile(
    r"(?is)\b(?:window\.|document\.|top\.)?location(?:\.href)?\s*="
    r"|\blocation\.(?:assign|replace)\s*\("
    r"|\bwindow\.open\s*\("
)
_META_REFRESH_RE = re.compile(r"(?is)<\s*meta\b[^>]*http-equiv\s*=\s*['\"]?\s*refresh\b[^>]*>")
_APP_MOUNT_RE = re.compile(
    r"(?is)<\s*(?:div|main|section)\b[^>]*(?:id|class)\s*=\s*['\"][^'\"]*"
    r"(?:app|root|__next|__nuxt|gatsby|svelte|vue)[^'\"]*['\"][^>]*>\s*</"
)
_JAVASCRIPT_REQUIRED_TOKENS = (
    "enable javascript",
    "requires javascript",
    "javascript is required",
    "please turn on javascript",
    "please enable js",
)
_PLACEHOLDER_TOKENS = (
    "loading",
    "redirecting",
    "please wait",
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
    if isinstance(
        exc,
        (
            httpx.UnsupportedProtocol,
            httpx.InvalidURL,
            httpx.ConnectError,
            httpx.TimeoutException,
        ),
    ):
        return False
    message = str(exc).lower()
    return not any(token in message for token in _RENDER_FALLBACK_BLOCK_TOKENS)


def _should_attempt_fast_degraded_fallback(exc: Exception) -> bool:
    """Allow render mode to degrade to a plain HTTP fetch for transient browser/runtime failures."""
    if isinstance(exc, (httpx.UnsupportedProtocol, httpx.InvalidURL, UnsupportedContentTypeError)):
        return False
    message = str(exc).lower()
    retryable_tokens = (
        "err_failed",
        "err_internet_disconnected",
        "err_network_io_suspended",
        "page is navigating",
        "page.content",
    )
    return any(token in message for token in retryable_tokens)


def _is_render_timeout_failure(exc: Exception) -> bool:
    message = f"{type(exc).__name__}: {exc}".lower()
    return "timeout" in message or "timed out" in message


def _should_reuse_fast_result_after_render_failure(exc: Exception) -> bool:
    """Allow auto mode to keep its already fetched fast document after render timeout."""
    return _should_attempt_fast_degraded_fallback(exc) or _is_render_timeout_failure(exc)


def _visible_html_text(html: str) -> str:
    """Return coarse visible text, excluding script/style/template payloads."""
    without_blocks = _SCRIPT_BLOCK_RE.sub(" ", html)
    without_comments = _COMMENT_RE.sub(" ", without_blocks)
    text = _TAG_RE.sub(" ", without_comments)
    return " ".join(unescape(text).split())


def _fast_html_render_hint(html: str) -> str | None:
    """Return why an HTTP fetch looks like it needs browser rendering, if it does."""
    if not html or not html.strip():
        return None

    sample = html[:1_000_000]
    lowered = sample.lower()
    if _META_REFRESH_RE.search(sample):
        return "meta_refresh"
    if _CLIENT_REDIRECT_RE.search(sample):
        return "client_redirect"
    if any(token in lowered for token in _JAVASCRIPT_REQUIRED_TOKENS):
        return "javascript_required"

    visible_text = _visible_html_text(sample)
    script_count = len(_SCRIPT_OPEN_RE.findall(sample))
    if script_count <= 0:
        return None
    if not visible_text.strip():
        return "javascript_shell"

    script_text_size = sum(len(match.group(1)) for match in _SCRIPT_CONTENT_RE.finditer(sample))
    has_bundle_script = bool(_SCRIPT_SRC_OR_MODULE_RE.search(sample))
    has_app_mount = bool(_APP_MOUNT_RE.search(sample))
    visible_size = len(visible_text)
    lowered_visible = visible_text.lower()

    if visible_size < 80 and has_app_mount and has_bundle_script:
        return "javascript_shell"
    if visible_size < 40 and script_count >= 2 and has_bundle_script:
        return "javascript_shell"
    if visible_size < 40 and script_text_size > visible_size * 20:
        return "javascript_shell"
    if visible_size < 60 and any(token in lowered_visible for token in _PLACEHOLDER_TOKENS):
        return "javascript_placeholder"
    return None
