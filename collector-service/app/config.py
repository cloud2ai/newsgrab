"""Environment-driven configuration for collector-service."""
import os

PLAYWRIGHT_SERVICE_URL = os.environ.get("PLAYWRIGHT_SERVICE_URL", "http://localhost:8000")
PLAYWRIGHT_RESOLVE_TIMEOUT_MS = int(os.environ.get("PLAYWRIGHT_RESOLVE_TIMEOUT_MS", "20000"))

# `or` (not dict.get's default arg) so an empty string -- which docker-compose
# materializes as the container's env var value when the deployer's shell
# never set it, per the `${GOOGLE_NEWS_LANGUAGE:-}` pass-through in
# docker-compose.yml -- still falls back to the built-in default, rather
# than silently becoming an invalid empty language/region code for gnews.
GOOGLE_NEWS_LANGUAGE = os.environ.get("GOOGLE_NEWS_LANGUAGE") or "en"
GOOGLE_NEWS_REGION = os.environ.get("GOOGLE_NEWS_REGION") or "US"
EXCLUDE_NEWS_SOURCE = ["zdnet.com"]
REDUNDANT_RATE = 3
MAX_RESULTS = 20
GNEWS_FETCH_TIMEOUT = 30
MINIMAL_CONTENT_LENGTH = 200

# Relative by default so local dev/tests never need root-level filesystem
# access; the Dockerfile overrides this to an absolute /data path via env.
DEDUP_CACHE_PATH = os.environ.get("DEDUP_CACHE_PATH", "./data/dedup_cache.db")
DEDUP_CACHE_TTL_SECONDS = 7 * 24 * 3600
