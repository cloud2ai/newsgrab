"""Orchestration tests: every dependency is mocked, so this only verifies
the sequencing/skip logic in collect() itself -- each dependency's own
real behavior is covered by its own task's tests."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
    """A lone failing link means all candidate links failed -- collect() must
    raise so the job ends up `failed` rather than `done` + `[]`."""
    cache_patch, mock_cache = _patch_dedup_cache()

    with patch.object(google_news_module, "fetch_google_news_links", return_value=[
        {"link": "https://news.google.com/rss/1", "title": "t1", "published_date": "d1"}
    ]), cache_patch, \
         patch.object(google_news_module, "resolve_and_render", new=AsyncMock(return_value=None)):
        with pytest.raises(RuntimeError):
            await google_news_module.collect("贵州茅台")


async def test_collect_skips_link_rejected_by_ssrf_check():
    """A lone SSRF-rejected link means all candidate links failed -- collect()
    must raise so the job ends up `failed` rather than `done` + `[]`."""
    cache_patch, mock_cache = _patch_dedup_cache()

    with patch.object(google_news_module, "fetch_google_news_links", return_value=[
        {"link": "https://news.google.com/rss/1", "title": "t1", "published_date": "d1"}
    ]), cache_patch, \
         patch.object(google_news_module, "resolve_and_render", new=AsyncMock(return_value={
             "final_url": "http://10.0.0.5/internal", "html": "<html></html>",
         })), \
         patch.object(google_news_module, "is_safe_url", return_value=False):
        with pytest.raises(RuntimeError):
            await google_news_module.collect("贵州茅台")


async def test_collect_skips_link_when_content_extraction_fails():
    """A lone content-extraction failure means all candidate links failed --
    collect() must raise so the job ends up `failed` rather than `done` + `[]`."""
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
        with pytest.raises(RuntimeError):
            await google_news_module.collect("贵州茅台")


async def test_collect_continues_past_one_failed_link_when_another_succeeds():
    """Regression guard for the skip-one-continue behavior: one link fails to
    resolve, the other succeeds all the way through -- collect() must return
    only the successful article, not raise."""
    cache_patch, mock_cache = _patch_dedup_cache()

    with patch.object(google_news_module, "fetch_google_news_links", return_value=[
        {"link": "https://news.google.com/rss/1", "title": "t1", "published_date": "d1"},
        {"link": "https://news.google.com/rss/2", "title": "t2", "published_date": "d2"},
    ]), cache_patch, \
         patch.object(google_news_module, "resolve_and_render", new=AsyncMock(side_effect=[
             None,
             {"final_url": "https://real-site.example/b", "html": "<html>content</html>"},
         ])), \
         patch.object(google_news_module, "is_safe_url", return_value=True), \
         patch.object(google_news_module, "_get_content_parser") as mock_get_parser:
        mock_get_parser.return_value.parse.return_value = {"title": "Real Title", "content": "full body text"}
        result = await google_news_module.collect("贵州茅台")

    assert len(result) == 1
    assert result[0]["url"] == "https://real-site.example/b"


async def test_collect_returns_empty_list_when_no_links_found():
    """fetch_google_news_links returning [] is a normal "no recent news"
    outcome -- collect() must return [] without raising."""
    with patch.object(google_news_module, "fetch_google_news_links", return_value=[]):
        result = await google_news_module.collect("贵州茅台")

    assert result == []


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
