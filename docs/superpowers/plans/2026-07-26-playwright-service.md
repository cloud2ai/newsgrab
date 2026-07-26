# playwright-service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `playwright-service`, a single long-running containerized browser-automation HTTP service that navigates URLs with a stealth-configured Chromium and returns the final resolved URL plus rendered HTML, exposed via a generic action-dispatch API so future actions can be added without changing the endpoint shape.

**Architecture:** One container runs Xvfb + a system-installed Chromium (started with CDP enabled, bound to localhost) plus a FastAPI app that connects to that same-container Chromium via Playwright's `connect_over_cdp`. Each API call gets a fresh, stealth-configured `BrowserContext` (no session/cookie reuse across calls), executes one registered "action" against it, and returns a uniform `{success, result, error}` envelope — never a raw 500.

**Tech Stack:** Python, FastAPI, Playwright (async API), playwright-stealth, uvicorn, Debian bookworm-slim + system Chromium, tini.

## Global Constraints

- CDP port is `9222`, bound to `localhost` only inside the container — never exposed outside it, since the FastAPI process and Chromium share one container.
- Chrome launch flags (exact, from two verified sources — CDP/display flags from `newshub/newsbot/browser/entrypoint.sh`, automation-stability flags from `newshub/newsbot/newsbot/browser/browser_bot.py`'s legacy launch config): `--remote-debugging-port=9222 --user-data-dir=/tmp/chrome-data --disable-blink-features=AutomationControlled --disable-background-timer-throttling --disable-backgrounding-occluded-windows --disable-renderer-backgrounding --no-sandbox --disable-dev-shm-usage --disable-web-security --mute-audio --disable-features=IsolateOrigins,site-per-process`.
- Stealth init script (exact, from `browser_bot.py:200-214`): overrides `navigator.webdriver` → `undefined`, `navigator.plugins` → `[1,2,3,4,5]`, `navigator.languages` → `['zh-CN', 'zh']`, applied via `context.add_init_script`, plus `playwright_stealth.Stealth().apply_stealth_async(page)` per page.
- API contract is fixed for this plan: `POST /v1/actions` request `{"action": str, "params": dict}`, response `{"success": bool, "result": dict|null, "error": str|null}` — HTTP status is always 200 for a request FastAPI itself could parse; failures are expressed in the JSON body, never as a 4xx/5xx from action execution.
- `resolve_and_render` params: `{"url": str, "timeout_ms": int = 30000, "leave_prefix": str|None = None}`; result: `{"final_url": str, "html": str}`. No Google-News-specific knowledge belongs in this service.
- No authentication on this service — security boundary is the deployment's internal docker network (per design spec §2). Do not add API keys or tokens.
- Every API call gets its own fresh `BrowserContext`, closed on exit — never share contexts/cookies across calls.
- No screenshot, no arbitrary-script-execution action in this plan — only `resolve_and_render`. The action registry must stay extensible for later additions without this plan implementing them.

---

## Task 1: Project skeleton, schemas, and `/healthz`

**Files:**
- Create: `playwright-service/pyproject.toml`
- Create: `playwright-service/app/__init__.py`
- Create: `playwright-service/app/schemas.py`
- Create: `playwright-service/app/main.py`
- Create: `playwright-service/tests/__init__.py`
- Test: `playwright-service/tests/test_main.py`

**Interfaces:**
- Produces: `ActionRequest(action: str, params: dict = {})`, `ActionResponse(success: bool, result: dict|None = None, error: str|None = None)`, `HealthResponse(status: str, browser_connected: bool)` (all pydantic `BaseModel`s in `app/schemas.py`); `app: FastAPI` and `get_browser(request: Request)` dependency function in `app/main.py` (later tasks import both — `get_browser` is what tests override to inject a fake browser, and what Task 4's real lifespan wiring targets via `app.state.browser`).

- [ ] **Step 1: Create the project directories**

```bash
mkdir -p /home/ubuntu/workspace/newsgrab/playwright-service/app
mkdir -p /home/ubuntu/workspace/newsgrab/playwright-service/tests
touch /home/ubuntu/workspace/newsgrab/playwright-service/app/__init__.py
touch /home/ubuntu/workspace/newsgrab/playwright-service/tests/__init__.py
```

- [ ] **Step 2: Write `pyproject.toml`**

`playwright-service/pyproject.toml`:

```toml
[project]
name = "playwright-service"
version = "0.1.0"
description = "Stealth-configured browser automation service for newsgrab"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "playwright>=1.40.0",
    "playwright-stealth>=1.0.6",
    "pydantic>=2.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.24.0",
    "httpx>=0.27.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"

[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
packages = ["app"]
```

- [ ] **Step 3: Install the project in editable/dev mode**

Run: `cd /home/ubuntu/workspace/newsgrab/playwright-service && pip install --break-system-packages -e ".[dev]"`
Expected: install succeeds (or already-satisfied) for fastapi/uvicorn/playwright/playwright-stealth/pydantic/pytest/pytest-asyncio/httpx.

- [ ] **Step 4: Write `app/schemas.py`**

```python
"""Pydantic request/response models for playwright-service's HTTP API."""
from typing import Any, Dict, Optional

from pydantic import BaseModel


class ActionRequest(BaseModel):
    action: str
    params: Dict[str, Any] = {}


class ActionResponse(BaseModel):
    success: bool
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    browser_connected: bool
```

- [ ] **Step 5: Write the failing test for `/healthz`**

`playwright-service/tests/test_main.py`:

```python
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from app.main import app, get_browser


def test_healthz_reports_connected_when_browser_is_connected():
    fake_browser = MagicMock()
    fake_browser.is_connected.return_value = True
    app.dependency_overrides[get_browser] = lambda: fake_browser

    client = TestClient(app)
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "browser_connected": True}
    app.dependency_overrides.clear()


def test_healthz_reports_degraded_when_browser_is_disconnected():
    fake_browser = MagicMock()
    fake_browser.is_connected.return_value = False
    app.dependency_overrides[get_browser] = lambda: fake_browser

    client = TestClient(app)
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "degraded", "browser_connected": False}
    app.dependency_overrides.clear()


def test_healthz_reports_degraded_when_browser_is_none():
    app.dependency_overrides[get_browser] = lambda: None

    client = TestClient(app)
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "degraded", "browser_connected": False}
    app.dependency_overrides.clear()
```

- [ ] **Step 6: Run the test to verify it fails**

Run: `cd /home/ubuntu/workspace/newsgrab/playwright-service && python -m pytest tests/test_main.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 7: Write `app/main.py`**

```python
"""FastAPI app: health check + (later) the /v1/actions dispatch endpoint."""
import logging

from fastapi import Depends, FastAPI, Request

from app.schemas import HealthResponse

logger = logging.getLogger(__name__)

app = FastAPI(title="newsgrab playwright-service")


def get_browser(request: Request):
    """Return the connected Browser instance, or None before startup completes.

    Tests override this dependency directly (app.dependency_overrides) instead
    of running the real Playwright/CDP startup sequence added in Task 4.
    """
    return getattr(request.app.state, "browser", None)


@app.get("/healthz", response_model=HealthResponse)
async def healthz(browser=Depends(get_browser)) -> HealthResponse:
    connected = bool(browser and browser.is_connected())
    return HealthResponse(status="ok" if connected else "degraded", browser_connected=connected)
```

- [ ] **Step 8: Run the test to verify it passes**

Run: `cd /home/ubuntu/workspace/newsgrab/playwright-service && python -m pytest tests/test_main.py -v`
Expected: PASS (3 tests)

- [ ] **Step 9: Commit**

```bash
cd /home/ubuntu/workspace/newsgrab
git add playwright-service/pyproject.toml playwright-service/app/__init__.py \
        playwright-service/app/schemas.py playwright-service/app/main.py \
        playwright-service/tests/__init__.py playwright-service/tests/test_main.py
git commit -m "feat: scaffold playwright-service with schemas and /healthz"
```

---

## Task 2: `browser.py` — CDP connection, retry, stealth, isolated contexts

**Files:**
- Create: `playwright-service/app/browser.py`
- Create: `playwright-service/tests/conftest.py`
- Test: `playwright-service/tests/test_browser.py`

**Interfaces:**
- Consumes: nothing from Task 1 (this module is standalone; `main.py` will consume it in Task 4).
- Produces: `async def connect_with_retry(cdp_url: str, *, attempts: int = 10, delay_sec: float = 1.0) -> Tuple[Playwright, Browser]` and `isolated_page(browser: Browser) -> AsyncContextManager[Page]` (async context manager yielding a stealth-configured `Page` in a fresh `BrowserContext`, closing that context on exit). Both are imported by `app/actions.py` (Task 3) and `app/main.py` (Task 4).

- [ ] **Step 1: Write the shared local-HTTP-server test fixture**

`playwright-service/tests/conftest.py`:

```python
"""Shared test fixtures: an isolated local HTTP server for browser integration tests.

Used by test_browser.py and test_actions.py to serve fixture HTML/assets
without any real network access, and to observe which paths were actually
requested (e.g. to prove blocked resource types never reached the server).
"""
import http.server
import threading

import pytest


class _RoutedHandler(http.server.BaseHTTPRequestHandler):
    routes = {}
    request_log = []

    def do_GET(self):
        type(self).request_log.append(self.path)
        body = type(self).routes.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        content_type = "image/png" if self.path.endswith(".png") else (
            "text/css" if self.path.endswith(".css") else "text/html"
        )
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # keep test output quiet


class LocalHttpServer:
    def __init__(self, routes, request_log, base_url):
        self.routes = routes
        self.request_log = request_log
        self.base_url = base_url


@pytest.fixture
def local_http_server():
    """Serve routes registered on `.routes` for the test's duration.

    Usage:
        local_http_server.routes["/page.html"] = b"<html>...</html>"
        url = f"{local_http_server.base_url}/page.html"
        ...
        assert "/blocked.png" not in local_http_server.request_log
    """
    handler_cls = type("_TestHandler", (_RoutedHandler,), {"routes": {}, "request_log": []})
    server = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    handle = LocalHttpServer(
        routes=handler_cls.routes,
        request_log=handler_cls.request_log,
        base_url=f"http://127.0.0.1:{port}",
    )
    yield handle
    server.shutdown()
    thread.join()
```

- [ ] **Step 2: Write the failing tests for `connect_with_retry`**

`playwright-service/tests/test_browser.py`:

```python
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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd /home/ubuntu/workspace/newsgrab/playwright-service && python -m pytest tests/test_browser.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.browser'`

- [ ] **Step 4: Ensure Playwright's own bundled Chromium is installed for tests**

Run: `cd /home/ubuntu/workspace/newsgrab/playwright-service && python -m playwright install chromium`
Expected: downloads Playwright's bundled Chromium (this is a TEST-time dependency only — production's Dockerfile in Task 5 uses the system-installed `chromium` package instead and never calls `playwright install`).

- [ ] **Step 5: Write `app/browser.py`**

```python
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
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd /home/ubuntu/workspace/newsgrab/playwright-service && python -m pytest tests/test_browser.py -v`
Expected: PASS (6 tests)

- [ ] **Step 7: Commit**

```bash
cd /home/ubuntu/workspace/newsgrab
git add playwright-service/app/browser.py playwright-service/tests/conftest.py \
        playwright-service/tests/test_browser.py
git commit -m "feat: add CDP connection retry, stealth config, and isolated page contexts"
```

---

## Task 3: `actions.py` — action registry and `resolve_and_render`

**Files:**
- Create: `playwright-service/app/actions.py`
- Test: `playwright-service/tests/test_actions.py`

**Interfaces:**
- Consumes: `isolated_page(browser: Browser) -> AsyncContextManager[Page]` from `app/browser.py` (Task 2); `local_http_server` fixture from `tests/conftest.py` (Task 2).
- Produces: `ACTIONS: Dict[str, ActionHandler]` (registry, currently `{"resolve_and_render": resolve_and_render}`), `async def execute_action(browser: Browser, action: str, params: dict) -> dict` (raises `KeyError` for an unregistered action, or whatever the handler itself raises on failure — `app/main.py` in Task 4 is responsible for turning both into an `ActionResponse` error).

- [ ] **Step 1: Write the failing tests**

`playwright-service/tests/test_actions.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/ubuntu/workspace/newsgrab/playwright-service && python -m pytest tests/test_actions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.actions'`

- [ ] **Step 3: Write `app/actions.py`**

```python
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
    """
    url = params["url"]
    timeout_ms = int(params.get("timeout_ms", 30000))
    leave_prefix = params.get("leave_prefix")

    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

    if leave_prefix:
        deadline = time.monotonic() + (timeout_ms / 1000)
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/ubuntu/workspace/newsgrab/playwright-service && python -m pytest tests/test_actions.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/workspace/newsgrab
git add playwright-service/app/actions.py playwright-service/tests/test_actions.py
git commit -m "feat: add resolve_and_render action and action registry"
```

---

## Task 4: Wire `POST /v1/actions` and the real startup/shutdown lifecycle

**Files:**
- Modify: `playwright-service/app/main.py`
- Test: `playwright-service/tests/test_main.py` (append to the file from Task 1)

**Interfaces:**
- Consumes: `connect_with_retry` (`app/browser.py`, Task 2); `ACTIONS`, `execute_action` (`app/actions.py`, Task 3); `ActionRequest`, `ActionResponse` (`app/schemas.py`, Task 1); `get_browser` (already defined in `app/main.py`, Task 1 — do not change its signature).
- Produces: `POST /v1/actions` endpoint; a `lifespan` context manager wired into the `FastAPI(...)` constructor that connects on startup and disconnects on shutdown, reading the CDP URL from the `PLAYWRIGHT_CDP_URL` environment variable (default `http://localhost:9222`).

- [ ] **Step 1: Write the failing tests**

Append to `playwright-service/tests/test_main.py`:

```python
from unittest.mock import AsyncMock


def test_run_action_unknown_action_returns_error_not_500():
    app.dependency_overrides[get_browser] = lambda: MagicMock()

    client = TestClient(app)
    response = client.post("/v1/actions", json={"action": "nope", "params": {}})

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert "nope" in body["error"]
    app.dependency_overrides.clear()


def test_run_action_success(monkeypatch):
    app.dependency_overrides[get_browser] = lambda: MagicMock()

    async def fake_execute_action(browser, action, params):
        assert action == "resolve_and_render"
        assert params == {"url": "https://example.com"}
        return {"final_url": "https://example.com/x", "html": "<html></html>"}

    monkeypatch.setattr("app.main.execute_action", fake_execute_action)

    client = TestClient(app)
    response = client.post(
        "/v1/actions",
        json={"action": "resolve_and_render", "params": {"url": "https://example.com"}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["result"] == {"final_url": "https://example.com/x", "html": "<html></html>"}
    assert body["error"] is None
    app.dependency_overrides.clear()


def test_run_action_execution_failure_returns_error_not_500(monkeypatch):
    app.dependency_overrides[get_browser] = lambda: MagicMock()

    async def failing_execute_action(browser, action, params):
        raise RuntimeError("navigation timed out")

    monkeypatch.setattr("app.main.execute_action", failing_execute_action)

    client = TestClient(app)
    response = client.post(
        "/v1/actions",
        json={"action": "resolve_and_render", "params": {"url": "https://example.com"}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert "navigation timed out" in body["error"]
    app.dependency_overrides.clear()


async def test_lifespan_connects_and_disconnects_browser(monkeypatch):
    fake_playwright = AsyncMock()
    fake_browser = AsyncMock()
    connect_calls = {}

    async def fake_connect_with_retry(cdp_url, **kwargs):
        connect_calls["cdp_url"] = cdp_url
        return fake_playwright, fake_browser

    monkeypatch.setattr("app.main.connect_with_retry", fake_connect_with_retry)
    monkeypatch.setenv("PLAYWRIGHT_CDP_URL", "http://localhost:9222")

    with TestClient(app) as client:
        assert connect_calls["cdp_url"] == "http://localhost:9222"
        assert client.app.state.browser is fake_browser

    fake_browser.close.assert_awaited_once()
    fake_playwright.stop.assert_awaited_once()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/ubuntu/workspace/newsgrab/playwright-service && python -m pytest tests/test_main.py -v`
Expected: FAIL — the three `/v1/actions` tests fail with 404 (route doesn't exist yet), and the lifespan test fails because `app.main` doesn't yet define `connect_with_retry`/a real lifespan.

- [ ] **Step 3: Rewrite `app/main.py`**

```python
"""FastAPI app: health check + the /v1/actions dispatch endpoint.

Startup connects to the Chromium already running in this container (started
by entrypoint.sh with CDP on localhost:9222); shutdown closes that
connection cleanly. Tests never run this real lifespan directly except in
test_lifespan_connects_and_disconnects_browser, which monkeypatches
connect_with_retry so no real browser is needed.
"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request

from app.actions import ACTIONS, execute_action
from app.browser import connect_with_retry
from app.schemas import ActionRequest, ActionResponse, HealthResponse

logger = logging.getLogger(__name__)

CDP_URL_ENV_VAR = "PLAYWRIGHT_CDP_URL"
DEFAULT_CDP_URL = "http://localhost:9222"


@asynccontextmanager
async def lifespan(app: FastAPI):
    cdp_url = os.environ.get(CDP_URL_ENV_VAR, DEFAULT_CDP_URL)
    playwright, browser = await connect_with_retry(cdp_url)
    app.state.playwright = playwright
    app.state.browser = browser
    logger.info("[main] connected to Chromium at %s", cdp_url)
    try:
        yield
    finally:
        await app.state.browser.close()
        await app.state.playwright.stop()
        logger.info("[main] browser connection closed")


app = FastAPI(title="newsgrab playwright-service", lifespan=lifespan)


def get_browser(request: Request):
    """Return the connected Browser instance, or None before startup completes.

    Tests override this dependency directly (app.dependency_overrides)
    instead of running the real Playwright/CDP startup sequence.
    """
    return getattr(request.app.state, "browser", None)


@app.get("/healthz", response_model=HealthResponse)
async def healthz(browser=Depends(get_browser)) -> HealthResponse:
    connected = bool(browser and browser.is_connected())
    return HealthResponse(status="ok" if connected else "degraded", browser_connected=connected)


@app.post("/v1/actions", response_model=ActionResponse)
async def run_action(payload: ActionRequest, browser=Depends(get_browser)) -> ActionResponse:
    if payload.action not in ACTIONS:
        return ActionResponse(success=False, error=f"unknown action: {payload.action}")
    try:
        result = await execute_action(browser, payload.action, payload.params)
    except Exception as exc:
        logger.warning("[main] action %s failed: %s", payload.action, exc)
        return ActionResponse(success=False, error=str(exc))
    return ActionResponse(success=True, result=result)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/ubuntu/workspace/newsgrab/playwright-service && python -m pytest tests/test_main.py -v`
Expected: PASS (7 tests: 3 `/healthz` tests from Task 1 + 3 new `/v1/actions` tests + the lifespan test added in this task)

- [ ] **Step 5: Run the full test suite**

Run: `cd /home/ubuntu/workspace/newsgrab/playwright-service && python -m pytest tests/ -v`
Expected: PASS (all tests across test_main.py, test_browser.py, test_actions.py)

- [ ] **Step 6: Commit**

```bash
cd /home/ubuntu/workspace/newsgrab
git add playwright-service/app/main.py playwright-service/tests/test_main.py
git commit -m "feat: wire POST /v1/actions and real Chromium startup/shutdown lifecycle"
```

---

## Task 5: Dockerfile + entrypoint.sh

**Files:**
- Create: `playwright-service/Dockerfile`
- Create: `playwright-service/entrypoint.sh`

**Interfaces:**
- Consumes: `app/main.py`'s `app` object (Task 4, run via `uvicorn app.main:app`); the exact Chrome flags and CDP port from this plan's Global Constraints section.
- Produces: a buildable Docker image exposing port `8000` (the FastAPI app), with Chromium's CDP port `9222` reachable only from `localhost` inside the container (never `EXPOSE`d).

- [ ] **Step 1: Write `entrypoint.sh`**

```bash
#!/bin/sh
set -eu

Xvfb :99 -screen 0 1920x1080x24 &

chromium \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-data \
  --disable-blink-features=AutomationControlled \
  --disable-background-timer-throttling \
  --disable-backgrounding-occluded-windows \
  --disable-renderer-backgrounding \
  --no-sandbox \
  --disable-dev-shm-usage \
  --disable-web-security \
  --mute-audio \
  --disable-features=IsolateOrigins,site-per-process \
  about:blank &

echo "Waiting for Chromium CDP endpoint on :9222..."
i=1
while [ "$i" -le 30 ]; do
  if curl -sf "http://localhost:9222/json/version" > /dev/null 2>&1; then
    echo "Chromium CDP is ready"
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo "Chromium CDP did not become ready within 30s" >&2
    exit 1
  fi
  i=$((i + 1))
  sleep 1
done

exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x /home/ubuntu/workspace/newsgrab/playwright-service/entrypoint.sh
```

- [ ] **Step 3: Write `Dockerfile`**

```dockerfile
# Single-container browser automation service: Xvfb + system Chromium (CDP)
# + FastAPI (Playwright client connecting to that same-container Chromium).
# No `playwright install` here -- we never launch Playwright's own bundled
# browser in production, only connect_over_cdp to the system Chromium below.
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99 \
    PYTHONUNBUFFERED=1

# Set to "false" to install OS/Python packages from the default upstream
# sources instead of the Tsinghua mirrors (useful outside mainland-China
# network paths). Declared right before its first use so it never sits
# between FROM and the (expensive, rarely-changing) apt-get layer below --
# an ARG inserted earlier in the file busts every later layer's cache even
# if that layer doesn't reference the ARG at all.
ARG USE_MIRROR=true

RUN set -eux; \
    if [ "$USE_MIRROR" = "true" ]; then \
        rm -f /etc/apt/sources.list.d/debian.sources; \
        printf '%s\n' \
            "deb http://mirrors.tuna.tsinghua.edu.cn/debian/ bookworm main contrib non-free non-free-firmware" \
            "deb http://mirrors.tuna.tsinghua.edu.cn/debian/ bookworm-updates main contrib non-free non-free-firmware" \
            "deb http://mirrors.tuna.tsinghua.edu.cn/debian-security/ bookworm-security main contrib non-free non-free-firmware" \
            > /etc/apt/sources.list; \
    fi; \
    apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        chromium \
        xvfb \
        python3 \
        python3-pip \
        tini \
        curl \
        fonts-noto-cjk \
        fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /tmp/chrome-data

WORKDIR /app

COPY pyproject.toml .
COPY app /app/app
RUN set -eux; \
    if [ "$USE_MIRROR" = "true" ]; then \
        pip install --break-system-packages --no-cache-dir --timeout 180 --retries 5 \
            --index-url https://pypi.tuna.tsinghua.edu.cn/simple .; \
    else \
        pip install --break-system-packages --no-cache-dir --timeout 180 --retries 5 .; \
    fi

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Only the FastAPI port is ever exposed -- CDP (9222) stays on localhost,
# reachable only from the FastAPI process inside this same container.
EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
```

- [ ] **Step 4: Build the image**

Run: `cd /home/ubuntu/workspace/newsgrab/playwright-service && docker build -t newsgrab-playwright-service .`
Expected: build completes without error.

- [ ] **Step 5: Manually run the container and smoke-test it**

```bash
docker run --rm -d --name playwright-service-smoketest -p 18000:8000 newsgrab-playwright-service
sleep 5
curl -s http://localhost:18000/healthz
```

Expected: `{"status":"ok","browser_connected":true}`. Then:

```bash
curl -s -X POST http://localhost:18000/v1/actions \
  -H "Content-Type: application/json" \
  -d '{"action": "resolve_and_render", "params": {"url": "https://example.com", "timeout_ms": 10000}}'
```

Expected: `{"success":true,"result":{"final_url":"https://example.com/","html":"..."},"error":null}` (real network access required for this manual check — note in your report if this environment has no outbound internet access, and rely on Task 3/4's automated tests as the primary verification instead).

```bash
docker stop playwright-service-smoketest
```

- [ ] **Step 6: Commit**

```bash
cd /home/ubuntu/workspace/newsgrab
git add playwright-service/Dockerfile playwright-service/entrypoint.sh
git commit -m "feat: add playwright-service Dockerfile and Chromium startup entrypoint"
```

---

## Task 6: Root `docker-compose.yml` and READMEs

**Files:**
- Create: `docker-compose.yml` (repo root)
- Create: `playwright-service/README.md`
- Create: `README.md` (repo root)

**Interfaces:**
- Consumes: `playwright-service/Dockerfile` (Task 5).
- Produces: nothing consumed by later tasks (this is the final task of this plan — `collector-service`'s own future plan will extend `docker-compose.yml` with a second service and reference this README's API contract).

- [ ] **Step 1: Write the root `docker-compose.yml`**

```yaml
name: newsgrab

services:
  playwright-service:
    build:
      context: ./playwright-service
    container_name: newsgrab-playwright-service
    restart: unless-stopped
    networks:
      - newsgrab-internal
    # No ports published to the host by default -- only reachable from
    # other containers on newsgrab-internal. Uncomment for local debugging:
    # ports:
    #   - "18000:8000"

networks:
  newsgrab-internal:
    driver: bridge
```

- [ ] **Step 2: Verify compose config parses**

Run: `cd /home/ubuntu/workspace/newsgrab && docker compose config --quiet`
Expected: no output, exit code 0.

- [ ] **Step 3: Write `playwright-service/README.md`**

```markdown
# playwright-service

Stealth-configured browser automation service for `newsgrab`. Single
long-running container: Xvfb + system Chromium (CDP-enabled) + a FastAPI
app that drives it via Playwright, over an extensible action-dispatch API.

## Why not Browserless

Browserless's licensing terms have shifted over time and are worth
re-checking before depending on it; this service is a from-scratch
Playwright wrapper instead (Playwright itself is Apache-2.0). The
stealth/anti-detection configuration is ported from a separately verified
project (`newshub`'s `newsbot/browser_bot.py`), not from Browserless.

## API

`POST /v1/actions`

```json
{"action": "resolve_and_render", "params": {"url": "https://...", "timeout_ms": 30000, "leave_prefix": "/rss/articles"}}
```

Response:

```json
{"success": true, "result": {"final_url": "https://...", "html": "..."}, "error": null}
```

`success` is `false` with a populated `error` string on any failure (unknown
action, navigation timeout, page crash, etc.) -- the HTTP status is always
200 for a well-formed request; failures never surface as a 4xx/5xx from
action execution.

`GET /healthz` returns `{"status": "ok"|"degraded", "browser_connected": bool}`.

## Adding a new action

Write an `async def my_action(page: Page, params: dict) -> dict` in
`app/actions.py` and register it in `ACTIONS`. No endpoint changes needed.

## No authentication

This service has no application-layer auth -- its security boundary is the
deployment's internal docker network. Do not expose it beyond a trusted
network without adding auth first.

## Running locally

```bash
pip install -e ".[dev]"
python -m playwright install chromium  # test-only; production uses system Chromium
pytest tests/ -v
```

## Running in Docker

```bash
docker compose up -d playwright-service
curl http://localhost:18000/healthz  # only if you uncommented the ports mapping in docker-compose.yml
```
```

- [ ] **Step 4: Write the root `README.md`**

```markdown
# newsgrab

A standalone, reusable content-collection service. Not tied to any single
consuming project.

## Architecture

Two containers, communicating over an internal docker network only (no
public exposure, no application-layer auth -- see each service's own
README for details):

- **`playwright-service`**: stealth-configured browser automation
  (Xvfb + Chromium + Playwright), exposed via a generic action-dispatch
  HTTP API. See `playwright-service/README.md`.
- **`collector-service`** (not yet built): pluggable content-collection
  backends (Google News first) with an async job API for callers. Calls
  `playwright-service` internally.

## Design

`docs/superpowers/specs/2026-07-26-newsgrab-design.md`

## Running

```bash
docker compose up -d
```
```

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/workspace/newsgrab
git add docker-compose.yml playwright-service/README.md README.md
git commit -m "docs: add docker-compose orchestration and READMEs for playwright-service"
```

---

## Post-Hoc Correction (found during Task 5 execution)

Task 5's original Dockerfile ran `COPY pyproject.toml .` + `RUN pip install .` *before* `COPY app /app/app`, so `pip install .` failed with `error: package directory 'app' does not exist` (setuptools' `packages = ["app"]` had nothing to find yet). Real docker build logs (`docker buildx history logs`) confirmed this after ~2.4 hours of apt package installation had already completed and cached successfully — only the final `pip install .` step was broken. Fixed by moving `COPY app /app/app` before the `pip install .` line, in both this plan and the actual `playwright-service/Dockerfile` on disk. This does cost the "app code changes don't invalidate the dependency-install layer" caching optimization, but correctness comes first, and this project's build isn't iterated on frequently enough for that caching to matter.

A second, unrelated issue surfaced on retry: this sandbox's network occasionally times out mid-download on large wheels (Playwright's is ~47MB), producing `pip._vendor.urllib3.exceptions.ReadTimeoutError`. Added `--timeout 180 --retries 5` to the `pip install` invocation for resilience against this class of transient failure.

A third issue then surfaced: a hash mismatch on a downloaded wheel (`pydantic-core`), consistent with a corrupted/truncated download over the same very slow direct-to-pythonhosted.org path (observed throughput as low as ~30 KB/s). Switched to the Tsinghua PyPI mirror (the same one used by the sibling `newshub`/`daily_stock_analysis` Dockerfiles), gated behind a `USE_MIRROR` build arg (default `true`) so the image remains buildable outside mainland-China network paths by passing `--build-arg USE_MIRROR=false`.

A fourth issue: placing that new `ARG USE_MIRROR` between `FROM` and the apt-get `RUN` layer busted Docker's cache for apt-get too (inserting any instruction earlier in the file invalidates every later layer's cache, whether or not that layer references the new instruction), forcing a ~3.5-hour redownload of the entire apt dependency chain over the same slow default `deb.debian.org` path -- on top of, not instead of, the original problem. Fixed by (1) moving `ARG USE_MIRROR` to appear immediately before its first use, right before the apt-get `RUN`, and (2) extending `USE_MIRROR` to also point apt at the Tsinghua Debian mirror (matching `newshub`'s own Dockerfile pattern), so this environment's apt installs are fast AND future Dockerfile edits below this point stop invalidating the apt layer's cache.

A fifth and sixth issue, found by verifying in a throwaway `debian:bookworm-slim` container before re-spending build time: (5) writing the mirror as `https://` failed TLS certificate verification, because this base image has no trusted CA store until `ca-certificates` itself -- one of the packages being installed -- is present; switched to plain `http://` (apt doesn't need TLS for repo authenticity, it verifies GPG signatures instead). (6) `debian:bookworm-slim` ships its default sources in the newer deb822 format at `/etc/apt/sources.list.d/debian.sources`, not the legacy `/etc/apt/sources.list` -- writing only the legacy file left apt querying BOTH the mirror and the still-present slow default, wasting most of the mirror's benefit. Fixed by `rm -f /etc/apt/sources.list.d/debian.sources` before writing the legacy-format mirror file. Verified in isolation: `apt-get update` + a real package install completed in single-digit seconds at over 1 MB/s with only the mirror queried.

Post-task-review follow-up: the pip mirror invocation originally paired `--trusted-host pypi.tuna.tsinghua.edu.cn` with its `https://` index URL, unconditionally disabling certificate verification for that host. Verified in an isolated container that the mirror's certificate is valid and pip works identically without `--trusted-host`; removed the flag.

## Self-Review Notes

- **Spec coverage:** Every playwright-service requirement from the design spec's §3 maps to a task — single bundled container (Task 5), stealth config ported from `browser_bot.py` (Task 2), per-call fresh context/no session reuse (Task 2's `isolated_page`), wide/extensible action-dispatch API (Tasks 3-4), resource blocking (Task 2), no-auth posture (documented in Global Constraints and README, Task 6), CDP bound to localhost only (Task 5's Dockerfile never `EXPOSE`s 9222).
- **Placeholder scan:** No TBD/TODO; every code step is complete and runnable. Task 5 Step 5's manual smoke test explicitly tells the executor what to do if outbound network access isn't available in their environment, rather than silently assuming it works.
- **Type consistency:** `connect_with_retry(cdp_url: str, *, attempts: int = 10, delay_sec: float = 1.0) -> Tuple[Playwright, Browser]` (Task 2) is called identically in Task 4's `lifespan`. `isolated_page(browser: Browser) -> AsyncIterator[Page]` (Task 2) is consumed identically by `execute_action` (Task 3). `ACTIONS`/`execute_action` (Task 3) are imported unchanged into `app/main.py` (Task 4). `ActionRequest`/`ActionResponse`/`HealthResponse` (Task 1) field names match every test's JSON assertions across all four tasks that touch `main.py`.
