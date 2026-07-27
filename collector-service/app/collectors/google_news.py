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
from app.config import DEDUP_CACHE_PATH, DEFAULT_MAX_CANDIDATES, PLAYWRIGHT_RESOLVE_TIMEOUT_MS
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
    """Google News collection: query -> resolve -> dedup -> SSRF-check -> parse.

    A single link's failure at any stage is logged and skipped -- it never
    aborts the rest of the job. See module docstring for the cache/lazy-
    singleton rationale.

    If `fetch_google_news_links` found zero candidate links, that's a normal
    "no recent news" outcome and this returns `[]`. But if it found one or
    more candidate links and *every one* of them subsequently failed (resolve
    error, SSRF rejection, content-extraction failure) so zero articles came
    out, that's treated as a job failure: raises `RuntimeError` so callers
    (see `app.main._run_job`) mark the job `failed` instead of `done` + `[]`,
    per design spec section 6.

    `max_results` (default 10) is the number of articles the caller wants
    back. `max_candidates` (default `config.DEFAULT_MAX_CANDIDATES`, 20) is
    a separate ceiling on how many raw Google News links this job is
    willing to resolve/render/parse while trying to reach that count --
    they're decoupled because not every candidate link survives the
    pipeline (Google News itself may not have `max_results` worth of live,
    parseable articles for a given query). `max_candidates` is always
    raised to at least `max_results` if given lower, since asking for
    fewer candidates than the desired result count would just guarantee
    under-delivery.
    """
    max_results = int(params.get("max_results", 10))
    days = int(params.get("days", 7))
    language = params.get("language")
    region = params.get("region")
    max_candidates = max(int(params.get("max_candidates", DEFAULT_MAX_CANDIDATES)), max_results)

    links = await asyncio.to_thread(
        fetch_google_news_links,
        query,
        candidate_limit=max_candidates,
        days=days,
        language=language,
        region=region,
    )

    dedup_cache = _get_dedup_cache()
    content_parser = _get_content_parser()
    articles: List[Dict[str, Any]] = []

    for link in links:
        if len(articles) >= max_results:
            break

        raw_link = link["link"]

        cached = dedup_cache.get_by_raw_link(raw_link)
        if cached:
            articles.append(cached)
            continue

        resolved = await resolve_and_render(
            raw_link, timeout_ms=PLAYWRIGHT_RESOLVE_TIMEOUT_MS, leave_prefix=_GOOGLE_NEWS_LEAVE_PREFIX
        )
        if resolved is None:
            logger.info("[google_news] skipping link (resolve failed): %s", raw_link)
            continue
        real_url = resolved["final_url"]
        html = resolved["html"]

        cached_by_real_url = dedup_cache.get_by_real_url(real_url)
        if cached_by_real_url:
            dedup_cache.link_raw_to_real(raw_link, real_url)
            articles.append(cached_by_real_url)
            continue

        if not is_safe_url(real_url):
            logger.info("[google_news] skipping link (rejected by SSRF check): %s", real_url)
            continue

        parsed = content_parser.parse(html, real_url)
        if not parsed or not parsed.get("content"):
            logger.info("[google_news] skipping link (content extraction failed): %s", real_url)
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

    logger.info("[google_news] %r -> %s articles from %s links", query, len(articles), len(links))

    if links and not articles:
        raise RuntimeError(
            f"all {len(links)} candidate link(s) failed to yield an article for query {query!r}"
        )

    return articles


COLLECTORS["google_news"] = collect
