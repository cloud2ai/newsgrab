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
