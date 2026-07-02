"""
JavaScript injection for stealth mode.

This module provides:
- Core WebDriver detection patches
- Chrome runtime patches
- Permissions API patches
- Plugin and MIME type patches
- Language and locale patches
- Console debug protection
"""

import random

from markdown_ingress.stealth_js_payloads import (
    STEALTH_JS_INJECTION,
    STEALTH_JS_POST_LOAD,
    WEBGL_FINGERPRINTS,
)

_rng = random.SystemRandom()


def _get_random_webgl_fingerprint() -> tuple[str, str]:
    """Return one realistic WebGL vendor/renderer pair."""
    return _rng.choice(WEBGL_FINGERPRINTS)


def _playwright_errors() -> tuple[type[BaseException], ...]:
    try:
        from playwright.async_api import Error, TimeoutError
    except ImportError:  # pragma: no cover
        return (Exception,)
    return (TimeoutError, Error)


# ============================================================================
# INJECTION FUNCTION
# ============================================================================


async def inject_stealth(page):
    """
    Inject all stealth scripts into a Playwright page.

    This function patches all known detection vectors including:
    - navigator.webdriver
    - Chrome runtime
    - WebGL fingerprinting
    - Canvas fingerprinting
    - Plugin detection
    - And many more...

    Args:
        page: Playwright Page object

    Returns:
        None (modifies page in-place)

    Example:
        >>> page = await context.new_page()
        >>> await inject_stealth(page)
        >>> await page.goto("https://example.com")
    """
    await inject_stealth_pre_nav(page)
    await inject_stealth_post_nav(page)


async def inject_stealth_pre_nav(page) -> None:
    """Inject stealth init scripts that persist across navigations.

    Call this BEFORE page.goto(). Uses add_init_script so the patches
    are re-applied on every navigation and frame load.
    """
    webgl_vendor, webgl_renderer = _get_random_webgl_fingerprint()

    stealth_js = STEALTH_JS_INJECTION.replace("__WEBGL_VENDOR__", webgl_vendor).replace(
        "__WEBGL_RENDERER__", webgl_renderer
    )

    await page.add_init_script(stealth_js)


async def inject_stealth_post_nav(page) -> None:
    """Run post-load cleanup to remove automation artifacts.

    Call this AFTER page.goto() has completed, so the evaluate runs
    on the actual target page (not about:blank).
    """
    try:
        await page.evaluate(STEALTH_JS_POST_LOAD)
    except _playwright_errors():
        pass  # pragma: no cover
