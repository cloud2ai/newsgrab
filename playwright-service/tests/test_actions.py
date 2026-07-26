"""Integration tests for app/actions.py against a real (Playwright-bundled) Chromium.

No mocks here: resolve_and_render's redirect-wait polling and resource
blocking are exercised against a genuine headless browser navigating a
local HTTP fixture, since that's the only way to prove the polling logic
actually waits out a client-side JS redirect rather than returning early.
"""
import pytest
from playwright.async_api import async_playwright

from app.actions import ACTIONS, execute_action, resolve_and_render


@pytest.fixture
async def browser():
    async with async_playwright() as playwright:
        chromium = await playwright.chromium.launch(headless=True)
        try:
            yield chromium
        finally:
            await chromium.close()


def test_resolve_and_render_is_registered():
    assert ACTIONS["resolve_and_render"] is resolve_and_render


async def test_resolve_and_render_waits_out_client_side_redirect(browser, local_http_server):
    local_http_server.routes["/redirect.html"] = (
        b"<!doctype html><html><body>"
        b"<script>setTimeout(function() { window.location.href = '/final.html'; }, 200);</script>"
        b"</body></html>"
    )
    local_http_server.routes["/final.html"] = b"<html><body><h1>Final Page</h1></body></html>"

    result = await execute_action(
        browser,
        "resolve_and_render",
        {
            "url": f"{local_http_server.base_url}/redirect.html",
            "timeout_ms": 5000,
            "leave_prefix": "/redirect.html",
        },
    )

    assert result["final_url"] == f"{local_http_server.base_url}/final.html"
    assert "Final Page" in result["html"]


async def test_resolve_and_render_without_leave_prefix_returns_immediately(browser, local_http_server):
    local_http_server.routes["/final.html"] = b"<html><body><h1>Final Page</h1></body></html>"

    result = await execute_action(
        browser,
        "resolve_and_render",
        {"url": f"{local_http_server.base_url}/final.html", "timeout_ms": 5000},
    )

    assert result["final_url"] == f"{local_http_server.base_url}/final.html"
    assert "Final Page" in result["html"]


async def test_resolve_and_render_blocks_images_during_redirect_wait(browser, local_http_server):
    local_http_server.routes["/page.html"] = (
        b"<html><body><img src='/pic.png'><h1>hello</h1></body></html>"
    )
    local_http_server.routes["/pic.png"] = b"\x89PNG\r\n\x1a\n"

    result = await execute_action(
        browser,
        "resolve_and_render",
        {"url": f"{local_http_server.base_url}/page.html", "timeout_ms": 5000},
    )

    assert "/pic.png" not in local_http_server.request_log
    assert "hello" in result["html"]


async def test_execute_action_raises_key_error_for_unknown_action(browser):
    with pytest.raises(KeyError):
        await execute_action(browser, "does_not_exist", {})
