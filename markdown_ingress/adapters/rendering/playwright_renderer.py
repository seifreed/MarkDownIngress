"""Explicit Playwright rendering adapter."""

from markdown_ingress.core.renderer import PLAYWRIGHT_INSTALLED, Renderer

PLAYWRIGHT_AVAILABLE = PLAYWRIGHT_INSTALLED
PlaywrightRenderer = Renderer

__all__ = ["PLAYWRIGHT_AVAILABLE", "PlaywrightRenderer"]
