# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`newsgrab` is a free, self-hosted news collection service: it queries
Google News RSS for a keyword, uses a real (stealth-configured) browser to
resolve each redirect link and render the page, extracts clean article
text, and returns the results via an async HTTP job API. It is two
independent Python/FastAPI services, deployed as two Docker containers on
one internal-only Docker network, with no application-layer auth (the
network boundary *is* the security boundary — see each service's README).

- `playwright-service/` — stealth browser automation. A single
  long-running container: Xvfb + system Chromium (CDP) + FastAPI, driven
  via Playwright over a generic action-dispatch API (`POST /v1/actions`).
- `collector-service/` — the service callers actually talk to. Async job
  API (`POST /jobs`, `GET /jobs/{id}`), pluggable collector backends
  (Google News is the only one implemented), SQLite dedup cache, SSRF
  guard, three-way content extraction.

Read `README.md`, `collector-service/README.md`, and
`playwright-service/README.md` before making non-trivial changes — they
document API contracts and non-obvious design decisions (proxy handling,
why no auth, why not Browserless, etc.) that this file doesn't repeat.
Deeper background/rationale (Chinese) lives under `docs/superpowers/`.

## Commands

Each service has its own `pyproject.toml`/venv; there is no root-level
build. Run these from inside the service directory.

### collector-service

```bash
cd collector-service
pip install --break-system-packages -e ".[dev]"
pytest tests/ -v                                  # all tests
pytest tests/test_gnews_collector.py -v            # one file
pytest tests/test_gnews_collector.py::test_name -v  # one test
pytest tests/ -v -m "not integration"               # skip real-network tests
```

### playwright-service

```bash
cd playwright-service
pip install --break-system-packages -e ".[dev]"
python -m playwright install chromium   # test-only; production connects to system Chromium via CDP instead
pytest tests/ -v
pytest tests/ -v -m "not integration"   # skip tests needing a real headless Chromium
```

### Running the full stack

```bash
docker compose up -d --build
```

Neither service publishes ports to the host by default (internal Docker
network only). For local testing, add a git-ignored
`docker-compose.override.yml` with a `ports:` mapping rather than editing
`docker-compose.yml` — see the existing (git-ignored) override for the
pattern.

## Architecture

### Registry pattern used by both services

Both services expose exactly one extension point, implemented the same
way: a plain `dict[str, async callable]` that request handling looks up
by name, with no other code to touch when adding an entry.

- `playwright-service/app/actions.py`: `ACTIONS["resolve_and_render"] = ...`.
  A new action is `async def f(page: Page, params: dict) -> dict` +
  registration; `POST /v1/actions` dispatches by the `action` field.
- `collector-service/app/collectors/base.py`: `COLLECTORS["google_news"] = ...`
  (registered by importing `app.collectors.google_news`, which must be
  imported somewhere for its `COLLECTORS[...] = collect` side effect to
  run — see `app/main.py`'s imports). A new backend is
  `async def collect(query: str, **params) -> list[dict]` + registration.

### Google News collection pipeline (`collector-service/app/collectors/google_news.py`)

`POST /jobs` → `_run_job` (background task, `app/main.py`) → the
registered collector's `collect()`:

1. `gnews_collector.fetch_google_news_links` queries `gnews` for candidate
   links. It monkeypatches `gnews`'s own URL resolution
   (`gnews.gnews.process_url`, `gnews.utils.utils.resolve_url`) to keep
   the raw `news.google.com/rss/articles/...` redirect link instead of
   letting `gnews` resolve it via a blocking `requests.head()` — that call
   can stall 146–292s behind a proxy, regardless of who ultimately
   resolves the link. Resolution is done by `playwright-service` instead.
2. Dedup check by raw link (`DedupCache.get_by_raw_link`) — skip the round
   trip entirely on a cache hit.
3. `playwright_client.resolve_and_render` calls `playwright-service`'s
   `resolve_and_render` action to follow the redirect and render the page.
4. Dedup check again, now by resolved real URL (a different raw link can
   resolve to an already-seen article).
5. `url_safety.is_safe_url` — SSRF guard on the resolved URL (rejects
   private/loopback/link-local/reserved/multicast IPs and internal
   hostnames) before fetching its content.
6. `content_parser.ContentParser.parse` — runs GNE, trafilatura, and
   readability-lxml (`content_fetchers.py`) independently and keeps
   whichever returns the longest content above `MINIMAL_CONTENT_LENGTH`.
7. Cache the result (`DedupCache.remember`) and append to the job's
   article list.

A single link's failure at any stage is logged and skipped, not raised —
the job still returns partial results. The one exception: if `gnews`
returned one or more candidate links and *every one* failed, `collect()`
raises `RuntimeError` so the job is marked `failed` rather than `done`
with an empty (and misleadingly "successful") result — zero candidates
from `gnews` in the first place is a normal `done` + `[]`, not a failure.

`language`/`region` can be set per-deployment (`GOOGLE_NEWS_LANGUAGE`/
`GOOGLE_NEWS_REGION` env vars, `app/config.py`) or overridden per job via
`params.language`/`params.region`.

### Browser automation (`playwright-service/app/browser.py`)

Production connects over CDP to a system Chromium already running in the
same container (started by `entrypoint.sh` with
`--remote-debugging-port=9222` and a battery of anti-detection flags,
e.g. `--disable-blink-features=AutomationControlled`) — it does **not**
launch Playwright's own bundled browser. Tests do the opposite
(`playwright.chromium.launch`) so they don't depend on the container's CDP
setup. Every action call gets a fresh `BrowserContext` via `isolated_page`
(stealth init script + `playwright_stealth` + image/stylesheet/font
request blocking), closed on exit so no cookies/state leak between calls.
The stealth configuration is ported from a separately-verified sister
project (`newshub`'s `newsbot/browser_bot.py`), not invented from scratch —
don't casually "simplify" it without understanding why each piece is
there.

### State: what's persisted vs. not

- Job status/results (`collector-service/app/jobs.py`, `JobStore`):
  in-memory only, lost on restart, by design. It's request-lifecycle
  bookkeeping, not durable data — callers must not treat a job ID as
  long-lived.
- Dedup cache (`collector-service/app/dedup_cache.py`): SQLite, keyed by
  resolved real URL with a secondary raw-link index, 7-day TTL
  (`DEDUP_CACHE_TTL_SECONDS`). This is an internal efficiency
  optimization (avoid re-rendering/re-parsing the same story across
  overlapping queries), not an article database — `newsgrab` itself never
  persists articles for callers.

### Proxy handling

`gnews`'s outbound queries go through `requests`, which honors
`HTTP_PROXY`/`HTTPS_PROXY` automatically. Chromium does not read those env
vars the way `requests` does, so `playwright-service/entrypoint.sh`
translates whichever is set into an explicit `--proxy-server` flag. Both
services' `NO_PROXY`/`no_proxy` must include the other service's container
name (see `docker-compose.yml`'s defaults) so internal service-to-service
and health-check traffic isn't routed through an external proxy.
