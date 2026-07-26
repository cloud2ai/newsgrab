"""Chromium CDP connection, stealth configuration, and per-call context isolation.

Production connects to a Chromium already running in this same container
(started by entrypoint.sh with --remote-debugging-port=9222). Tests instead
launch Playwright's own bundled Chromium directly (playwright.chromium.launch)
to exercise isolated_page's stealth/blocking/isolation behavior without
needing the container's CDP setup.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Tuple

from playwright.async_api import Browser, Page, Playwright, async_playwright
from playwright_stealth import Stealth

logger = logging.getLogger(__name__)

_STEALTH_INIT_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined
    });
    Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5]
    });
    Object.defineProperty(navigator, 'languages', {
        get: () => ['zh-CN', 'zh']
    });
"""

_BLOCKED_RESOURCE_TYPES = {"image", "stylesheet", "font"}


async def connect_with_retry(
    cdp_url: str,
    *,
    attempts: int = 10,
    delay_sec: float = 1.0,
) -> Tuple[Playwright, Browser]:
    """Connect to a locally-running Chromium's CDP endpoint, retrying while it starts up.

    Each attempt starts a fresh Playwright driver and tries connect_over_cdp;
    on failure the driver is stopped before the next attempt so nothing leaks.
    Raises the last connection error if every attempt fails.
    """
    last_error: Exception = RuntimeError("no connection attempts made")
    for attempt in range(1, attempts + 1):
        playwright = await async_playwright().start()
        try:
            browser = await playwright.chromium.connect_over_cdp(cdp_url)
            return playwright, browser
        except Exception as exc:
            last_error = exc
            await playwright.stop()
            logger.warning(
                "[browser] connect_over_cdp attempt %s/%s to %s failed: %s",
                attempt, attempts, cdp_url, exc,
            )
            if attempt < attempts:
                await asyncio.sleep(delay_sec)
    raise last_error


async def _block_heavy_resources(route) -> None:
    if route.request.resource_type in _BLOCKED_RESOURCE_TYPES:
        await route.abort()
    else:
        await route.continue_()


@asynccontextmanager
async def isolated_page(browser: Browser) -> AsyncIterator[Page]:
    """Yield a Page in a fresh, stealth-configured BrowserContext.

    The context is always closed on exit, so cookies/storage from one call
    never leak into the next -- each API call gets a clean slate.
    """
    context = await browser.new_context()
    try:
        await context.add_init_script(_STEALTH_INIT_SCRIPT)
        page = await context.new_page()
        stealth = Stealth()
        await stealth.apply_stealth_async(page)
        await page.route("**/*", _block_heavy_resources)
        yield page
    finally:
        await context.close()
