"""Health endpoint tests."""

from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_ok_status_and_service() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "sentinel-backend"}