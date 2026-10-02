"""PostGIS/Redis integration coverage for prediction persistence and APIs."""

import asyncio
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from geoalchemy2 import WKTElement
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.main import app
from app.models import Hazard, Mission, Route, Vehicle
from app.models.enums import HazardType, RouteStatus, VehicleStatus, VehicleType

pytestmark = pytest.mark.integration
BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@pytest.fixture(scope="module", autouse=True)
def upgrade_test_database() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip("set TEST_DATABASE_URL to run prediction integration tests")
    command.upgrade(Config(str(BACKEND_DIR / "alembic.ini")), "head")


def create_mission(client: TestClient, objective: str) -> str:
    response = client.post("/api/v1/missions", json={"objective": objective})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def add_route_and_hazard(mission_id: str) -> str:
    vehicle_id = str(uuid4())

    async def persist() -> None:
        engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                async with session.begin():
                    mission = await session.get(Mission, UUID(mission_id))
                    assert mission is not None
                    vehicle = Vehicle(
                        id=UUID(vehicle_id),
                        mission_id=mission.id,
                        vehicle_type=VehicleType.AMBULANCE,
                        status=VehicleStatus.EN_ROUTE,
                        call_sign=f"PRED-{uuid4()}",
                        speed=2.0,
                    )
                    route = Route(
                        mission_id=mission.id,
                        vehicle_id=UUID(vehicle_id),
                        status=RouteStatus.ACTIVE,
                        name="Synthetic prediction test route",
                        geometry=WKTElement(
                            "LINESTRING(77.5946 12.9716, 77.6046 12.9816)",
                            srid=4326,
                        ),
                        distance_meters=1_000,
                        estimated_duration_seconds=100,
                        risk_score=0.6,
                    )
                    hazard = Hazard(
                        mission_id=mission.id,
                        hazard_type=HazardType.FIRE,
                        severity=4,
                        geometry=WKTElement(
                            "MULTIPOLYGON(((77.594 12.971, 77.595 12.971, "
                            "77.595 12.972, 77.594 12.972, 77.594 12.971)))",
                            srid=4326,
                        ),
                        description="Synthetic prediction integration hazard",
                    )
                    session.add_all([vehicle, route, hazard])
        finally:
            await engine.dispose()

    asyncio.run(persist())
    return vehicle_id


def test_prediction_api_persists_filters_retrieves_and_emits_event() -> None:
    with TestClient(app) as client:
        empty_mission = create_mission(client, "Prediction unavailable scenario")
        unavailable = client.post(
            f"/api/v1/missions/{empty_mission}/predictions",
            json={"prediction_type": "ETA"},
        )
        assert unavailable.status_code == 201, unavailable.text
        unavailable_body = unavailable.json()
        assert unavailable_body["status"] == "UNAVAILABLE"
        assert unavailable_body["reason"]
        assert unavailable_body["missing_inputs"] == ["active_route"]

        mission_id = create_mission(client, "Prediction observable scenario")
        add_route_and_hazard(mission_id)
        correlation_id = str(uuid4())
        eta = client.post(
            f"/api/v1/missions/{mission_id}/predictions",
            json={
                "prediction_type": "ETA",
                "correlation_id": correlation_id,
                "horizon_seconds": 600,
            },
        )
        assert eta.status_code == 201, eta.text
        eta_body = eta.json()
        assert eta_body["prediction_type"] == "ETA"
        assert eta_body["correlation_id"] == correlation_id
        assert eta_body["value"]["routes"][0]["eta_seconds"] == 500.0
        assert eta_body["model_version"] == "baseline_rule_v1"
        assert eta_body["factors"]

        route_failure = client.post(
            f"/api/v1/missions/{mission_id}/predictions",
            json={"prediction_type": "ROUTE_FAILURE"},
        )
        assert route_failure.status_code == 201, route_failure.text
        assert 0 <= route_failure.json()["probability"] <= 1

        congestion = client.post(
            f"/api/v1/missions/{mission_id}/predictions",
            json={"prediction_type": "CONGESTION"},
        )
        assert congestion.status_code == 201, congestion.text
        assert congestion.json()["value"]["level"] == "HIGH"

        hazard = client.post(
            f"/api/v1/missions/{mission_id}/predictions",
            json={"prediction_type": "HAZARD_IMPACT"},
        )
        assert hazard.status_code == 201, hazard.text
        assert hazard.json()["value"]["impact_level"] == "HIGH"

        fetched = client.get(
            f"/api/v1/missions/{mission_id}/predictions/{eta_body['prediction_id']}"
        )
        assert fetched.status_code == 200
        assert fetched.json()["correlation_id"] == correlation_id
        missing = client.get(
            f"/api/v1/missions/{mission_id}/predictions/{uuid4()}"
        )
        assert missing.status_code == 404

        history = client.get(f"/api/v1/missions/{mission_id}/predictions?limit=2")
        assert history.status_code == 200
        assert len(history.json()["items"]) == 2
        assert history.json()["has_more"] is True
        filtered = client.get(
            f"/api/v1/missions/{mission_id}/predictions?prediction_type=ETA"
        )
        assert filtered.status_code == 200
        assert all(
            item["prediction_type"] == "ETA" for item in filtered.json()["items"]
        )
        time_window = client.get(
            f"/api/v1/missions/{mission_id}/predictions"
            "?after=2000-01-01T00:00:00Z&before=2100-01-01T00:00:00Z&limit=10"
        )
        assert time_window.status_code == 200
        assert len(time_window.json()["items"]) == 4

        events = client.get(
            f"/api/v1/missions/{mission_id}/events?event_type=PREDICTION_UPDATED"
        )
        assert events.status_code == 200
        matching = [
            event
            for event in events.json()["items"]
            if event["payload"].get("prediction_id") == eta_body["prediction_id"]
        ]
        assert len(matching) == 1
        assert matching[0]["correlation_id"] == correlation_id
        assert matching[0]["payload"]["prediction_type"] == "ETA"


def test_prediction_api_validates_type_limits_and_mission_scope() -> None:
    with TestClient(app) as client:
        mission_id = create_mission(client, "Prediction validation")
        invalid_type = client.post(
            f"/api/v1/missions/{mission_id}/predictions",
            json={"prediction_type": "OTHER"},
        )
        assert invalid_type.status_code == 422
        invalid_horizon = client.post(
            f"/api/v1/missions/{mission_id}/predictions",
            json={"prediction_type": "ETA", "horizon_seconds": 0},
        )
        assert invalid_horizon.status_code == 422
        invalid_mission = client.post(
            f"/api/v1/missions/{uuid4()}/predictions",
            json={"prediction_type": "ETA"},
        )
        assert invalid_mission.status_code == 404
        invalid_history = client.get(
            f"/api/v1/missions/{mission_id}/predictions?limit=201"
        )
        assert invalid_history.status_code == 422