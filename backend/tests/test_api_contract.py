"""Database-free checks for public API registration and timestamp inputs."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.main import app
from app.schemas.domain import IncidentCreate


def test_domain_routes_are_registered() -> None:
    registered = {
        (route.path, method)
        for route in app.routes
        if hasattr(route, "methods")
        for method in route.methods or set()
    }
    expected = {
        ("/api/v1/missions", "POST"),
        ("/api/v1/missions/{mission_id}", "GET"),
        ("/api/v1/missions/{mission_id}/state", "GET"),
        ("/api/v1/incidents", "POST"),
        ("/api/v1/incidents/{incident_id}", "GET"),
        ("/api/v1/vehicles", "POST"),
        ("/api/v1/vehicles/{vehicle_id}", "GET"),
        ("/api/v1/missions/{mission_id}/events", "POST"),
        ("/api/v1/missions/{mission_id}/events", "GET"),
        ("/api/v1/missions/{mission_id}/telemetry", "POST"),
        ("/api/v1/missions/{mission_id}/predictions", "POST"),
        ("/api/v1/missions/{mission_id}/predictions", "GET"),
            ("/api/v1/missions/{mission_id}/routes/candidates", "POST"),
            ("/api/v1/missions/{mission_id}/routes", "GET"),
            ("/api/v1/missions/{mission_id}/routes/{route_id}", "GET"),
            ("/api/v1/missions/{mission_id}/routes/{route_id}/activate", "POST"),
            ("/api/v1/missions/{mission_id}/routes/resilience", "GET"),
            ("/api/v1/missions/{mission_id}/simulations/baseline", "POST"),
            ("/api/v1/missions/{mission_id}/simulations/clearpath", "POST"),
            ("/api/v1/missions/{mission_id}/simulations/compare", "POST"),
            ("/api/v1/missions/{mission_id}/simulations", "GET"),
            ("/api/v1/missions/{mission_id}/simulations/{simulation_id}", "GET"),
        (
            "/api/v1/missions/{mission_id}/predictions/{prediction_id}",
            "GET",
        ),
    }
    assert expected <= registered
    assert any(route.path == "/ws/missions/{mission_id}" for route in app.routes)


def test_root_and_health_endpoints_remain_available() -> None:
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/health").json()["status"] == "ok"


def test_incident_time_input_must_be_timezone_aware_and_is_normalized() -> None:
    with pytest.raises(ValidationError):
        IncidentCreate(
            type="FIRE",
            severity=2,
            description="Synthetic test",
            location={"latitude": 0, "longitude": 0},
            occurred_at=datetime(2026, 1, 1),
        )

    incident = IncidentCreate(
        type="FIRE",
        severity=2,
        description="Synthetic test",
        location={"latitude": 0, "longitude": 0},
        occurred_at="2026-01-01T03:00:00+03:00",
    )
    assert incident.occurred_at is not None
    assert incident.occurred_at.utcoffset() == timezone.utc.utcoffset(incident.occurred_at)