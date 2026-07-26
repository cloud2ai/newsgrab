import time

from fastapi.testclient import TestClient

from app.main import app


def test_healthz():
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_job_unknown_backend_returns_400():
    client = TestClient(app)
    response = client.post("/jobs", json={"backend": "nonexistent", "query": "x"})
    assert response.status_code == 400


def test_get_job_unknown_id_returns_404():
    client = TestClient(app)
    response = client.get("/jobs/nonexistent-id")
    assert response.status_code == 404


def test_create_and_poll_job_with_echo_backend():
    client = TestClient(app)
    response = client.post("/jobs", json={"backend": "echo", "query": "hello"})
    assert response.status_code == 201
    job_id = response.json()["job_id"]

    deadline = time.monotonic() + 5
    poll = None
    while time.monotonic() < deadline:
        poll = client.get(f"/jobs/{job_id}")
        assert poll.status_code == 200
        if poll.json()["status"] in {"done", "failed"}:
            break
        time.sleep(0.05)

    assert poll.json()["status"] == "done"
    assert poll.json()["result"] == [
        {"title": "hello", "content": "", "url": "", "source": "echo", "published_date": None}
    ]
