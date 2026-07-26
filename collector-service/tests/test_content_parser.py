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
