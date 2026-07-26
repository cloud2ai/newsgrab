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
