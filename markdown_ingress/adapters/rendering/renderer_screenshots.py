"""Screenshot lifecycle helpers for Playwright renderers."""

from __future__ import annotations

import atexit
import logging
import os
import tempfile
import threading
from contextlib import suppress

logger = logging.getLogger(__name__)

_active_screenshots: set[str] = set()
_screenshots_lock = threading.Lock()


def cleanup_pending_screenshots() -> None:
    """atexit handler: clean up any temp screenshot files still on disk."""
    with _screenshots_lock:
        paths = list(_active_screenshots)
        _active_screenshots.clear()
    for path in paths:
        with suppress(OSError):
            os.unlink(path)


async def capture_screenshot(page, screenshot: bool | str | None) -> str | None:
    """Capture a screenshot to a configured path or managed temp file."""
    if not screenshot:
        return None

    if screenshot is True:
        with tempfile.NamedTemporaryFile(
            suffix=".png", delete=False, prefix="mdingress_screenshot_"
        ) as temp_file:
            screenshot_path = temp_file.name
        with _screenshots_lock:
            _active_screenshots.add(screenshot_path)
    else:
        screenshot_path = str(screenshot)

    try:
        await page.screenshot(path=screenshot_path, full_page=True)
    except Exception as exc:
        logger.debug("Screenshot capture failed: %s", exc)
        if screenshot is True:
            cleanup_screenshot(screenshot_path)
        raise
    return screenshot_path


def cleanup_screenshot(path: str | None) -> None:
    """Delete a screenshot path and remove it from temp tracking."""
    if not path:
        return
    try:
        os.unlink(path)
        with _screenshots_lock:
            _active_screenshots.discard(path)
    except OSError:
        with _screenshots_lock:
            _active_screenshots.discard(path)


atexit.register(cleanup_pending_screenshots)
