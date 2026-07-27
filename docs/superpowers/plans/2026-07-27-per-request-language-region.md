# Per-Request Language/Region Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a `google_news` job's `params` optionally carry `language`/`region`, overriding collector-service's deployment-level `GOOGLE_NEWS_LANGUAGE`/`GOOGLE_NEWS_REGION` for that one request, so a single deployment can serve multi-region queries.

**Architecture:** Thread two new optional keyword arguments through the existing call chain (`collect(query, **params)` → `fetch_google_news_links(...)` → `GNews(...)`), falling back to the current config defaults when absent. No new files, no schema changes (`JobRequest.params` is already a free dict).

**Tech Stack:** Python, `gnews`, `pytest`/`unittest.mock` (existing conventions in this repo).

## Global Constraints

Copied verbatim from `docs/superpowers/specs/2026-07-27-per-request-language-region.md`:

- **Fully backward compatible**: omitting `language`/`region` from a job's `params` must produce byte-for-byte the same behavior as today (uses `config.GOOGLE_NEWS_LANGUAGE`/`config.GOOGLE_NEWS_REGION`).
- **No validation** of language/region values — pure pass-through, trusting the caller, same trust boundary as the existing `days`/`max_results` params.
- **No changes** to `playwright-service`, SSRF checks, dedup cache, content parser, or `app/schemas.py` (`JobRequest.params` is already a free `dict`, needs no schema change).
- Commit directly to `main` (established repo convention for this project). Commit messages in English.

---

### Task 1: Thread `language`/`region` through `fetch_google_news_links` and `collect()`

