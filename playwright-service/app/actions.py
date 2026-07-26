"""Action registry: playwright-service's extensible dispatch surface.

Adding a new action means writing a new async handler with this same
(Page, params) -> dict signature and registering it in ACTIONS -- the
POST /v1/actions endpoint (app/main.py) never needs to change shape.
"""
import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Dict

from playwright.async_api import Browser, Page

from app.browser import isolated_page

logger = logging.getLogger(__name__)

ActionHandler = Callable[[Page, Dict[str, Any]], Awaitable[Dict[str, Any]]]


async def resolve_and_render(page: Page, params: Dict[str, Any]) -> Dict[str, Any]:
    """Navigate to a URL, optionally wait out a client-side redirect, return final URL + HTML.

    `leave_prefix`, when given, is a generic "the redirect hasn't settled
    while this substring is still in the URL" marker -- callers resolving
    Google News links pass "/rss/articles" or similar; this function has
    no built-in knowledge of any specific news source.

    `timeout_ms` bounds the ENTIRE call (navigation + redirect wait), not
    each phase separately -- both share one wall-clock deadline computed
    before navigation starts.
    """
    url = params["url"]
    timeout_ms = int(params.get("timeout_ms", 30000))
    leave_prefix = params.get("leave_prefix")

    deadline = time.monotonic() + (timeout_ms / 1000)
    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

    if leave_prefix:
        while leave_prefix in page.url and time.monotonic() < deadline:
            await asyncio.sleep(1)

    html = await page.content()
    return {"final_url": page.url, "html": html}


ACTIONS: Dict[str, ActionHandler] = {
    "resolve_and_render": resolve_and_render,
}


async def execute_action(browser: Browser, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Look up and run a registered action inside a fresh isolated page.

    Raises KeyError if `action` isn't registered; propagates whatever the
    action itself raises on failure. The API layer (app/main.py) is
    responsible for turning both into an ActionResponse error -- this
    function never swallows errors itself.
    """
    handler = ACTIONS[action]
    async with isolated_page(browser) as page:
        return await handler(page, params)
