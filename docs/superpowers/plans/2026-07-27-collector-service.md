# collector-service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `collector-service`, the second `newsgrab` container: an async job-based HTTP API with a pluggable content-collection backend interface, whose first (and only, this plan) backend is Google News — querying via `gnews`, resolving redirects and rendering pages by calling `playwright-service`, extracting content via a three-way fallback, guarding against SSRF, and deduplicating via a local SQLite cache.

**Architecture:** A single FastAPI container. `POST /jobs` creates an in-memory-tracked job and runs collection via `BackgroundTasks`; `GET /jobs/{id}` polls status/result. A `Collector` protocol + registry (mirroring `playwright-service`'s `ACTIONS` pattern) lets `google_news` be the first of potentially many backends. The Google News backend calls `playwright-service` over HTTP (never resolves redirects itself), checks a two-table SQLite dedup cache before and after resolution, validates the resolved URL against SSRF, and extracts content via GNE/trafilatura/readability-lxml.

**Tech Stack:** Python, FastAPI, httpx, gnews, gne, trafilatura, readability-lxml, sqlite3 (stdlib), uvicorn, `python:3.11-slim`, tini.

## Global Constraints

- `POST /jobs` request `{"backend": str, "query": str, "params": dict = {}}` → 201 `{"job_id": str}`. Unknown backend → 400.
- `GET /jobs/{job_id}` → `{"job_id": str, "status": "pending"|"running"|"done"|"failed", "result": list|null, "error": str|null}`. Unknown job_id → 404.
- Job state is in-memory only (`JobStore`, dict + `asyncio.Lock`) — a restart loses all jobs. This is intentional, matching the design's "stateless for article data" stance; job bookkeeping is request-lifecycle scoped, not durable business data.
- `Collector` protocol: `async def collect(query: str, **params) -> List[Dict[str, Any]]`, registered in a module-level `COLLECTORS: Dict[str, Collector]` dict — mirrors `playwright-service`'s `ACTIONS` registry pattern for consistency across the two services.
- Google News backend never resolves redirects itself — it calls `playwright-service`'s `POST /v1/actions` with `action="resolve_and_render"`. `PLAYWRIGHT_SERVICE_URL` env var (default `http://localhost:8000`, set to `http://playwright-service:8000` in `docker-compose.yml`).
- The `gnews.gnews.process_url`/`gnews.utils.utils.resolve_url` monkeypatch (returning raw, unresolved links) is still required — gnews's own default resolution via blocking `requests.head()` can stall 146-292s behind a proxy regardless of who does the *real* resolution afterward.
- Dedup cache: SQLite, two tables (`articles` keyed by resolved real URL, `raw_link_index` keyed by the raw Google News redirect link mapping to a real URL), 7-day TTL governed solely by `articles.cached_at` (`raw_link_index` has no TTL of its own — it joins through `articles`).
- Content extraction: three-way fallback only — GNE, trafilatura, readability-lxml. Do **not** add `newspaper4k` (namespace-collision risk established in the abandoned `daily_stock_analysis` plugin branch).
- SSRF guard (`is_safe_url`) runs on the URL `playwright-service` resolves to, before content parsing. Never raises.
- Single-link failure (resolution failure, SSRF rejection, content-extraction failure) skips that link only — never aborts the whole job, unless *every* link fails, in which case the job is marked `failed`.
- No authentication anywhere in this service — security boundary is the deployment's internal docker network, matching `playwright-service`.
- Docker build must get the mirror/cache/TLS/deb822 details right the *first* time (see Task 8) — these were each real, separately-discovered build failures in `playwright-service` and must not be re-discovered here.

---

## Task 1: Project skeleton — schemas, JobStore, collector registry, job API

**Files:**
- Create: `collector-service/pyproject.toml`
- Create: `collector-service/app/__init__.py`
- Create: `collector-service/app/config.py`
- Create: `collector-service/app/schemas.py`
- Create: `collector-service/app/jobs.py`
- Create: `collector-service/app/collectors/__init__.py`
- Create: `collector-service/app/collectors/base.py`
- Create: `collector-service/app/main.py`
- Create: `collector-service/tests/__init__.py`
- Test: `collector-service/tests/test_jobs.py`
- Test: `collector-service/tests/test_main.py`

**Interfaces:**
- Produces: `JobStore` class with `async create(backend: str, query: str) -> Job`, `async get(job_id: str) -> Optional[Job]`, `async mark_running(job_id: str) -> None`, `async mark_done(job_id: str, result: List[Dict]) -> None`, `async mark_failed(job_id: str, error: str) -> None`; `Job` dataclass with `id, backend, query, status, created_at, result, error`; status constants `PENDING/RUNNING/DONE/FAILED` (`app/jobs.py`). `COLLECTORS: Dict[str, Collector]` registry with a stub `"echo"` entry (`app/collectors/base.py`) — later tasks add real entries, never replace this file's registry object. `app: FastAPI`, `job_store: JobStore` module-level singleton (`app/main.py`).

- [ ] **Step 1: Create the project directories**

```bash
mkdir -p /home/ubuntu/workspace/newsgrab/collector-service/app/collectors
mkdir -p /home/ubuntu/workspace/newsgrab/collector-service/tests
touch /home/ubuntu/workspace/newsgrab/collector-service/app/__init__.py
touch /home/ubuntu/workspace/newsgrab/collector-service/app/collectors/__init__.py
touch /home/ubuntu/workspace/newsgrab/collector-service/tests/__init__.py
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "collector-service"
version = "0.1.0"
description = "Pluggable async content-collection job API for newsgrab"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "httpx>=0.27.0",
    "pydantic>=2.0.0",
    "gnews>=0.4.0",
    "gne>=0.4.0",
    "trafilatura>=2.0.0",
    "readability-lxml>=0.8.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.24.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
markers = [
    "integration: tests requiring real network access (e.g. real DNS resolution)",
]

[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
packages = ["app", "app.collectors"]
```

- [ ] **Step 3: Install the project in editable/dev mode**

Run: `cd /home/ubuntu/workspace/newsgrab/collector-service && pip install --no-cache-dir -e ".[dev]"`
Expected: install succeeds for fastapi/uvicorn/httpx/pydantic/gnews/gne/trafilatura/readability-lxml/pytest/pytest-asyncio. (No `--break-system-packages` needed here — this project targets `python:3.11-slim` in production, which does not impose PEP 668 restrictions, unlike `playwright-service`'s Debian-apt-installed Python.)

- [ ] **Step 4: Write `app/config.py`**

```python
"""Environment-driven configuration for collector-service."""
import os

PLAYWRIGHT_SERVICE_URL = os.environ.get("PLAYWRIGHT_SERVICE_URL", "http://localhost:8000")
PLAYWRIGHT_RESOLVE_TIMEOUT_MS = int(os.environ.get("PLAYWRIGHT_RESOLVE_TIMEOUT_MS", "20000"))

GOOGLE_NEWS_LANGUAGE = os.environ.get("GOOGLE_NEWS_LANGUAGE", "en")
GOOGLE_NEWS_REGION = os.environ.get("GOOGLE_NEWS_REGION", "US")
EXCLUDE_NEWS_SOURCE = ["zdnet.com"]
REDUNDANT_RATE = 3
MAX_RESULTS = 20
GNEWS_FETCH_TIMEOUT = 30
MINIMAL_CONTENT_LENGTH = 200

# Relative by default so local dev/tests never need root-level filesystem
# access; the Dockerfile overrides this to an absolute /data path via env.
DEDUP_CACHE_PATH = os.environ.get("DEDUP_CACHE_PATH", "./data/dedup_cache.db")
DEDUP_CACHE_TTL_SECONDS = 7 * 24 * 3600
```

- [ ] **Step 5: Write `app/schemas.py`**

```python
"""Pydantic request/response models for collector-service's job API."""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class JobRequest(BaseModel):
    backend: str
    query: str
    params: Dict[str, Any] = {}


class JobCreatedResponse(BaseModel):
    job_id: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    result: Optional[List[Dict[str, Any]]] = None
    error: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
```

- [ ] **Step 6: Write the failing tests for `JobStore`**

`collector-service/tests/test_jobs.py`:

```python
from app.jobs import JobStore, PENDING, RUNNING, DONE, FAILED


async def test_create_returns_pending_job():
    store = JobStore()
    job = await store.create("echo", "hello")
    assert job.status == PENDING
    assert job.backend == "echo"
    assert job.query == "hello"


async def test_get_returns_none_for_unknown_id():
    store = JobStore()
    assert await store.get("nonexistent") is None


async def test_mark_running_updates_status():
    store = JobStore()
    job = await store.create("echo", "hello")
    await store.mark_running(job.id)
    fetched = await store.get(job.id)
    assert fetched.status == RUNNING


async def test_mark_done_stores_result():
    store = JobStore()
    job = await store.create("echo", "hello")
    await store.mark_done(job.id, [{"title": "x"}])
    fetched = await store.get(job.id)
    assert fetched.status == DONE
    assert fetched.result == [{"title": "x"}]


async def test_mark_failed_stores_error():
    store = JobStore()
    job = await store.create("echo", "hello")
    await store.mark_failed(job.id, "boom")
    fetched = await store.get(job.id)
    assert fetched.status == FAILED
    assert fetched.error == "boom"


async def test_jobs_are_isolated_by_id():
    store = JobStore()
    job_a = await store.create("echo", "a")
    job_b = await store.create("echo", "b")
    await store.mark_done(job_a.id, [{"title": "a"}])
    fetched_b = await store.get(job_b.id)
    assert fetched_b.status == PENDING
```

- [ ] **Step 7: Run the tests to verify they fail**

Run: `cd /home/ubuntu/workspace/newsgrab/collector-service && pytest tests/test_jobs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.jobs'`

- [ ] **Step 8: Write `app/jobs.py`**

```python
"""In-memory job bookkeeping.

Not persisted -- a restart loses all jobs, by design (this service is
stateless for article data; job status is request-lifecycle-scoped
bookkeeping, not durable business data callers should rely on surviving
a restart).
"""
import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"


@dataclass
class Job:
    id: str
    backend: str
    query: str
    status: str = PENDING
    created_at: float = field(default_factory=time.time)
    result: Optional[List[Dict[str, Any]]] = None
    error: Optional[str] = None


class JobStore:
    def __init__(self):
        self._jobs: Dict[str, Job] = {}
        self._lock = asyncio.Lock()

    async def create(self, backend: str, query: str) -> Job:
        job = Job(id=str(uuid.uuid4()), backend=backend, query=query)
        async with self._lock:
            self._jobs[job.id] = job
        return job

    async def get(self, job_id: str) -> Optional[Job]:
        async with self._lock:
            return self._jobs.get(job_id)

    async def mark_running(self, job_id: str) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.status = RUNNING

    async def mark_done(self, job_id: str, result: List[Dict[str, Any]]) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.status = DONE
                job.result = result

    async def mark_failed(self, job_id: str, error: str) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.status = FAILED
                job.error = error
```

- [ ] **Step 9: Run the `JobStore` tests to verify they pass**

Run: `cd /home/ubuntu/workspace/newsgrab/collector-service && pytest tests/test_jobs.py -v`
Expected: PASS (6 tests)

- [ ] **Step 10: Write `app/collectors/base.py`**

```python
"""Pluggable collector backend registry.

Mirrors playwright-service's ACTIONS registry pattern: a plain dict from
name to an async callable, so a new backend is "write a collect()
function, register it here" with no other endpoint changes needed.
"""
from typing import Any, Awaitable, Callable, Dict, List

Collector = Callable[..., Awaitable[List[Dict[str, Any]]]]

COLLECTORS: Dict[str, Collector] = {}


async def _echo_collect(query: str, **params: Any) -> List[Dict[str, Any]]:
    """Placeholder backend for wiring/testing the job API before a real
    backend (google_news, added in a later task) is registered."""
    return [{"title": query, "content": "", "url": "", "source": "echo", "published_date": None}]


COLLECTORS["echo"] = _echo_collect
```

- [ ] **Step 11: Write the failing tests for the job API**

`collector-service/tests/test_main.py`:

```python
import time

from fastapi.testclient import TestClient

from app.main import app


def test_healthz():
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_job_unknown_backend_returns_400():
    client = TestClient(app)
    response = client.post("/jobs", json={"backend": "nonexistent", "query": "x"})
    assert response.status_code == 400


def test_get_job_unknown_id_returns_404():
    client = TestClient(app)
    response = client.get("/jobs/nonexistent-id")
    assert response.status_code == 404


def test_create_and_poll_job_with_echo_backend():
    client = TestClient(app)
    response = client.post("/jobs", json={"backend": "echo", "query": "hello"})
    assert response.status_code == 201
    job_id = response.json()["job_id"]

    deadline = time.monotonic() + 5
    poll = None
    while time.monotonic() < deadline:
        poll = client.get(f"/jobs/{job_id}")
        assert poll.status_code == 200
        if poll.json()["status"] in {"done", "failed"}:
            break
        time.sleep(0.05)

    assert poll.json()["status"] == "done"
    assert poll.json()["result"] == [
        {"title": "hello", "content": "", "url": "", "source": "echo", "published_date": None}
    ]
```

- [ ] **Step 12: Run the tests to verify they fail**

Run: `cd /home/ubuntu/workspace/newsgrab/collector-service && pytest tests/test_main.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 13: Write `app/main.py`**

```python
"""FastAPI app: health check + the async job API (POST /jobs, GET /jobs/{id})."""
import logging

from fastapi import BackgroundTasks, FastAPI, HTTPException

from app.collectors.base import COLLECTORS
from app.jobs import JobStore
from app.schemas import HealthResponse, JobCreatedResponse, JobRequest, JobStatusResponse

logger = logging.getLogger(__name__)

app = FastAPI(title="newsgrab collector-service")
job_store = JobStore()


async def _run_job(job_id: str, backend: str, query: str, params: dict) -> None:
    await job_store.mark_running(job_id)
    try:
        collector = COLLECTORS[backend]
        result = await collector(query, **params)
    except Exception as exc:
        logger.warning("[main] job %s failed: %s", job_id, exc)
        await job_store.mark_failed(job_id, str(exc))
        return
    await job_store.mark_done(job_id, result)


@app.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    return HealthResponse(status="ok")


@app.post("/jobs", response_model=JobCreatedResponse, status_code=201)
async def create_job(payload: JobRequest, background_tasks: BackgroundTasks) -> JobCreatedResponse:
    if payload.backend not in COLLECTORS:
        raise HTTPException(status_code=400, detail=f"unknown backend: {payload.backend}")
    job = await job_store.create(payload.backend, payload.query)
    background_tasks.add_task(_run_job, job.id, payload.backend, payload.query, payload.params)
    return JobCreatedResponse(job_id=job.id)


@app.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job(job_id: str) -> JobStatusResponse:
    job = await job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return JobStatusResponse(job_id=job.id, status=job.status, result=job.result, error=job.error)
```

- [ ] **Step 14: Run the tests to verify they pass**

Run: `cd /home/ubuntu/workspace/newsgrab/collector-service && pytest tests/test_main.py -v`
Expected: PASS (4 tests)

- [ ] **Step 15: Run the full test suite**

Run: `cd /home/ubuntu/workspace/newsgrab/collector-service && pytest tests/ -v`
Expected: PASS (10 tests)

- [ ] **Step 16: Commit**

```bash
cd /home/ubuntu/workspace/newsgrab
git add collector-service/pyproject.toml collector-service/app/__init__.py \
        collector-service/app/config.py collector-service/app/schemas.py \
        collector-service/app/jobs.py collector-service/app/collectors/__init__.py \
        collector-service/app/collectors/base.py collector-service/app/main.py \
        collector-service/tests/__init__.py collector-service/tests/test_jobs.py \
        collector-service/tests/test_main.py
git commit -m "feat: scaffold collector-service with job API and collector registry"
```

---

## Task 2: `url_safety.py` — SSRF guard (ported)

**Files:**
- Create: `collector-service/app/url_safety.py`
- Test: `collector-service/tests/test_url_safety.py`

**Interfaces:**
- Produces: `is_safe_url(url: str) -> bool` — never raises. Consumed by `app/collectors/google_news.py` in Task 7.

- [ ] **Step 1: Write the failing tests**

`collector-service/tests/test_url_safety.py`:

```python
"""Tests for the SSRF guard. IP-literal cases must never trigger real DNS
resolution -- verified by asserting socket.getaddrinfo is never called."""
from unittest.mock import patch

from app.url_safety import is_safe_url


def test_public_https_url_is_safe():
    assert is_safe_url("https://example.com/article") is True


def test_private_ip_literal_is_unsafe():
    assert is_safe_url("http://10.0.0.5/x") is False


def test_localhost_hostname_is_unsafe():
    assert is_safe_url("http://localhost/x") is False


def test_loopback_ip_literal_is_unsafe():
    assert is_safe_url("http://127.0.0.1/x") is False


def test_malformed_url_without_scheme_is_unsafe():
    assert is_safe_url("not-a-url") is False


def test_url_with_credentials_is_unsafe():
    assert is_safe_url("https://user:pass@example.com/x") is False


def test_ip_literal_checks_never_trigger_dns_resolution():
    with patch("socket.getaddrinfo") as mock_getaddrinfo:
        is_safe_url("http://10.0.0.5/x")
        is_safe_url("http://127.0.0.1/x")
        mock_getaddrinfo.assert_not_called()


def test_dns_resolution_failure_is_unsafe():
    with patch("socket.getaddrinfo", side_effect=OSError("resolution failed")):
        assert is_safe_url("http://this-does-not-resolve.invalid/x") is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/ubuntu/workspace/newsgrab/collector-service && pytest tests/test_url_safety.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.url_safety'`

- [ ] **Step 3: Write `app/url_safety.py`**

```python
"""Self-contained SSRF guard for playwright-service-resolved article URLs.

Rejects (returns False) non-http(s) schemes, missing netloc, embedded
credentials, localhost/.local hostnames, and any hostname or resolved IP
address that is private/loopback/link-local/reserved/multicast/non-global.
Never raises -- callers treat this as a plain boolean gate before parsing
content from a resolved URL.
"""
import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_PRIVATE_HOSTNAMES = {"localhost", "localhost.localdomain"}


def _is_blocked_ip(ip: "ipaddress._BaseAddress") -> bool:
    return (
        not ip.is_global
        or ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
    )


def is_safe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False

    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return False
    if parsed.username or parsed.password:
        return False

    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname:
        return False
    if hostname in _PRIVATE_HOSTNAMES or hostname.endswith(".local"):
        return False

    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        ip = None

    if ip is not None:
        return not _is_blocked_ip(ip)

    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except OSError as exc:
        logger.debug("[url_safety] DNS resolution failed for %s: %s", hostname, exc)
        return False

    if not addr_infos:
        return False

    has_public_address = False
    for info in addr_infos:
        try:
            resolved_ip = ipaddress.ip_address(info[4][0])
        except (IndexError, ValueError):
            continue
        if _is_blocked_ip(resolved_ip):
            return False
        has_public_address = True

    return has_public_address
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/ubuntu/workspace/newsgrab/collector-service && pytest tests/test_url_safety.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/workspace/newsgrab
git add collector-service/app/url_safety.py collector-service/tests/test_url_safety.py
git commit -m "feat: port SSRF guard (is_safe_url) to collector-service"
```

---

## Task 3: `content_fetchers.py` + `content_parser.py` — three-way fallback (ported)

**Files:**
- Create: `collector-service/app/content_fetchers.py`
- Create: `collector-service/app/content_parser.py`
- Test: `collector-service/tests/test_content_parser.py`

**Interfaces:**
- Consumes: `config.MINIMAL_CONTENT_LENGTH` (Task 1).
- Produces: `GNEFetcher().fetch(html, url) -> Optional[dict]`, `TrafilaturaFetcher().fetch(html, url) -> Optional[dict]`, `ReadabilityFetcher().fetch(html, url) -> Optional[dict]` (each dict: `content, title, author, publish_time, images`). `ContentParser().parse(html, url) -> Optional[dict]`. Consumed by `app/collectors/google_news.py` in Task 7.

- [ ] **Step 1: Write the failing tests**

`collector-service/tests/test_content_parser.py`:

```python
from unittest.mock import patch

from app.content_parser import ContentParser

LONG_CONTENT = "word " * 60  # 300 chars, clears MINIMAL_CONTENT_LENGTH=200


def test_parse_returns_none_when_all_fetchers_fail():
    parser = ContentParser()
    with patch.object(parser.gne_fetcher, "fetch", return_value=None), \
         patch.object(parser.trafilatura_fetcher, "fetch", return_value=None), \
         patch.object(parser.readability_fetcher, "fetch", return_value=None):
        assert parser.parse("<html></html>", "https://example.com/a") is None


def test_parse_selects_longest_valid_result():
    parser = ContentParser()
    short_result = {"content": LONG_CONTENT, "title": "short", "author": "", "publish_time": "", "images": []}
    long_result = {"content": LONG_CONTENT * 3, "title": "long", "author": "", "publish_time": "", "images": []}
    with patch.object(parser.gne_fetcher, "fetch", return_value=long_result), \
         patch.object(parser.trafilatura_fetcher, "fetch", return_value=short_result), \
         patch.object(parser.readability_fetcher, "fetch", return_value=None):
        result = parser.parse("<html></html>", "https://example.com/a")

    assert result["title"] == "long"


def test_parse_skips_fetcher_that_raises():
    parser = ContentParser()
    ok_result = {"content": LONG_CONTENT, "title": "ok", "author": "", "publish_time": "", "images": []}
    with patch.object(parser.gne_fetcher, "fetch", side_effect=RuntimeError("boom")), \
         patch.object(parser.trafilatura_fetcher, "fetch", return_value=ok_result), \
         patch.object(parser.readability_fetcher, "fetch", return_value=None):
        result = parser.parse("<html></html>", "https://example.com/a")

    assert result["title"] == "ok"


def test_parse_ignores_empty_content():
    parser = ContentParser()
    empty_result = {"content": "", "title": "empty", "author": "", "publish_time": "", "images": []}
    with patch.object(parser.gne_fetcher, "fetch", return_value=empty_result), \
         patch.object(parser.trafilatura_fetcher, "fetch", return_value=None), \
         patch.object(parser.readability_fetcher, "fetch", return_value=None):
        assert parser.parse("<html></html>", "https://example.com/a") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/ubuntu/workspace/newsgrab/collector-service && pytest tests/test_content_parser.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.content_parser'`

- [ ] **Step 3: Write `app/content_fetchers.py`**

```python
"""Article content fetchers: three independent extraction strategies.

Each fetcher returns None on failure or insufficient content; ContentParser
runs all three and picks the longest valid result. Library imports are
deferred into each fetch() call so a missing optional dependency degrades
that one fetcher instead of breaking the whole service at import time.
"""
import logging
import re
from html import unescape
from html.parser import HTMLParser
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from app.config import MINIMAL_CONTENT_LENGTH

logger = logging.getLogger(__name__)


class GNEFetcher:
    def __init__(self, min_content_length: int = MINIMAL_CONTENT_LENGTH):
        self.min_content_length = min_content_length

    def fetch(self, html: str, url: str) -> Optional[Dict[str, Any]]:
        try:
            from gne import GeneralNewsExtractor
        except ImportError:
            logger.warning("[GNEFetcher] gne not installed")
            return None
        try:
            parsed = urlparse(url)
            host = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else url
            result = GeneralNewsExtractor().extract(html, host=host)
            if not result:
                return None
            content = result.get("content", "")
            if not content or len(content.strip()) < self.min_content_length:
                return None
            return {
                "content": content,
                "title": result.get("title", ""),
                "author": result.get("author", ""),
                "publish_time": result.get("publish_time", ""),
                "images": list(result.get("images", []) or []),
            }
        except Exception as exc:
            logger.debug("[GNEFetcher] failed for %s: %s", url, exc)
            return None


class TrafilaturaFetcher:
    def __init__(self, min_content_length: int = MINIMAL_CONTENT_LENGTH):
        self.min_content_length = min_content_length

    def fetch(self, html: str, url: str) -> Optional[Dict[str, Any]]:
        try:
            from trafilatura import extract, extract_metadata
        except ImportError:
            logger.warning("[TrafilaturaFetcher] trafilatura not installed")
            return None
        try:
            content = extract(html)
            if not content or len(content.strip()) < self.min_content_length:
                return None
            metadata = extract_metadata(html)
            title = author = publish_time = ""
            if isinstance(metadata, dict):
                title = metadata.get("title", "") or ""
                author = metadata.get("author", "") or ""
                date = metadata.get("date", "")
                if date:
                    publish_time = str(date)
            return {
                "content": content,
                "title": title,
                "author": author,
                "publish_time": publish_time,
                "images": [],
            }
        except Exception as exc:
            logger.debug("[TrafilaturaFetcher] failed for %s: %s", url, exc)
            return None


class ReadabilityFetcher:
    def __init__(self, min_content_length: int = MINIMAL_CONTENT_LENGTH):
        self.min_content_length = min_content_length

    def fetch(self, html: str, url: str) -> Optional[Dict[str, Any]]:
        try:
            from readability import Document
        except ImportError:
            logger.warning("[ReadabilityFetcher] readability-lxml not installed")
            return None
        try:
            doc = Document(html)
            content_html = doc.summary()
            title = doc.title()

            class _TextExtractor(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.parts = []

                def handle_data(self, data):
                    cleaned = data.strip()
                    if cleaned:
                        self.parts.append(cleaned)

            extractor = _TextExtractor()
            extractor.feed(content_html)
            content = re.sub(r"\s+", " ", " ".join(extractor.parts)).strip()
            if not content or len(content) < self.min_content_length:
                return None
            return {
                "content": content,
                "title": unescape(title) if title else "",
                "author": "",
                "publish_time": "",
                "images": [],
            }
        except Exception as exc:
            logger.debug("[ReadabilityFetcher] failed for %s: %s", url, exc)
            return None
```

- [ ] **Step 4: Write `app/content_parser.py`**

```python
"""Three-way content extraction fallback: run all fetchers, keep the longest."""
import logging
from typing import Any, Dict, Optional

from app.content_fetchers import GNEFetcher, ReadabilityFetcher, TrafilaturaFetcher

logger = logging.getLogger(__name__)


class ContentParser:
    def __init__(self):
        self.gne_fetcher = GNEFetcher()
        self.trafilatura_fetcher = TrafilaturaFetcher()
        self.readability_fetcher = ReadabilityFetcher()

    def parse(self, html: str, url: str) -> Optional[Dict[str, Any]]:
        fetchers = [
            ("GNE", lambda: self.gne_fetcher.fetch(html, url)),
            ("trafilatura", lambda: self.trafilatura_fetcher.fetch(html, url)),
            ("readability-lxml", lambda: self.readability_fetcher.fetch(html, url)),
        ]
        results = []
        for name, fn in fetchers:
            try:
                data = fn()
            except Exception as exc:
                logger.debug("[ContentParser] %s raised: %s", name, exc)
                continue
            content = (data or {}).get("content", "") if data else ""
            if data and content:
                results.append((name, data, len(content)))

        if not results:
            logger.warning("[ContentParser] all fetchers failed for %s", url)
            return None

        best_name, best_data, best_len = max(results, key=lambda item: item[2])
        logger.info(
            "[ContentParser] selected %s (%s chars) from %s candidates for %s",
            best_name, best_len, len(results), url,
        )
        return best_data
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd /home/ubuntu/workspace/newsgrab/collector-service && pytest tests/test_content_parser.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
cd /home/ubuntu/workspace/newsgrab
git add collector-service/app/content_fetchers.py collector-service/app/content_parser.py \
        collector-service/tests/test_content_parser.py
git commit -m "feat: port three-way content parser fallback to collector-service"
```

---

## Task 4: `dedup_cache.py` — SQLite two-table dedup cache

**Files:**
- Create: `collector-service/app/dedup_cache.py`
- Test: `collector-service/tests/test_dedup_cache.py`

**Interfaces:**
- Consumes: `config.DEDUP_CACHE_TTL_SECONDS` (Task 1).
- Produces: `DedupCache(db_path: str)` with `get_by_raw_link(raw_link: str) -> Optional[dict]`, `get_by_real_url(real_url: str) -> Optional[dict]`, `remember(real_url: str, raw_link: str, article: dict) -> None`, `link_raw_to_real(raw_link: str, real_url: str) -> None`. Consumed by `app/collectors/google_news.py` in Task 7.

- [ ] **Step 1: Write the failing tests**

`collector-service/tests/test_dedup_cache.py`:

```python
"""Tests for the SQLite dedup cache. Uses a real file-backed SQLite DB per
test (via tmp_path) rather than :memory:, since DedupCache opens a fresh
connection per method call -- :memory: databases don't persist across
separate connections."""
import time

import pytest

from app.dedup_cache import DedupCache


@pytest.fixture
def cache(tmp_path):
    return DedupCache(str(tmp_path / "test_cache.db"))


def test_get_by_real_url_returns_none_when_empty(cache):
    assert cache.get_by_real_url("https://example.com/a") is None


def test_get_by_raw_link_returns_none_when_empty(cache):
    assert cache.get_by_raw_link("https://news.google.com/rss/x") is None


def test_remember_and_get_by_real_url(cache):
    article = {"title": "t", "content": "c", "url": "https://example.com/a"}
    cache.remember("https://example.com/a", "https://news.google.com/rss/x", article)
    assert cache.get_by_real_url("https://example.com/a") == article


def test_remember_and_get_by_raw_link(cache):
    article = {"title": "t"}
    cache.remember("https://example.com/a", "https://news.google.com/rss/x", article)
    assert cache.get_by_raw_link("https://news.google.com/rss/x") == article


def test_link_raw_to_real_enables_fast_path_for_a_different_raw_link(cache):
    article = {"title": "t"}
    cache.remember("https://example.com/a", "https://news.google.com/rss/x", article)
    # A different raw Google News link later resolves to the same real_url:
    cache.link_raw_to_real("https://news.google.com/rss/y", "https://example.com/a")
    assert cache.get_by_raw_link("https://news.google.com/rss/y") == article


def test_expired_entry_is_not_returned_by_either_lookup(tmp_path, monkeypatch):
    import app.dedup_cache as dedup_cache_module
    monkeypatch.setattr(dedup_cache_module, "DEDUP_CACHE_TTL_SECONDS", 1)
    cache = DedupCache(str(tmp_path / "test_cache.db"))
    article = {"title": "t"}
    cache.remember("https://example.com/a", "https://news.google.com/rss/x", article)
    time.sleep(1.2)
    assert cache.get_by_real_url("https://example.com/a") is None
    assert cache.get_by_raw_link("https://news.google.com/rss/x") is None


def test_remember_overwrites_previous_entry_for_same_real_url(cache):
    cache.remember("https://example.com/a", "https://news.google.com/rss/x", {"title": "old"})
    cache.remember("https://example.com/a", "https://news.google.com/rss/x", {"title": "new"})
    assert cache.get_by_real_url("https://example.com/a") == {"title": "new"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/ubuntu/workspace/newsgrab/collector-service && pytest tests/test_dedup_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.dedup_cache'`

- [ ] **Step 3: Write `app/dedup_cache.py`**

```python
"""SQLite-backed dedup cache, keyed primarily by resolved real URL.

A secondary raw_link_index lets repeated encounters of the SAME raw
Google News redirect link skip calling playwright-service entirely
(Google's redirect links are stable per-article), while expiry is
governed solely by `articles.cached_at` -- raw_link_index has no TTL of
its own, it only joins through `articles` at read time.

Each method opens and closes its own connection rather than holding one
open for the object's lifetime, since this is called from async request
handlers via a synchronous sqlite3 API -- keeping connections short-lived
avoids any cross-request connection-sharing concerns.
"""
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Optional

from app.config import DEDUP_CACHE_TTL_SECONDS


class DedupCache:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS articles ("
                "real_url TEXT PRIMARY KEY, article_json TEXT NOT NULL, cached_at REAL NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS raw_link_index ("
                "raw_link TEXT PRIMARY KEY, real_url TEXT NOT NULL)"
            )
            conn.commit()
        finally:
            conn.close()

    def get_by_raw_link(self, raw_link: str) -> Optional[Dict[str, Any]]:
        cutoff = time.time() - DEDUP_CACHE_TTL_SECONDS
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT a.article_json FROM raw_link_index r "
                "JOIN articles a ON r.real_url = a.real_url "
                "WHERE r.raw_link = ? AND a.cached_at > ?",
                (raw_link, cutoff),
            ).fetchone()
        finally:
            conn.close()
        return json.loads(row[0]) if row else None

    def get_by_real_url(self, real_url: str) -> Optional[Dict[str, Any]]:
        cutoff = time.time() - DEDUP_CACHE_TTL_SECONDS
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT article_json FROM articles WHERE real_url = ? AND cached_at > ?",
                (real_url, cutoff),
            ).fetchone()
        finally:
            conn.close()
        return json.loads(row[0]) if row else None

    def remember(self, real_url: str, raw_link: str, article: Dict[str, Any]) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO articles (real_url, article_json, cached_at) VALUES (?, ?, ?)",
                (real_url, json.dumps(article), time.time()),
            )
            conn.execute(
                "INSERT OR REPLACE INTO raw_link_index (raw_link, real_url) VALUES (?, ?)",
                (raw_link, real_url),
            )
            conn.commit()
        finally:
            conn.close()

    def link_raw_to_real(self, raw_link: str, real_url: str) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO raw_link_index (raw_link, real_url) VALUES (?, ?)",
                (raw_link, real_url),
            )
            conn.commit()
        finally:
            conn.close()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/ubuntu/workspace/newsgrab/collector-service && pytest tests/test_dedup_cache.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/workspace/newsgrab
git add collector-service/app/dedup_cache.py collector-service/tests/test_dedup_cache.py
git commit -m "feat: add SQLite two-table dedup cache to collector-service"
```

---

## Task 5: `playwright_client.py` — HTTP client for `playwright-service`

**Files:**
- Create: `collector-service/app/playwright_client.py`
- Test: `collector-service/tests/test_playwright_client.py`

**Interfaces:**
- Consumes: `config.PLAYWRIGHT_SERVICE_URL` (Task 1).
- Produces: `async def resolve_and_render(url: str, *, timeout_ms: int, leave_prefix: Optional[str] = None) -> Optional[Dict[str, Any]]` — returns `{"final_url": str, "html": str}` on success, `None` on any failure (never raises). Consumed by `app/collectors/google_news.py` in Task 7.

- [ ] **Step 1: Write the failing tests**

`collector-service/tests/test_playwright_client.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch

from app.playwright_client import resolve_and_render


def _make_mock_client(response=None, post_side_effect=None):
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    if post_side_effect is not None:
        mock_client.post = AsyncMock(side_effect=post_side_effect)
    else:
        mock_client.post = AsyncMock(return_value=response)
    return mock_client


async def test_resolve_and_render_returns_result_on_success():
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {
        "success": True,
        "result": {"final_url": "https://example.com/a", "html": "<html></html>"},
        "error": None,
    }
    with patch("app.playwright_client.httpx.AsyncClient", return_value=_make_mock_client(fake_response)):
        result = await resolve_and_render("https://news.google.com/rss/x", timeout_ms=20000)

    assert result == {"final_url": "https://example.com/a", "html": "<html></html>"}


async def test_resolve_and_render_returns_none_on_service_reported_failure():
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {"success": False, "result": None, "error": "navigation timed out"}
    with patch("app.playwright_client.httpx.AsyncClient", return_value=_make_mock_client(fake_response)):
        result = await resolve_and_render("https://news.google.com/rss/x", timeout_ms=20000)

    assert result is None


async def test_resolve_and_render_returns_none_on_network_error():
    mock_client = _make_mock_client(post_side_effect=ConnectionError("connection refused"))
    with patch("app.playwright_client.httpx.AsyncClient", return_value=mock_client):
        result = await resolve_and_render("https://news.google.com/rss/x", timeout_ms=20000)

    assert result is None


async def test_resolve_and_render_passes_leave_prefix_when_given():
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {"success": True, "result": {"final_url": "x", "html": "y"}, "error": None}
    mock_client = _make_mock_client(fake_response)
    with patch("app.playwright_client.httpx.AsyncClient", return_value=mock_client):
        await resolve_and_render(
            "https://news.google.com/rss/x", timeout_ms=20000, leave_prefix="/rss/articles"
        )

    call_kwargs = mock_client.post.call_args.kwargs
    assert call_kwargs["json"]["params"]["leave_prefix"] == "/rss/articles"


async def test_resolve_and_render_omits_leave_prefix_when_not_given():
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {"success": True, "result": {"final_url": "x", "html": "y"}, "error": None}
    mock_client = _make_mock_client(fake_response)
    with patch("app.playwright_client.httpx.AsyncClient", return_value=mock_client):
        await resolve_and_render("https://news.google.com/rss/x", timeout_ms=20000)

    call_kwargs = mock_client.post.call_args.kwargs
    assert "leave_prefix" not in call_kwargs["json"]["params"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/ubuntu/workspace/newsgrab/collector-service && pytest tests/test_playwright_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.playwright_client'`

- [ ] **Step 3: Write `app/playwright_client.py`**

```python
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
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/ubuntu/workspace/newsgrab/collector-service && pytest tests/test_playwright_client.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/workspace/newsgrab
git add collector-service/app/playwright_client.py collector-service/tests/test_playwright_client.py
git commit -m "feat: add playwright-service HTTP client to collector-service"
```

---

## Task 6: `gnews_collector.py` — Google News link query (ported + monkeypatch)

**Files:**
- Create: `collector-service/app/gnews_collector.py`
- Test: `collector-service/tests/test_gnews_collector.py`

**Interfaces:**
- Consumes: `config.GOOGLE_NEWS_LANGUAGE`, `config.GOOGLE_NEWS_REGION`, `config.EXCLUDE_NEWS_SOURCE`, `config.REDUNDANT_RATE`, `config.MAX_RESULTS`, `config.GNEWS_FETCH_TIMEOUT` (Task 1).
- Produces: `fetch_google_news_links(keyword: str, max_results: int = 10, days: int = 7) -> List[Dict[str, Any]]`, each item `{"link": str, "title": str, "published_date": str}`. Consumed by `app/collectors/google_news.py` in Task 7 (via `asyncio.to_thread`, since this function is synchronous/blocking).

- [ ] **Step 1: Write the failing tests**

`collector-service/tests/test_gnews_collector.py`:

```python
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch


def _raw_item(days_ago: int, title: str, url: str) -> dict:
    published = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "title": title,
        "url": url,
        "published date": published.strftime("%a, %d %b %Y %H:%M:%S %Z"),
        "source": {"href": "https://source.example"},
    }


def test_fetch_google_news_links_empty_keyword_returns_empty_list():
    from app.gnews_collector import fetch_google_news_links
    assert fetch_google_news_links("") == []


def test_fetch_google_news_links_filters_by_day_window_and_strips_title_suffix():
    from app.gnews_collector import fetch_google_news_links

    fresh = _raw_item(1, "Some Headline - Example News", "https://news.google.com/rss/articles/fresh")
    stale = _raw_item(30, "Old Headline", "https://news.google.com/rss/articles/stale")

    fake_gnews = MagicMock()
    fake_gnews.get_news.return_value = [fresh, stale]

    with patch("app.gnews_collector.GNews", return_value=fake_gnews):
        links = fetch_google_news_links("贵州茅台", max_results=10, days=7)

    assert len(links) == 1
    assert links[0]["link"] == "https://news.google.com/rss/articles/fresh"
    assert links[0]["title"] == "Some Headline"


def test_fetch_google_news_links_respects_max_results():
    from app.gnews_collector import fetch_google_news_links

    items = [_raw_item(1, f"Headline {i}", f"https://news.google.com/rss/articles/{i}") for i in range(5)]
    fake_gnews = MagicMock()
    fake_gnews.get_news.return_value = items

    with patch("app.gnews_collector.GNews", return_value=fake_gnews):
        links = fetch_google_news_links("贵州茅台", max_results=2, days=7)

    assert len(links) == 2


def test_fetch_google_news_links_returns_empty_on_gnews_exception():
    from app.gnews_collector import fetch_google_news_links

    fake_gnews = MagicMock()
    fake_gnews.get_news.side_effect = RuntimeError("network down")

    with patch("app.gnews_collector.GNews", return_value=fake_gnews):
        assert fetch_google_news_links("贵州茅台") == []


def test_monkeypatch_keeps_raw_google_news_url():
    import gnews.gnews as gnews_module
    import gnews.utils.utils as gnews_utils
    import app.gnews_collector  # noqa: F401 (applies the monkeypatch)

    item = {"link": "https://news.google.com/rss/articles/xyz", "source": {"href": "https://blocked.example"}}
    assert gnews_module.process_url(item, exclude_websites=None) == "https://news.google.com/rss/articles/xyz"
    assert gnews_module.process_url(item, exclude_websites=["blocked.example"]) is None
    assert gnews_utils.resolve_url("https://news.google.com/rss/articles/xyz") == (
        "https://news.google.com/rss/articles/xyz"
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/ubuntu/workspace/newsgrab/collector-service && pytest tests/test_gnews_collector.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.gnews_collector'`

- [ ] **Step 3: Write `app/gnews_collector.py`**

```python
"""Google News link collection via the gnews package.

Ports the redirect-URL passthrough monkeypatch: gnews resolves each Google
News redirect via a blocking requests.head() call that can stall 146-292s
behind a proxy. We keep the raw news.google.com/rss/articles/... link
instead and let playwright_client.resolve_and_render resolve it via a real
browser -- this holds regardless of who does the actual resolution, since
the problem is gnews's own default behavior, not who consumes the result.
"""
import logging
import re
import socket
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from gnews import GNews
import gnews.gnews as _gnews_module
import gnews.utils.utils as _gnews_utils

from app import config

logger = logging.getLogger(__name__)

_DATE_FORMAT = "%a, %d %b %Y %H:%M:%S %Z"


def _keep_raw_url(item, exclude_websites, proxies=None):
    source_href = (item.get("source") or {}).get("href", "")
    if source_href and exclude_websites:
        for website in exclude_websites:
            if re.match(rf"^http(s)?://(www.)?{website.lower()}.*", source_href):
                return None
    return item.get("link")


_gnews_module.process_url = _keep_raw_url
_gnews_utils.resolve_url = lambda url, proxies=None: url


def _parse_published_date(published_date: str) -> Optional[datetime]:
    try:
        return datetime.strptime(published_date, _DATE_FORMAT).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def fetch_google_news_links(
    keyword: str,
    max_results: int = 10,
    days: int = 7,
) -> List[Dict[str, Any]]:
    """Query Google News for `keyword` and return raw links (no content).

    Synchronous/blocking (gnews uses `requests` internally) -- callers in
    async code must wrap this in `asyncio.to_thread()`.

    Returns a list of {link, title, published_date} dicts, filtered to
    articles published within the last `days` days. Returns an empty list
    on any gnews failure -- callers should treat this as "no links found".
    """
    if not keyword or not keyword.strip():
        return []

    previous_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(config.GNEWS_FETCH_TIMEOUT)
    try:
        g = GNews(
            language=config.GOOGLE_NEWS_LANGUAGE,
            country=config.GOOGLE_NEWS_REGION,
            max_results=max(max_results * config.REDUNDANT_RATE, config.MAX_RESULTS),
            exclude_websites=config.EXCLUDE_NEWS_SOURCE,
        )
        raw_items = g.get_news(keyword) or []
    except Exception as exc:
        logger.warning("[gnews_collector] gnews query failed for %r: %s", keyword, exc)
        return []
    finally:
        socket.setdefaulttimeout(previous_timeout)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    links: List[Dict[str, Any]] = []
    for item in raw_items:
        published = _parse_published_date(item.get("published date", ""))
        if published is not None and published < cutoff:
            continue
        title = item.get("title", "")
        idx = title.rfind("-")
        if idx > 0:
            title = title[:idx].strip()
        links.append({
            "link": item.get("url", ""),
            "title": title,
            "published_date": item.get("published date", ""),
        })
        if len(links) >= max_results:
            break

    logger.info("[gnews_collector] %r -> %s links", keyword, len(links))
    return links
```

- [ ] **Step 4: Ensure `gnews` is importable for the tests**

Run: `cd /home/ubuntu/workspace/newsgrab/collector-service && python3 -c "import gnews"`
Expected: no output (already installed via Task 1's `pip install -e ".[dev]"`).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd /home/ubuntu/workspace/newsgrab/collector-service && pytest tests/test_gnews_collector.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
cd /home/ubuntu/workspace/newsgrab
git add collector-service/app/gnews_collector.py collector-service/tests/test_gnews_collector.py
git commit -m "feat: port gnews link collector with redirect passthrough monkeypatch"
```

---

## Task 7: `collectors/google_news.py` — orchestration

**Files:**
- Create: `collector-service/app/collectors/google_news.py`
- Test: `collector-service/tests/test_google_news_collector.py`

**Interfaces:**
- Consumes: `COLLECTORS` registry (Task 1, `app/collectors/base.py`); `fetch_google_news_links` (Task 6); `DedupCache` (Task 4); `resolve_and_render` (Task 5); `is_safe_url` (Task 2); `ContentParser` (Task 3); `config.DEDUP_CACHE_PATH`, `config.PLAYWRIGHT_RESOLVE_TIMEOUT_MS` (Task 1).
- Produces: `async def collect(query: str, **params) -> List[Dict[str, Any]]`, registered as `COLLECTORS["google_news"]`. This is the function `app/main.py`'s job runner (Task 1) invokes for `backend="google_news"` requests — no changes needed to `main.py` itself, since it already looks up `COLLECTORS` by name.

- [ ] **Step 1: Write the failing tests**

`collector-service/tests/test_google_news_collector.py`:

```python
"""Orchestration tests: every dependency is mocked, so this only verifies
the sequencing/skip logic in collect() itself -- each dependency's own
real behavior is covered by its own task's tests."""
from unittest.mock import AsyncMock, MagicMock, patch

import app.collectors.google_news as google_news_module


def _patch_dedup_cache(no_hits: bool = True):
    mock_cache = MagicMock()
    if no_hits:
        mock_cache.get_by_raw_link.return_value = None
        mock_cache.get_by_real_url.return_value = None
    return patch.object(google_news_module, "_get_dedup_cache", return_value=mock_cache), mock_cache


async def test_collect_uses_raw_link_cache_hit_without_calling_playwright():
    cached_article = {"title": "cached"}
    cache_patch, mock_cache = _patch_dedup_cache(no_hits=False)
    mock_cache.get_by_raw_link.return_value = cached_article

    with patch.object(google_news_module, "fetch_google_news_links", return_value=[
        {"link": "https://news.google.com/rss/1", "title": "t1", "published_date": "d1"}
    ]), cache_patch, \
         patch.object(google_news_module, "resolve_and_render", new=AsyncMock()) as mock_resolve:
        result = await google_news_module.collect("贵州茅台")

    assert result == [cached_article]
    mock_resolve.assert_not_called()


async def test_collect_skips_link_that_fails_to_resolve():
    cache_patch, mock_cache = _patch_dedup_cache()

    with patch.object(google_news_module, "fetch_google_news_links", return_value=[
        {"link": "https://news.google.com/rss/1", "title": "t1", "published_date": "d1"}
    ]), cache_patch, \
         patch.object(google_news_module, "resolve_and_render", new=AsyncMock(return_value=None)):
        result = await google_news_module.collect("贵州茅台")

    assert result == []


async def test_collect_skips_link_rejected_by_ssrf_check():
    cache_patch, mock_cache = _patch_dedup_cache()

    with patch.object(google_news_module, "fetch_google_news_links", return_value=[
        {"link": "https://news.google.com/rss/1", "title": "t1", "published_date": "d1"}
    ]), cache_patch, \
         patch.object(google_news_module, "resolve_and_render", new=AsyncMock(return_value={
             "final_url": "http://10.0.0.5/internal", "html": "<html></html>",
         })), \
         patch.object(google_news_module, "is_safe_url", return_value=False):
        result = await google_news_module.collect("贵州茅台")

    assert result == []


async def test_collect_skips_link_when_content_extraction_fails():
    cache_patch, mock_cache = _patch_dedup_cache()

    with patch.object(google_news_module, "fetch_google_news_links", return_value=[
        {"link": "https://news.google.com/rss/1", "title": "t1", "published_date": "d1"}
    ]), cache_patch, \
         patch.object(google_news_module, "resolve_and_render", new=AsyncMock(return_value={
             "final_url": "https://real-site.example/a", "html": "<html></html>",
         })), \
         patch.object(google_news_module, "is_safe_url", return_value=True), \
         patch.object(google_news_module, "_get_content_parser") as mock_get_parser:
        mock_get_parser.return_value.parse.return_value = None
        result = await google_news_module.collect("贵州茅台")

    assert result == []


async def test_collect_full_pipeline_success_caches_the_article():
    cache_patch, mock_cache = _patch_dedup_cache()

    with patch.object(google_news_module, "fetch_google_news_links", return_value=[
        {"link": "https://news.google.com/rss/1", "title": "t1", "published_date": "d1"}
    ]), cache_patch, \
         patch.object(google_news_module, "resolve_and_render", new=AsyncMock(return_value={
             "final_url": "https://real-site.example/a", "html": "<html>content</html>",
         })), \
         patch.object(google_news_module, "is_safe_url", return_value=True), \
         patch.object(google_news_module, "_get_content_parser") as mock_get_parser:
        mock_get_parser.return_value.parse.return_value = {"title": "Real Title", "content": "full body text"}
        result = await google_news_module.collect("贵州茅台")

    assert len(result) == 1
    assert result[0]["title"] == "Real Title"
    assert result[0]["url"] == "https://real-site.example/a"
    assert result[0]["source"] == "real-site.example"
    mock_cache.remember.assert_called_once_with(
        "https://real-site.example/a", "https://news.google.com/rss/1", result[0]
    )


async def test_collect_real_url_cache_hit_links_raw_url_without_reparsing():
    cached_article = {"title": "already cached"}
    cache_patch, mock_cache = _patch_dedup_cache()
    mock_cache.get_by_real_url.return_value = cached_article

    with patch.object(google_news_module, "fetch_google_news_links", return_value=[
        {"link": "https://news.google.com/rss/1", "title": "t1", "published_date": "d1"}
    ]), cache_patch, \
         patch.object(google_news_module, "resolve_and_render", new=AsyncMock(return_value={
             "final_url": "https://real-site.example/a", "html": "<html></html>",
         })), \
         patch.object(google_news_module, "_get_content_parser") as mock_get_parser:
        result = await google_news_module.collect("贵州茅台")

    assert result == [cached_article]
    mock_cache.link_raw_to_real.assert_called_once_with(
        "https://news.google.com/rss/1", "https://real-site.example/a"
    )
    mock_get_parser.return_value.parse.assert_not_called()


def test_google_news_registered_in_collectors():
    from app.collectors.base import COLLECTORS
    assert "google_news" in COLLECTORS
    assert COLLECTORS["google_news"] is google_news_module.collect
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/ubuntu/workspace/newsgrab/collector-service && pytest tests/test_google_news_collector.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.collectors.google_news'`

- [ ] **Step 3: Write `app/collectors/google_news.py`**

```python
"""Google News collection backend: orchestrates gnews query, playwright-
service resolution, dedup cache, SSRF check, and content extraction.

The dedup cache and content parser are constructed lazily (not at module
import time) so importing this module in a test never touches the
filesystem -- tests patch `_get_dedup_cache`/`_get_content_parser` instead
of constructing real instances.
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from app.collectors.base import COLLECTORS
from app.config import DEDUP_CACHE_PATH, PLAYWRIGHT_RESOLVE_TIMEOUT_MS
from app.content_parser import ContentParser
from app.dedup_cache import DedupCache
from app.gnews_collector import fetch_google_news_links
from app.playwright_client import resolve_and_render
from app.url_safety import is_safe_url

logger = logging.getLogger(__name__)

# Google News redirect links look like news.google.com/rss/articles/...;
# this substring must disappear from the URL before the client-side JS
# redirect is considered settled.
_GOOGLE_NEWS_LEAVE_PREFIX = "/rss/articles"

_dedup_cache: Optional[DedupCache] = None
_content_parser: Optional[ContentParser] = None


def _get_dedup_cache() -> DedupCache:
    global _dedup_cache
    if _dedup_cache is None:
        _dedup_cache = DedupCache(DEDUP_CACHE_PATH)
    return _dedup_cache


def _get_content_parser() -> ContentParser:
    global _content_parser
    if _content_parser is None:
        _content_parser = ContentParser()
    return _content_parser


async def collect(query: str, **params: Any) -> List[Dict[str, Any]]:
    max_results = int(params.get("max_results", 10))
    days = int(params.get("days", 7))

    links = await asyncio.to_thread(fetch_google_news_links, query, max_results=max_results, days=days)

    dedup_cache = _get_dedup_cache()
    articles: List[Dict[str, Any]] = []

    for link in links:
        raw_link = link["link"]

        cached = dedup_cache.get_by_raw_link(raw_link)
        if cached:
            articles.append(cached)
            continue

        resolved = await resolve_and_render(
            raw_link, timeout_ms=PLAYWRIGHT_RESOLVE_TIMEOUT_MS, leave_prefix=_GOOGLE_NEWS_LEAVE_PREFIX
        )
        if resolved is None:
            continue
        real_url = resolved["final_url"]
        html = resolved["html"]

        cached_by_real_url = dedup_cache.get_by_real_url(real_url)
        if cached_by_real_url:
            dedup_cache.link_raw_to_real(raw_link, real_url)
            articles.append(cached_by_real_url)
            continue

        if not is_safe_url(real_url):
            continue

        parsed = _get_content_parser().parse(html, real_url)
        if not parsed or not parsed.get("content"):
            continue

        article = {
            "title": parsed.get("title") or link.get("title", ""),
            "content": parsed.get("content", ""),
            "url": real_url,
            "source": urlparse(real_url).netloc,
            "published_date": link.get("published_date"),
        }
        dedup_cache.remember(real_url, raw_link, article)
        articles.append(article)

    return articles


COLLECTORS["google_news"] = collect
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/ubuntu/workspace/newsgrab/collector-service && pytest tests/test_google_news_collector.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Run the full test suite**

Run: `cd /home/ubuntu/workspace/newsgrab/collector-service && pytest tests/ -v`
Expected: PASS (all tests across all task test files)

- [ ] **Step 6: Commit**

```bash
cd /home/ubuntu/workspace/newsgrab
git add collector-service/app/collectors/google_news.py collector-service/tests/test_google_news_collector.py
git commit -m "feat: add Google News collector orchestration to collector-service"
```

---

## Task 8: Dockerfile + entrypoint.sh

**Files:**
- Create: `collector-service/Dockerfile`
- Create: `collector-service/entrypoint.sh`
- Create: `collector-service/.dockerignore`

**Interfaces:**
- Consumes: `app/main.py`'s `app` object (run via `uvicorn app.main:app`).
- Produces: a buildable Docker image exposing port `8000`.

Get this right the first time -- apply every lesson already paid for in `playwright-service`'s Dockerfile (see this plan's Global Constraints and `docs/superpowers/plans/2026-07-26-playwright-service.md`'s "Post-Hoc Correction"/"Post-task-review follow-up" sections for the full incident history): `COPY app/` before `pip install .`; `ARG USE_MIRROR` declared immediately before its first use, not right after `FROM`; apt mirror as `http://` (not `https://`, no CA trust store exists pre-`ca-certificates`); remove `/etc/apt/sources.list.d/debian.sources` before writing the legacy-format mirror file (this base image ships deb822-format sources too -- confirmed by inspection, same as `playwright-service`'s base); pip mirror with `--index-url` only, no `--trusted-host` (the mirror's certificate is valid). One difference from `playwright-service`: this image does **not** need `--break-system-packages` for pip (verified: `python:3.11-slim`'s Python is not the Debian-apt-installed one, so PEP 668's externally-managed-environment restriction does not apply here).

- [ ] **Step 1: Write `.dockerignore`**

```
__pycache__/
*.pyc
*.egg-info/
.pytest_cache/
tests/
data/
```

- [ ] **Step 2: Write `entrypoint.sh`**

```bash
#!/bin/sh
set -eu

exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- [ ] **Step 3: Make it executable**

```bash
chmod +x /home/ubuntu/workspace/newsgrab/collector-service/entrypoint.sh
```

- [ ] **Step 4: Write `Dockerfile`**

```dockerfile
# Pure-Python job API service: no browser, no Xvfb -- resolution/rendering
# is delegated entirely to playwright-service over HTTP.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

# Set to "false" to install OS/Python packages from the default upstream
# sources instead of the Tsinghua mirrors (useful outside mainland-China
# network paths). Declared right before its first use so it never sits
# between FROM and the apt-get layer below -- an ARG inserted earlier
# busts every later layer's cache even if that layer doesn't reference it.
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
        tini \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /data

WORKDIR /app

COPY pyproject.toml .
COPY app /app/app
RUN set -eux; \
    if [ "$USE_MIRROR" = "true" ]; then \
        pip install --no-cache-dir --timeout 180 --retries 5 \
            --index-url https://pypi.tuna.tsinghua.edu.cn/simple .; \
    else \
        pip install --no-cache-dir --timeout 180 --retries 5 .; \
    fi

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV DEDUP_CACHE_PATH=/data/dedup_cache.db

EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
```

- [ ] **Step 5: Build the image**

Run: `cd /home/ubuntu/workspace/newsgrab/collector-service && docker build -t newsgrab-collector-service .`
Expected: build completes without error. If it doesn't, DO NOT guess -- read the actual error (`docker buildx history logs <BUILD_ID>` if the build ran in the background) and compare against the specific failure modes already documented in `playwright-service`'s plan before trying anything new.

- [ ] **Step 6: Standalone smoke-test (echo backend only -- playwright-service isn't reachable yet at this point)**

```bash
docker run --rm -d --name collector-service-smoketest -p 18100:8000 newsgrab-collector-service
sleep 3
curl -s http://localhost:18100/healthz
echo
curl -s -X POST http://localhost:18100/jobs -H "Content-Type: application/json" \
  -d '{"backend": "echo", "query": "smoke test"}'
echo
docker stop collector-service-smoketest
```

Expected: `{"status":"ok"}` then `{"job_id":"<some-uuid>"}`. (Full `google_news` backend verification happens in Task 10, once `playwright-service` is reachable via docker-compose.)

- [ ] **Step 7: Commit**

```bash
cd /home/ubuntu/workspace/newsgrab
git add collector-service/Dockerfile collector-service/entrypoint.sh collector-service/.dockerignore
git commit -m "feat: add collector-service Dockerfile and entrypoint"
```

---

## Task 9: `docker-compose.yml` update + READMEs

**Files:**
- Modify: `docker-compose.yml` (repo root)
- Create: `collector-service/README.md`
- Modify: `README.md` (repo root)

**Interfaces:**
- Consumes: `collector-service/Dockerfile` (Task 8); the existing `playwright-service` compose service and `newsgrab-internal` network (already in `docker-compose.yml`).

- [ ] **Step 1: Update the root `docker-compose.yml`**

The current file (from the `playwright-service` plan) is:

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

Replace it with:

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

  collector-service:
    build:
      context: ./collector-service
    container_name: newsgrab-collector-service
    restart: unless-stopped
    environment:
      PLAYWRIGHT_SERVICE_URL: http://playwright-service:8000
    depends_on:
      - playwright-service
    networks:
      - newsgrab-internal
    volumes:
      - collector-dedup-cache:/data
    # No ports published to the host by default -- only reachable from
    # other containers on newsgrab-internal. Uncomment for local debugging:
    # ports:
    #   - "18100:8000"

networks:
  newsgrab-internal:
    driver: bridge

volumes:
  collector-dedup-cache:
```

- [ ] **Step 2: Verify compose config parses**

Run: `cd /home/ubuntu/workspace/newsgrab && docker compose config --quiet`
Expected: no output, exit code 0.

- [ ] **Step 3: Write `collector-service/README.md`**

```markdown
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
```

- [ ] **Step 4: Update the root `README.md`**

Replace the "not yet built" line for `collector-service` with a built description. The current file (from the `playwright-service` plan) is:

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

Replace with:

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
- **`collector-service`**: pluggable content-collection backends (Google
  News first) with an async job API for callers. Calls `playwright-service`
  internally. See `collector-service/README.md`.

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
git add docker-compose.yml collector-service/README.md README.md
git commit -m "docs: wire collector-service into docker-compose and document it"
```

---

## Task 10: End-to-end live verification

**Files:** None created or modified — this task is verification only.

**Interfaces:** None produced — this is the final task of this plan.

- [ ] **Step 1: Bring up both services together**

```bash
cd /home/ubuntu/workspace/newsgrab
docker compose up -d --build
sleep 5
docker compose ps
```

Expected: both `newsgrab-playwright-service` and `newsgrab-collector-service` show as running.

- [ ] **Step 2: Confirm both services are healthy from inside the compose network**

```bash
docker compose exec collector-service python3 -c "
import urllib.request
print(urllib.request.urlopen('http://playwright-service:8000/healthz').read().decode())
print(urllib.request.urlopen('http://localhost:8000/healthz').read().decode())
"
```

Expected: two JSON lines, `{"status":"ok","browser_connected":true}` and `{"status":"ok"}`. If `browser_connected` is `false`, wait a few more seconds (Chromium/CDP startup) and retry before treating it as a real failure.

- [ ] **Step 3: Submit a real Google News collection job for a stock-related query**

```bash
JOB_ID=$(docker compose exec -T collector-service python3 -c "
import urllib.request, json
body = json.dumps({'backend': 'google_news', 'query': '贵州茅台 600519', 'params': {'max_results': 5, 'days': 7}}).encode()
req = urllib.request.Request('http://localhost:8000/jobs', data=body, headers={'Content-Type': 'application/json'}, method='POST')
print(json.loads(urllib.request.urlopen(req).read())['job_id'])
")
echo "job_id: $JOB_ID"
```

Expected: a UUID printed. This requires real outbound network access from inside the `playwright-service` container (Google News + the actual resolved news sites) — if this sandbox has no outbound internet access, note that explicitly rather than fabricating a result, and fall back to Step 5's mocked-network smoke test instead.

- [ ] **Step 4: Poll until the job completes**

```bash
for i in $(seq 1 30); do
  STATUS_JSON=$(docker compose exec -T collector-service python3 -c "
import urllib.request
print(urllib.request.urlopen('http://localhost:8000/jobs/$JOB_ID').read().decode())
")
  echo "$STATUS_JSON"
  echo "$STATUS_JSON" | grep -q '"status":"done"' && break
  echo "$STATUS_JSON" | grep -q '"status":"failed"' && break
  sleep 2
done
```

Expected: eventually `"status":"done"` with a non-empty `"result"` array containing real article dicts (`title`, `content`, `url`, `source`, `published_date`) about 贵州茅台/600519 — inspect the actual returned titles/URLs to confirm they're genuinely about the queried stock, not empty/garbage content. If `"status":"failed"`, read the `"error"` field and diagnose before declaring this task done — do not retry blindly.

- [ ] **Step 5: Tear down**

```bash
cd /home/ubuntu/workspace/newsgrab
docker compose down
```

- [ ] **Step 6: Record the outcome**

Write the actual observed result (job status, article count, a sample title/URL from the response, or the exact failure reason if it didn't work) into this plan file under a new "## Live Verification Result" section at the end, so the outcome is preserved alongside the plan rather than only in a chat transcript.

---

## Self-Review Notes

- **Spec coverage:** Every collector-service requirement from the design spec's §4/6/7/8 maps to a task — async job API (Task 1), pluggable `Collector` registry (Task 1), Google News backend (Tasks 2-7), SQLite dedup cache with the exact TTL/key-granularity semantics from §4.4 (Task 4), single-link-failure isolation (Task 7's per-link `continue` pattern), no auth (documented throughout, no code adds it), no second backend implemented (only the interface, per §8).
- **Placeholder scan:** No TBD/TODO; every code step is complete and runnable. Task 10's live verification explicitly tells the executor what to do if outbound network access is unavailable, rather than assuming success.
- **Type consistency:** `fetch_google_news_links(keyword, max_results=10, days=7) -> List[Dict[str, Any]]` (Task 6) is called identically (via `asyncio.to_thread`) in Task 7. `resolve_and_render(url, *, timeout_ms, leave_prefix=None) -> Optional[Dict]` (Task 5) is called identically in Task 7. `DedupCache`'s four methods (Task 4) are called with the exact same signatures in Task 7. `ContentParser().parse(html, url)` (Task 3) matches its Task 7 call site. `JobRequest`/`JobStatusResponse` field names (Task 1) match every test's JSON assertions.
- **Cross-plan consistency:** the `{"action": "resolve_and_render", "params": {"url", "timeout_ms", "leave_prefix"}}` request shape and `{"success", "result", "error"}` response envelope used in Task 5 match `playwright-service`'s actual, already-built and live-verified API exactly (`docs/superpowers/plans/2026-07-26-playwright-service.md`) — not a re-guess of the contract.
