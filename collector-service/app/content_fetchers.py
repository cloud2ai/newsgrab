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
