"""HTTP client for playwright-service's action-dispatch API."""
import logging
from typing import Any, Dict, Optional

import httpx

from app.config import PLAYWRIGHT_SERVICE_URL

logger = logging.getLogger(__name__)


async def resolve_and_render(
    url: str, *, timeout_ms: int, leave_prefix: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Call playwright-service's resolve_and_render action.

    Returns {"final_url": str, "html": str} on success, or None on any
    failure (network error, non-2xx response, or the service's own
    {"success": false} envelope) -- never raises, so callers can skip
    this one link without aborting the whole collection job.
    """
    params: Dict[str, Any] = {"url": url, "timeout_ms": timeout_ms}
    if leave_prefix:
        params["leave_prefix"] = leave_prefix

    timeout_sec = (timeout_ms / 1000) + 10  # allow for HTTP overhead beyond the action's own budget
    try:
        # trust_env=False: this is an internal, same-network call to
        # playwright-service (never the public internet), so it must never
        # honor HTTP(S)_PROXY/NO_PROXY from the environment. httpx defaults
        # to trust_env=True, which makes this call's success depend on the
        # operator's NO_PROXY including "playwright-service" -- an ambient
        # shell value silently overriding docker-compose.yml's own NO_PROXY
        # default has broken this exact call in practice (proxying an
        # internal container hostname yields a 502 from the external
        # proxy). Disabling env trust here removes that failure mode
        # entirely, regardless of the deployer's shell state.
        async with httpx.AsyncClient(timeout=timeout_sec, trust_env=False) as client:
            response = await client.post(
                f"{PLAYWRIGHT_SERVICE_URL}/v1/actions",
                json={"action": "resolve_and_render", "params": params},
            )
            response.raise_for_status()
            envelope = response.json()
    except Exception as exc:
        logger.warning("[playwright_client] resolve_and_render failed for %s: %s", url, exc)
        return None

    if not envelope.get("success"):
        logger.warning(
            "[playwright_client] resolve_and_render reported failure for %s: %s",
            url, envelope.get("error"),
        )
        return None

    return envelope.get("result")
