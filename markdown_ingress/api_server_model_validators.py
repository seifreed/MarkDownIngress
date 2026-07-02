"""Validation helpers for API server request models."""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from markdown_ingress.config_models import (
    VALID_OUTPUT_REPRESENTATIONS,
    VALID_POLICY_NAMES,
)
from markdown_ingress.core.ssrf import (
    resolve_allow_local_urls,
    validate_http_url_no_ssrf,
    validate_http_url_no_ssrf_with_dns_check,
)


def validate_url_no_ssrf(
    url: str,
    *,
    allow_local: bool | None = None,
    resolve_dns: bool = True,
) -> str:
    """Validate URL against SSRF attacks."""
    resolved_allow_local = resolve_allow_local_urls(allow_local)
    if resolve_dns:
        return validate_http_url_no_ssrf_with_dns_check(url, allow_local=resolved_allow_local)
    return validate_http_url_no_ssrf(
        url,
        allow_local=resolved_allow_local,
        resolve_dns=False,
    )


def validate_output_formats_value(value: list[str]) -> list[str]:
    """Validate supported document output representations for HTTP requests."""
    if not value:
        raise ValueError("output_formats must be a non-empty list of supported format strings")
    for index, item in enumerate(value):
        if item not in VALID_OUTPUT_REPRESENTATIONS:
            raise ValueError(
                f"output_formats[{index}] has invalid value '{item}'. "
                f"Must be one of: {', '.join(VALID_OUTPUT_REPRESENTATIONS)}"
            )
    return value


def validate_policy_name_value(value: str | None) -> str | None:
    """Validate supported policy names for HTTP requests."""
    if value is None:
        return None
    if value not in VALID_POLICY_NAMES:
        raise ValueError(
            f"policy_name has invalid value '{value}'. "
            f"Must be one of: {', '.join(VALID_POLICY_NAMES)}"
        )
    return value


def validate_reports_dir_value(value: str) -> str:
    """Validate that reports_dir is non-empty and safe from path traversal."""
    if not value.strip():
        raise ValueError("reports_dir cannot be empty")
    if "\x00" in value:
        raise ValueError("reports_dir contains null byte")
    if (
        Path(value).is_absolute()
        or PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
    ):
        raise ValueError("reports_dir must be relative")
    if ".." in value.split("/") or ".." in value.split("\\"):
        raise ValueError("reports_dir must not contain '..' path segments")
    return value


def validate_api_screenshot_value(value: Any) -> bool | None:
    """Restrict HTTP API screenshot requests to server-managed captures."""
    if value is None or isinstance(value, bool):
        return value
    raise ValueError("screenshot must be a boolean or null in the HTTP API")
