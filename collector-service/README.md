# collector-service

Pluggable, async job-based content-collection API for `newsgrab`. Its only
backend so far is Google News: query via `gnews`, resolve redirects and
render pages by calling `playwright-service`, extract content via a
three-way fallback (GNE/trafilatura/readability-lxml), guard against SSRF,
and deduplicate via a local SQLite cache.

## API

`POST /jobs`

```json
{"backend": "google_news", "query": "贵州茅台 600519", "params": {"max_results": 10, "days": 7}}
```

Returns `201 {"job_id": "..."}` immediately; collection runs in the
background.

`GET /jobs/{job_id}`

```json
{"job_id": "...", "status": "done", "result": [{"title": "...", "content": "...", "url": "...", "source": "...", "published_date": "..."}], "error": null}
```

`status` is one of `pending`/`running`/`done`/`failed`. Unknown `job_id` →
`404`. Job state is in-memory only -- a service restart loses all jobs;
callers must re-submit and treat this as a stateless job queue, not a
durable work-tracking system.

## Adding a new backend

Write an `async def collect(query: str, **params) -> List[Dict[str, Any]]`
and register it in `app.collectors.base.COLLECTORS`. No endpoint changes
needed.

## Dedup cache

A local SQLite file (`DEDUP_CACHE_PATH`, defaults to `/data/dedup_cache.db`
in the container) remembers resolved articles for 7 days, keyed by the
resolved real URL, with a secondary index on the raw Google News redirect
link so repeated encounters of the same link skip calling
`playwright-service` entirely. This is an internal efficiency cache, not a
durable article store -- callers persist their own copy of returned
articles if they need history.

## No authentication

Same posture as `playwright-service`: no application-layer auth, security
boundary is the deployment's internal docker network.

## Running locally

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## Running in Docker

```bash
docker compose up -d
curl -X POST http://localhost:18100/jobs -H "Content-Type: application/json" \
  -d '{"backend": "google_news", "query": "your query here"}'  # only if you uncommented the ports mapping
```

## Configuration

- `GOOGLE_NEWS_LANGUAGE`/`GOOGLE_NEWS_REGION`: which Google News edition
  `gnews` queries (defaults to `en`/`US`). Set to e.g. `zh-CN`/`CN` for
  Chinese-language results -- unset means English/US results even for a
  Chinese-language query, not an error, so this is easy to forget.
- `HTTPS_PROXY`/`HTTP_PROXY` (and lowercase forms): if your deployment needs
  an egress proxy, `gnews`'s own `requests`-based feed queries honor these
  automatically -- no code change needed, just set them in the environment
  (see `playwright-service/README.md`'s proxy section for why
  `NO_PROXY`/`no_proxy` matters too, and why `playwright-service` itself
  needs an explicit flag rather than just the env var).
