"""Shared screenshot caching policy for application use cases."""

from __future__ import annotations

from markdown_ingress.config_models import IngestConfig, RenderConfig


def screenshot_requires_fresh_capture(config: IngestConfig | RenderConfig) -> bool:
    """Return True when a request produces screenshot side effects that must not be cached."""
    return config.screenshot is True or isinstance(config.screenshot, str)
