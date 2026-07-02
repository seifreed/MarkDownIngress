"""Cleanup helpers for document build failures."""

from __future__ import annotations

import os

from markdown_ingress.models import FetchResult, SafeDocument


def cleanup_screenshot_on_failure(
    document: SafeDocument | None,
    fetch_result: FetchResult,
) -> None:
    """Remove a temporary screenshot file when document creation failed."""
    if document is None and fetch_result.metadata.get("screenshot_temp"):
        path = fetch_result.metadata.get("screenshot_path")
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
