"""Screenshot side-effect cleanup helpers for ingestion pipelines."""

from __future__ import annotations

import logging
import os
import tempfile

from markdown_ingress.config_models import IngestConfig, RenderConfig

_logger = logging.getLogger(__name__)


def cleanup_screenshot(path: str | None, *, logger: logging.Logger = _logger) -> None:
    if not path:
        return
    try:
        os.unlink(path)
    except OSError as exc:
        logger.debug("Could not remove screenshot %s: %s", path, exc)


def cleanup_orphaned_screenshot(
    config: IngestConfig | RenderConfig,
    *,
    logger: logging.Logger = _logger,
) -> None:
    """Remove temporary screenshot file from a failed render attempt."""
    screenshot = config.screenshot
    if screenshot is True:
        return
    if not isinstance(screenshot, str) or not os.path.isfile(screenshot):
        return

    abs_path = os.path.abspath(screenshot)
    tmp_root = os.path.abspath(tempfile.gettempdir())
    if not abs_path.startswith(tmp_root + os.sep):
        return
    cleanup_screenshot(screenshot, logger=logger)
