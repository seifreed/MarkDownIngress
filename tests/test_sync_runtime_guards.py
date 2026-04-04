"""Tests for sync wrappers that should reject active event loops cleanly."""

import asyncio

import pytest

from markdown_ingress.core.advanced_stealth import (
    AdvancedStealthRenderer,
    render_with_advanced_stealth_sync,
)
from markdown_ingress.core.renderer import Renderer


async def _call_renderer_sync_in_loop() -> None:
    renderer = Renderer()
    with pytest.raises(RuntimeError, match="await render\\(\\) instead"):
        renderer.render_sync("https://example.com")


async def _call_advanced_renderer_sync_in_loop() -> None:
    renderer = AdvancedStealthRenderer()
    with pytest.raises(RuntimeError, match="await render\\(\\) instead"):
        renderer.render_sync("https://example.com")


async def _call_advanced_helper_sync_in_loop() -> None:
    with pytest.raises(
        RuntimeError,
        match="await render_with_advanced_stealth\\(\\) instead",
    ):
        render_with_advanced_stealth_sync("https://example.com")


def test_renderer_sync_rejects_active_event_loop():
    asyncio.run(_call_renderer_sync_in_loop())


def test_advanced_stealth_renderer_sync_rejects_active_event_loop():
    asyncio.run(_call_advanced_renderer_sync_in_loop())


def test_advanced_stealth_helper_sync_rejects_active_event_loop():
    asyncio.run(_call_advanced_helper_sync_in_loop())
