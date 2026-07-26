from fastapi.testclient import TestClient
from unittest.mock import MagicMock

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
