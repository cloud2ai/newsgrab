from unittest.mock import AsyncMock, MagicMock, patch

from app.playwright_client import resolve_and_render


def _make_mock_client(response=None, post_side_effect=None):
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    if post_side_effect is not None:
        mock_client.post = AsyncMock(side_effect=post_side_effect)
    else:
        mock_client.post = AsyncMock(return_value=response)
    return mock_client


async def test_resolve_and_render_returns_result_on_success():
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {
        "success": True,
        "result": {"final_url": "https://example.com/a", "html": "<html></html>"},
        "error": None,
    }
    with patch("app.playwright_client.httpx.AsyncClient", return_value=_make_mock_client(fake_response)):
        result = await resolve_and_render("https://news.google.com/rss/x", timeout_ms=20000)

    assert result == {"final_url": "https://example.com/a", "html": "<html></html>"}


async def test_resolve_and_render_returns_none_on_service_reported_failure():
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {"success": False, "result": None, "error": "navigation timed out"}
    with patch("app.playwright_client.httpx.AsyncClient", return_value=_make_mock_client(fake_response)):
        result = await resolve_and_render("https://news.google.com/rss/x", timeout_ms=20000)

    assert result is None


async def test_resolve_and_render_returns_none_on_network_error():
    mock_client = _make_mock_client(post_side_effect=ConnectionError("connection refused"))
    with patch("app.playwright_client.httpx.AsyncClient", return_value=mock_client):
        result = await resolve_and_render("https://news.google.com/rss/x", timeout_ms=20000)

    assert result is None


async def test_resolve_and_render_passes_leave_prefix_when_given():
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {"success": True, "result": {"final_url": "x", "html": "y"}, "error": None}
    mock_client = _make_mock_client(fake_response)
    with patch("app.playwright_client.httpx.AsyncClient", return_value=mock_client):
        await resolve_and_render(
            "https://news.google.com/rss/x", timeout_ms=20000, leave_prefix="/rss/articles"
        )

    call_kwargs = mock_client.post.call_args.kwargs
    assert call_kwargs["json"]["params"]["leave_prefix"] == "/rss/articles"


async def test_resolve_and_render_omits_leave_prefix_when_not_given():
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {"success": True, "result": {"final_url": "x", "html": "y"}, "error": None}
    mock_client = _make_mock_client(fake_response)
    with patch("app.playwright_client.httpx.AsyncClient", return_value=mock_client):
        await resolve_and_render("https://news.google.com/rss/x", timeout_ms=20000)

    call_kwargs = mock_client.post.call_args.kwargs
    assert "leave_prefix" not in call_kwargs["json"]["params"]


async def test_resolve_and_render_disables_env_proxy_trust():
    """Regression test: this call must never honor ambient HTTP(S)_PROXY/
    NO_PROXY env vars, since it is always an internal same-network call to
    playwright-service. httpx defaults to trust_env=True, which made this
    call's success depend on the operator's NO_PROXY including
    "playwright-service" -- a real deployment hit this exact failure when an
    ambient shell NO_PROXY (set for unrelated reasons) silently overrode
    docker-compose.yml's own NO_PROXY default, causing every article fetch
    to fail with a 502 from the external proxy trying to reach an internal
    container hostname."""
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {"success": True, "result": {"final_url": "x", "html": "y"}, "error": None}
    mock_client = _make_mock_client(fake_response)
    with patch("app.playwright_client.httpx.AsyncClient", return_value=mock_client) as mock_cls:
        await resolve_and_render("https://news.google.com/rss/x", timeout_ms=20000)

    assert mock_cls.call_args.kwargs["trust_env"] is False
