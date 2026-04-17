"""Moved to adapters/rendering/playwright_renderer.py — re-exported for backward compatibility."""
from markdown_ingress.adapters.rendering.playwright_renderer import (
    PLAYWRIGHT_INSTALLED,
    Renderer,
    WaitUntil,
    _active_screenshots,
)

__all__ = ["PLAYWRIGHT_INSTALLED", "Renderer", "WaitUntil", "_active_screenshots"]
