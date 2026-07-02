"""Navigation and content extraction helpers for Playwright render sessions."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from typing import Any, Literal, cast

WaitUntil = Literal["commit", "domcontentloaded", "load", "networkidle"]

logger = logging.getLogger(__name__)

CONTENT_SELECTORS = (
    "article",
    "main",
    '[role="main"]',
    ".content",
    "#content",
    "body",
)

PlaywrightError: type[Exception]
PlaywrightTimeoutError: type[Exception]
try:
    from playwright.async_api import (
        Error as _PlaywrightError,
    )
    from playwright.async_api import (
        TimeoutError as _PlaywrightTimeoutError,
    )

    PlaywrightError = _PlaywrightError
    PlaywrightTimeoutError = _PlaywrightTimeoutError
except ImportError:  # pragma: no cover
    PlaywrightError = Exception  # pragma: no cover
    PlaywrightTimeoutError = Exception  # pragma: no cover


async def wait_for_content(
    page: Any,
    *,
    max_wait: int | float = 30,
    selectors: tuple[str, ...] = CONTENT_SELECTORS,
) -> None:
    wait_start = time.time()
    max_wait_ms = max_wait * 1000

    if selectors:
        remaining_ms = max(0, max_wait_ms)
        if remaining_ms > 0:
            with suppress(PlaywrightTimeoutError, PlaywrightError):
                selector_query = ", ".join(selectors)
                selector_timeout = min(2500, remaining_ms)
                await page.wait_for_selector(selector_query, timeout=selector_timeout)
                logger.info("[Smart Wait] Found content selector: %s", selector_query)

    elapsed = time.time() - wait_start
    remaining_ms = max(0, (max_wait - elapsed)) * 1000
    if remaining_ms <= 0:
        logger.warning("[Smart Wait] No time remaining for content verification")
        return
    timeout_ms = min(3000, int(remaining_ms))

    try:
        await page.wait_for_function(
            """
            () => {
                const body = document.body;
                if (!body) return false;

                const hasContent = document.querySelector('p, article, main, [role="main"]')
                    || body;
                const text = (hasContent.innerText || body.innerText || '').trim();
                if (!text) return false;

                const loadingIndicators = document.querySelectorAll(
                    '[class*="loading"], [class*="spinner"], [id*="loading"]'
                );
                for (const indicator of loadingIndicators) {
                    const style = window.getComputedStyle(indicator);
                    if (style.display !== 'none' && style.visibility !== 'hidden') {
                        return false;
                    }
                }

                return true;
            }
            """,
            timeout=timeout_ms,
        )
        logger.info("[Smart Wait] Content verification passed")
    except PlaywrightError as exc:
        logger.warning("[Smart Wait] Content verification timed out: %s", exc)


async def navigate_page(page: Any, url: str, timeout_ms: int, *, wait_until: str) -> Any:
    response = await page.goto(
        url,
        timeout=timeout_ms,
        wait_until=cast(WaitUntil, wait_until),
    )
    if wait_until != "networkidle":
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=min(1500, timeout_ms))
        except PlaywrightError as exc:
            logger.debug("DOMContentLoaded wait skipped: %s", exc)
    return response


async def extract_page_content(page: Any) -> str:
    for attempt in range(3):
        try:
            return cast(str, await page.content())
        except Exception as exc:
            if "page is navigating" not in str(exc).lower():
                raise
            if attempt == 2:
                break
            await asyncio.sleep(0.2 * (attempt + 1))

    html = None
    try:
        html = await page.evaluate("""
            () => {
                const root = document.documentElement;
                return root ? root.outerHTML : '';
            }
            """)
        if isinstance(html, str) and html.strip():
            return html
    except PlaywrightError as exc:
        logger.debug("Page evaluate fallback failed: %s", exc)
    raise RuntimeError("Unable to retrieve page content after navigation churn")
