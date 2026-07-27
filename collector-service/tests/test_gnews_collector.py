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
