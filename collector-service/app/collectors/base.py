"""Pluggable collector backend registry.

Mirrors playwright-service's ACTIONS registry pattern: a plain dict from
name to an async callable, so a new backend is "write a collect()
function, register it here" with no other endpoint changes needed.
"""
from typing import Any, Awaitable, Callable, Dict, List

Collector = Callable[..., Awaitable[List[Dict[str, Any]]]]

COLLECTORS: Dict[str, Collector] = {}


async def _echo_collect(query: str, **params: Any) -> List[Dict[str, Any]]:
    """Placeholder backend for wiring/testing the job API before a real
    backend (google_news, added in a later task) is registered."""
    return [{"title": query, "content": "", "url": "", "source": "echo", "published_date": None}]


COLLECTORS["echo"] = _echo_collect
