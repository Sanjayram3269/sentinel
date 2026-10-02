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
    }
    assert expected <= registered


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