from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock

from app.main import app, get_browser


def test_healthz_reports_connected_when_browser_is_connected():
    fake_browser = MagicMock()
    fake_browser.is_connected.return_value = True
    app.dependency_overrides[get_browser] = lambda: fake_browser

    client = TestClient(app)
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "browser_connected": True}
    app.dependency_overrides.clear()


def test_healthz_reports_degraded_when_browser_is_disconnected():
    fake_browser = MagicMock()
    fake_browser.is_connected.return_value = False
    app.dependency_overrides[get_browser] = lambda: fake_browser

    client = TestClient(app)
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "degraded", "browser_connected": False}
    app.dependency_overrides.clear()


def test_healthz_reports_degraded_when_browser_is_none():
    app.dependency_overrides[get_browser] = lambda: None

    client = TestClient(app)
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "degraded", "browser_connected": False}
    app.dependency_overrides.clear()


def test_run_action_unknown_action_returns_error_not_500():
    app.dependency_overrides[get_browser] = lambda: MagicMock()

    client = TestClient(app)
    response = client.post("/v1/actions", json={"action": "nope", "params": {}})

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert "nope" in body["error"]
    app.dependency_overrides.clear()


def test_run_action_success(monkeypatch):
    app.dependency_overrides[get_browser] = lambda: MagicMock()

    async def fake_execute_action(browser, action, params):
        assert action == "resolve_and_render"
        assert params == {"url": "https://example.com"}
        return {"final_url": "https://example.com/x", "html": "<html></html>"}

    monkeypatch.setattr("app.main.execute_action", fake_execute_action)

    client = TestClient(app)
    response = client.post(
        "/v1/actions",
        json={"action": "resolve_and_render", "params": {"url": "https://example.com"}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["result"] == {"final_url": "https://example.com/x", "html": "<html></html>"}
    assert body["error"] is None
    app.dependency_overrides.clear()


def test_run_action_execution_failure_returns_error_not_500(monkeypatch):
    app.dependency_overrides[get_browser] = lambda: MagicMock()

    async def failing_execute_action(browser, action, params):
        raise RuntimeError("navigation timed out")

    monkeypatch.setattr("app.main.execute_action", failing_execute_action)

    client = TestClient(app)
    response = client.post(
        "/v1/actions",
        json={"action": "resolve_and_render", "params": {"url": "https://example.com"}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert "navigation timed out" in body["error"]
    app.dependency_overrides.clear()


async def test_lifespan_connects_and_disconnects_browser(monkeypatch):
    fake_playwright = AsyncMock()
    fake_browser = AsyncMock()
    connect_calls = {}

    async def fake_connect_with_retry(cdp_url, **kwargs):
        connect_calls["cdp_url"] = cdp_url
        return fake_playwright, fake_browser

    monkeypatch.setattr("app.main.connect_with_retry", fake_connect_with_retry)
    monkeypatch.setenv("PLAYWRIGHT_CDP_URL", "http://localhost:9222")

    with TestClient(app) as client:
        assert connect_calls["cdp_url"] == "http://localhost:9222"
        assert client.app.state.browser is fake_browser

    fake_browser.close.assert_awaited_once()
    fake_playwright.stop.assert_awaited_once()
