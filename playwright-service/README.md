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

**Build-time mirror configuration:** The Dockerfile defaults to `USE_MIRROR=true`, which routes both `apt-get` and `pip install` through Tsinghua mirrors (`mirrors.tuna.tsinghua.edu.cn` for apt, `pypi.tuna.tsinghua.edu.cn` for pip) during the Docker build. This default was chosen for faster builds inside mainland China, as this project's development sandbox experienced very slow and unreliable direct connectivity to `deb.debian.org` and `files.pythonhosted.org`. If you are building this image in a region with good connectivity to official upstream sources, or prefer to use them directly, pass `--build-arg USE_MIRROR=false` to the build command to disable the mirrors.
