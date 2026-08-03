"""Heuristic filter for images extracted from article HTML.

GNE's extractor returns every <img> src it finds on the page, including
logos, icons, avatars, tracking pixels, and other site-chrome images that
have nothing to do with the article itself. newsgrab never fetches the
images themselves to inspect them (that would mean an unbounded number of
extra network calls -- one per image, each needing its own SSRF check --
just to return metadata callers may not even use), so filtering here is
heuristic and URL-only: resolve relative URLs to absolute, require a
plausible content-image extension, and drop filenames that look like
site chrome rather than a photo.
"""
from typing import List
from urllib.parse import urljoin, urlparse

_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
_BLOCKED_EXTENSIONS = {".svg", ".ico"}

_NON_CONTENT_KEYWORDS = (
    "logo", "icon", "sprite", "avatar", "pixel", "spacer", "blank",
    "tracking", "1x1", "badge", "placeholder", "favicon", "emoji",
)

_DEFAULT_MAX_IMAGES = 20


def filter_images(images: List[str], page_url: str, max_images: int = _DEFAULT_MAX_IMAGES) -> List[str]:
    """Resolve, validate, and dedupe a raw list of image URLs from article HTML.

    Never raises -- a malformed entry is silently dropped rather than
    aborting the whole list, same posture as the rest of content
    extraction (best-effort, skip what doesn't work).
    """
    seen = set()
    result: List[str] = []

    for raw in images:
        if not raw or not raw.strip() or any(char.isspace() for char in raw):
            continue

        absolute = urljoin(page_url, raw)
        parsed = urlparse(absolute)

        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            continue

        path_lower = parsed.path.lower()
        extension = _extension(path_lower)
        if extension in _BLOCKED_EXTENSIONS:
            continue
        if extension and extension not in _ALLOWED_EXTENSIONS:
            continue
        if any(keyword in path_lower for keyword in _NON_CONTENT_KEYWORDS):
            continue

        if absolute in seen:
            continue
        seen.add(absolute)
        result.append(absolute)

        if len(result) >= max_images:
            break

    return result


def _extension(path: str) -> str:
    dot_index = path.rfind(".")
    return path[dot_index:] if dot_index != -1 else ""
