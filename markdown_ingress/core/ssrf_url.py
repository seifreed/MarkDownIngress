"""HTTP URL parsing and reconstruction helpers for SSRF validation."""

from __future__ import annotations

from urllib.parse import SplitResult, urlsplit, urlunsplit


def normalize_http_url_text(url: str) -> str:
    normalized_url = str(url).strip()
    if not normalized_url:
        raise ValueError("URL cannot be empty")
    if "\r" in normalized_url or "\n" in normalized_url:
        raise ValueError(f"URL contains forbidden CRLF characters: {url!r}")
    if "\x00" in normalized_url:
        raise ValueError(f"URL contains null byte: {url!r}")
    return normalized_url


def split_http_url_for_ssrf(normalized_url: str, original_url: str) -> SplitResult:
    try:
        return urlsplit(normalized_url)
    except ValueError as exc:
        raise ValueError(f"Invalid URL format: {original_url!r}: {exc}") from exc


def validate_http_url_authority(parsed: SplitResult, original_url: str) -> int | None:
    if not parsed.netloc:
        raise ValueError(f"URL must have a valid network location: {original_url!r}")

    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(
            f"Invalid URL scheme: {scheme!r}. Only http and https are allowed. "
            f"URL: {original_url!r}"
        )

    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"Invalid URL port: {original_url!r}: {exc}") from exc
    if port is not None and (port < 1 or port > 65535):
        raise ValueError(f"Invalid URL port: {original_url!r}. Port must be between 1 and 65535")
    return port


def build_pinned_url_netloc(
    parsed: SplitResult,
    validated_target: str,
    port: int | None,
) -> str:
    if ":" in validated_target:
        new_netloc = f"[{validated_target}]"
    else:
        new_netloc = validated_target

    if port is not None:
        new_netloc = f"{new_netloc}:{port}"

    if parsed.username:
        auth = parsed.username
        if parsed.password:
            auth = f"{auth}:{parsed.password}"
        new_netloc = f"{auth}@{new_netloc}"
    return new_netloc


def reconstruct_url_with_pinned_target(
    parsed: SplitResult,
    *,
    validated_target: str,
    port: int | None,
) -> str:
    new_netloc = build_pinned_url_netloc(parsed, validated_target, port)
    return urlunsplit((parsed.scheme, new_netloc, parsed.path, parsed.query, parsed.fragment))
