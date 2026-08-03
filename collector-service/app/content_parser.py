"""Three-way content extraction fallback: run all fetchers, keep the longest."""
import logging
from typing import Any, Dict, Optional

from app.content_fetchers import GNEFetcher, ReadabilityFetcher, TrafilaturaFetcher
from app.image_filter import filter_images

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
        best_data["images"] = filter_images(best_data.get("images") or [], url)
        return best_data
