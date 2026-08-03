# newsgrab

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

**newsgrab** is a free, self-hosted news collection service. It queries
[Google News](https://news.google.com) RSS for the latest articles on any
keyword, drives a real (stealth-configured) browser to resolve and render
each article, and extracts clean article text for downstream use — search,
summarization, alerting, whatever your application needs.

It ships as two small, stateless containers, standalone and reusable —
not tied to any single consuming project.

## Why

Google News RSS gives you near-real-time headlines for free, in any
language or region, but the links it returns are redirect URLs that need a
real browser to resolve, and the resulting pages need real content
extraction (not just "grab the HTML"). newsgrab handles that pipeline once,
behind a small HTTP API, so your application layer doesn't have to.

- **Free.** No paid API, no scraping-as-a-service subscription, no API key.
  Just Google News RSS and your own compute.
- **Keyword + locale driven.** Search any keyword; language and region can
  be set per-deployment or overridden per job, for any Google
  News-supported combination (e.g. `en`/`US`, `zh-CN`/`CN`).
- **Stateless.** No article database, no accounts. Submit a job, get a
  result, store it yourself if you want it kept.
- **Containerized.** Two Docker services, communicating over an internal
  network only — drop them into any stack.

## Architecture

Two containers, communicating over an internal Docker network only (no
public exposure, no application-layer auth — see each service's own
README for details):

```
                    async job API (HTTP)
   your app  ─────────────────────────────▶  collector-service
                                                  │  │
                                query keywords ───┘  └─── resolve/render URL
                                     (gnews)               (HTTP, internal)
                                                            │
                                                            ▼
                                                    playwright-service
                                                 (Xvfb + stealth Chromium)
```

- **[`playwright-service`](playwright-service/README.md)** — stealth-
  configured browser automation (Xvfb + Chromium + Playwright) that
  navigates to a URL, waits out any client-side redirects, and returns the
  final URL and rendered HTML. Exposed via a small, generic action-dispatch
  API so it isn't hardwired to Google News.
- **[`collector-service`](collector-service/README.md)** — the
  orchestration layer callers actually talk to. Exposes an async job API,
  queries Google News for candidate links, hands each one to
  `playwright-service` for resolution, runs a three-way content-extraction
  fallback (GNE / trafilatura / readability-lxml, keeping whichever returns
  the most content), checks resolved URLs against SSRF rules before
  fetching them, and skips-and-continues on any single link's failure
  rather than failing the whole job. It also keeps a short-lived SQLite
  cache keyed by resolved article URL so the same story isn't re-rendered
  and re-parsed on every overlapping query.
- Pluggable collector backends: Google News is the first and, for now,
  only backend, but adding another one is "write a `collect()` function and
  register it" — no other code changes required.

Neither service does application-layer authentication; the security
boundary is the internal Docker network. Don't expose either service
beyond a trusted network without adding auth in front of it first.

## Quick start

```bash
git clone git@github.com:cloud2ai/newsgrab.git
cd newsgrab
docker compose up -d --build
```

Submit a collection job:

```bash
curl -X POST http://localhost:18100/jobs \
  -H "Content-Type: application/json" \
  -d '{
        "backend": "google_news",
        "query": "artificial intelligence",
        "params": {"max_results": 10, "days": 7, "language": "en", "region": "US"}
      }'
# -> {"job_id": "..."}
```

Poll for the result:

```bash
curl http://localhost:18100/jobs/<job_id>
# -> {"job_id": "...", "status": "done", "result": [
#      {"title": "...", "content": "...", "url": "...",
#       "source": "example.com", "published_date": "...",
#       "images": ["https://example.com/photos/real.jpg", ...]},
#      ...
#    ], "error": null}
```

> Neither service publishes ports to the host by default — uncomment the
> `ports:` mapping for the service you need in `docker-compose.yml`, or add
> a `docker-compose.override.yml` (git-ignored) for local-only exposure.

## Configuration

Both services are configured entirely through environment variables, passed
through in `docker-compose.yml`:

| Variable | Default | Purpose |
|---|---|---|
| `GOOGLE_NEWS_LANGUAGE` / `GOOGLE_NEWS_REGION` | `en` / `US` | Default Google News edition; overridable per job via `params.language` / `params.region` |
| `PLAYWRIGHT_SERVICE_URL` | `http://localhost:8000` | Where `collector-service` reaches `playwright-service` |
| `DEFAULT_MAX_CANDIDATES` | `20` | Default ceiling on candidate links resolved per job; overridable per job via `params.max_candidates` (see `collector-service/README.md`) |
| `HTTPS_PROXY` / `HTTP_PROXY` (+ lowercase) | unset | Optional egress proxy for both outbound Google News queries and browser navigation |
| `NO_PROXY` / `no_proxy` | `localhost,127.0.0.1,::1,playwright-service,collector-service` | Keeps internal service-to-service traffic off the proxy |

See [`collector-service/README.md`](collector-service/README.md) and
[`playwright-service/README.md`](playwright-service/README.md) for the full
list, including dedup-cache TTL and browser stealth details.

## Development

```bash
docker compose -f docker-compose.dev.yml up -d --build
```

`docker-compose.dev.yml` builds the same images as `docker-compose.yml`,
but bind-mounts each service's `app/` directory over the image and runs
uvicorn with `--reload`, so edits on the host take effect immediately --
no rebuild, no manual restart. Ports are published to the host by default
(`18000` for `playwright-service`, `18100` for `collector-service`), since
this file exists specifically for local iteration.

Found a bug or want to propose a change? Open an issue or a pull request
against this repo.

## Design doc

The original design proposal (background, architecture rationale, and
decisions on scope) lives at
[`docs/superpowers/specs/2026-07-26-newsgrab-design.md`](docs/superpowers/specs/2026-07-26-newsgrab-design.md)
(in Chinese).

## License

Apache License 2.0 — see [LICENSE](LICENSE).
