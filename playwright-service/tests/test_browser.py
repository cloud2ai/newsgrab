"""Tests for app/browser.py: CDP connection retry, stealth, context isolation."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from playwright.async_api import async_playwright

from app.browser import connect_with_retry, isolated_page


async def test_connect_with_retry_succeeds_first_try():
    fake_browser = MagicMock()

    def make_fake_playwright():
        pw = MagicMock()
        pw.chromium.connect_over_cdp = AsyncMock(return_value=fake_browser)
        pw.stop = AsyncMock()
        return pw

    fake_pw_context = MagicMock()
    fake_pw_context.start = AsyncMock(side_effect=make_fake_playwright)

    with patch("app.browser.async_playwright", return_value=fake_pw_context):
        playwright, browser = await connect_with_retry(
            "http://localhost:9222", attempts=3, delay_sec=0
        )

    assert browser is fake_browser
    playwright.stop.assert_not_called()


async def test_connect_with_retry_retries_then_succeeds():
    fake_browser = MagicMock()
    attempt_count = {"n": 0}

    def make_fake_playwright():
        pw = MagicMock()

        async def flaky_connect(url):
            attempt_count["n"] += 1
            if attempt_count["n"] < 3:
                raise ConnectionError("not ready yet")
            return fake_browser

        pw.chromium.connect_over_cdp = flaky_connect
        pw.stop = AsyncMock()
        return pw

    fake_pw_context = MagicMock()
    fake_pw_context.start = AsyncMock(side_effect=make_fake_playwright)

    with patch("app.browser.async_playwright", return_value=fake_pw_context):
        playwright, browser = await connect_with_retry(
            "http://localhost:9222", attempts=5, delay_sec=0
        )

    assert browser is fake_browser
    assert attempt_count["n"] == 3


async def test_connect_with_retry_raises_after_exhausting_attempts():
    def make_fake_playwright():
        pw = MagicMock()

        async def always_fail(url):
            raise ConnectionError("still not ready")

        pw.chromium.connect_over_cdp = always_fail
        pw.stop = AsyncMock()
        return pw

    fake_pw_context = MagicMock()
    fake_pw_context.start = AsyncMock(side_effect=make_fake_playwright)

    with patch("app.browser.async_playwright", return_value=fake_pw_context):
        with pytest.raises(ConnectionError, match="still not ready"):
            await connect_with_retry("http://localhost:9222", attempts=2, delay_sec=0)


@pytest.mark.integration
async def test_isolated_page_hides_webdriver_flag():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            async with isolated_page(browser) as page:
                await page.goto("about:blank")
                webdriver_flag = await page.evaluate("navigator.webdriver")
        finally:
            await browser.close()

    assert webdriver_flag is None


@pytest.mark.integration
async def test_isolated_page_blocks_image_and_stylesheet_requests(local_http_server):
    local_http_server.routes["/page.html"] = (
        b"<html><body><img src='/pic.png'>"
        b"<link rel='stylesheet' href='/style.css'><h1>hello</h1></body></html>"
    )
    local_http_server.routes["/pic.png"] = b"\x89PNG\r\n\x1a\n"
    local_http_server.routes["/style.css"] = b"body { color: red; }"

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            async with isolated_page(browser) as page:
                await page.goto(f"{local_http_server.base_url}/page.html")
        finally:
            await browser.close()

    assert "/pic.png" not in local_http_server.request_log
    assert "/style.css" not in local_http_server.request_log
    assert "/page.html" in local_http_server.request_log


@pytest.mark.integration
async def test_isolated_page_contexts_do_not_share_cookies(local_http_server):
    local_http_server.routes["/x.html"] = b"<html><body>x</body></html>"

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            async with isolated_page(browser) as page1:
                await page1.context.add_cookies([{
                    "name": "session",
                    "value": "abc",
                    "url": local_http_server.base_url,
                }])
                cookies_in_first_context = await page1.context.cookies()
                assert len(cookies_in_first_context) == 1

            async with isolated_page(browser) as page2:
                cookies_in_second_context = await page2.context.cookies()

            assert cookies_in_second_context == []
        finally:
            await browser.close()
