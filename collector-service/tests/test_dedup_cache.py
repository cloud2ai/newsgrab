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
