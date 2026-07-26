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