**Files:**
- Modify: `collector-service/app/gnews_collector.py:47-59` (`fetch_google_news_links` signature and its `GNews(...)` construction)
- Modify: `collector-service/app/collectors/google_news.py:62-65` (`collect()`'s params reading + the `fetch_google_news_links` call)
- Test: `collector-service/tests/test_gnews_collector.py`
- Test: `collector-service/tests/test_google_news_collector.py`

**Interfaces:**
- Consumes: nothing new from other tasks — this is the only task in this plan.
- Produces: `fetch_google_news_links(keyword, max_results=10, days=7, language=None, region=None) -> List[Dict[str, Any]]` — the two new trailing keyword-only-by-convention params, both defaulting to `None`. This exact signature is the stable contract `collect()` (and any future caller) relies on.

- [ ] **Step 1: Write the failing tests for `fetch_google_news_links`**

Add to `collector-service/tests/test_gnews_collector.py` (after the existing `test_fetch_google_news_links_returns_empty_on_gnews_exception`, before `test_monkeypatch_keeps_raw_google_news_url`):

```python
def test_fetch_google_news_links_uses_config_defaults_when_language_region_omitted():
    from app.gnews_collector import fetch_google_news_links
    from app import config

    fake_gnews = MagicMock()
    fake_gnews.get_news.return_value = []

    with patch("app.gnews_collector.GNews", return_value=fake_gnews) as mock_gnews_cls:
        fetch_google_news_links("贵州茅台")

    mock_gnews_cls.assert_called_once()
    _, kwargs = mock_gnews_cls.call_args
    assert kwargs["language"] == config.GOOGLE_NEWS_LANGUAGE
    assert kwargs["country"] == config.GOOGLE_NEWS_REGION


def test_fetch_google_news_links_uses_explicit_language_region_when_given():
    from app.gnews_collector import fetch_google_news_links

    fake_gnews = MagicMock()
    fake_gnews.get_news.return_value = []

    with patch("app.gnews_collector.GNews", return_value=fake_gnews) as mock_gnews_cls:
        fetch_google_news_links("鉄鋼", language="ja", region="JP")

    mock_gnews_cls.assert_called_once()
    _, kwargs = mock_gnews_cls.call_args
    assert kwargs["language"] == "ja"
    assert kwargs["country"] == "JP"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/ubuntu/workspace/newsgrab/collector-service && python3 -m pytest tests/test_gnews_collector.py -v`
Expected: the two new tests FAIL with `TypeError: fetch_google_news_links() got an unexpected keyword argument 'language'` (the second one) and the first one currently passes already by coincidence (it's asserting today's actual default behavior) — confirm this by running it now: if it already passes, that's fine, it's establishing a baseline the refactor must not break. The second test must fail before the implementation change.

- [ ] **Step 3: Implement the signature/construction change in `fetch_google_news_links`**

In `collector-service/app/gnews_collector.py`, change:

```python
def fetch_google_news_links(
    keyword: str,
    max_results: int = 10,
    days: int = 7,
) -> List[Dict[str, Any]]:
```

to:

```python
def fetch_google_news_links(
    keyword: str,
    max_results: int = 10,
    days: int = 7,
    language: Optional[str] = None,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
```

and update its docstring's final paragraph (currently ends with `"Returns an empty list on any gnews failure -- callers should treat this as 'no links found'."`) by appending:

```
    `language`/`region` override the deployment's GOOGLE_NEWS_LANGUAGE/
    GOOGLE_NEWS_REGION config for this call only, when given.
    """
```

Then change the `GNews(...)` construction from:

```python
        g = GNews(
            language=config.GOOGLE_NEWS_LANGUAGE,
            country=config.GOOGLE_NEWS_REGION,
            max_results=max(max_results * config.REDUNDANT_RATE, config.MAX_RESULTS),
            exclude_websites=config.EXCLUDE_NEWS_SOURCE,
        )
```

to:

```python
        g = GNews(
            language=language or config.GOOGLE_NEWS_LANGUAGE,
            country=region or config.GOOGLE_NEWS_REGION,
            max_results=max(max_results * config.REDUNDANT_RATE, config.MAX_RESULTS),
            exclude_websites=config.EXCLUDE_NEWS_SOURCE,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/ubuntu/workspace/newsgrab/collector-service && python3 -m pytest tests/test_gnews_collector.py -v`
Expected: all tests pass, including the 2 new ones (7 total).

- [ ] **Step 5: Write the failing test for `collect()`'s params pass-through**

Add to `collector-service/tests/test_google_news_collector.py` (after `test_collect_returns_empty_list_when_no_links_found`, before `test_collect_full_pipeline_success_caches_the_article`):

```python
async def test_collect_passes_language_region_from_params_to_fetch_google_news_links():
    """collect() must forward language/region from its **params kwargs to
    fetch_google_news_links, so a caller can override the deployment default
    per-request (e.g. MacroIntelAgent querying 6 different regions)."""
    with patch.object(
        google_news_module, "fetch_google_news_links", return_value=[]
    ) as mock_fetch:
        await google_news_module.collect("鉄鋼業界", language="ja", region="JP")

    mock_fetch.assert_called_once_with(
        "鉄鋼業界", max_results=10, days=7, language="ja", region="JP"
    )


async def test_collect_omits_language_region_when_not_provided():
    """Regression guard: omitting language/region from params must not pass
    None explicitly in a way that breaks fetch_google_news_links's own
    config-default fallback -- confirm the call site passes None through
    (fetch_google_news_links's own `or` fallback handles the rest, already
    covered by that function's own tests)."""
    with patch.object(
        google_news_module, "fetch_google_news_links", return_value=[]
    ) as mock_fetch:
        await google_news_module.collect("贵州茅台")

    mock_fetch.assert_called_once_with(
        "贵州茅台", max_results=10, days=7, language=None, region=None
    )
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd /home/ubuntu/workspace/newsgrab/collector-service && python3 -m pytest tests/test_google_news_collector.py -v`
Expected: the 2 new tests FAIL — `mock_fetch.assert_called_once_with(...)` raises `AssertionError` because the actual call is currently `fetch_google_news_links(query, max_results=max_results, days=days)` (no `language`/`region` kwargs at all).

- [ ] **Step 7: Implement the params-reading change in `collect()`**

In `collector-service/app/collectors/google_news.py`, change:

```python
    max_results = int(params.get("max_results", 10))
    days = int(params.get("days", 7))

    links = await asyncio.to_thread(fetch_google_news_links, query, max_results=max_results, days=days)
```

to:

```python
    max_results = int(params.get("max_results", 10))
    days = int(params.get("days", 7))
    language = params.get("language")
    region = params.get("region")

    links = await asyncio.to_thread(
        fetch_google_news_links,
        query,
        max_results=max_results,
        days=days,
        language=language,
        region=region,
    )
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd /home/ubuntu/workspace/newsgrab/collector-service && python3 -m pytest tests/test_google_news_collector.py -v`
Expected: all tests pass, including the 2 new ones (12 total).

- [ ] **Step 9: Run the full collector-service test suite**

Run: `cd /home/ubuntu/workspace/newsgrab/collector-service && python3 -m pytest -v`
Expected: all tests pass (was 51 before this task — 8 new tests added across the two files — expect 55... actually recompute: test_gnews_collector.py had 5, now 7 (+2); test_google_news_collector.py had 11, now 13 (+2); net +4 to whatever the full-suite baseline was before this task. Run the suite and confirm zero failures rather than trusting an exact number — report the actual final count in the commit.)

- [ ] **Step 10: Commit**

```bash
cd /home/ubuntu/workspace/newsgrab
git add collector-service/app/gnews_collector.py collector-service/app/collectors/google_news.py collector-service/tests/test_gnews_collector.py collector-service/tests/test_google_news_collector.py
git commit -m "$(cat <<'EOF'
feat: support per-request language/region for google_news jobs

fetch_google_news_links() and collect() now accept optional language/region
that override the deployment's GOOGLE_NEWS_LANGUAGE/GOOGLE_NEWS_REGION for
a single job, via the job's params dict. Omitting them keeps today's
behavior unchanged (falls back to the deployment config), so this is fully
backward compatible.

Enables daily_stock_analysis's planned MacroIntelAgent to query the same
collector-service deployment across 6 fixed regions (CN/JP/KR/SG/US/EU)
instead of being locked to one globally configured language/region.
EOF
)"
git log --oneline -3
```
