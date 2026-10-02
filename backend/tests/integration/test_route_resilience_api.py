"""PostGIS/Redis integration coverage for route resilience workflows."""

import asyncio
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event as sqlalchemy_event
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.main import app
from app.db.session import engine as app_engine
from app.models import Prediction, RouteCandidate
from app.models.enums import PredictionType
from app.services.event_bus import EventPublisher
from app.services.event_service import EventService
from app.services.prediction_service import PredictionService
from app.services.route_monitor import RouteMonitorService

pytestmark = pytest.mark.integration
BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@pytest.fixture(scope="module", autouse=True)
def upgrade_test_database() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip("set TEST_DATABASE_URL to run route resilience integration tests")
    command.upgrade(Config(str(BACKEND_DIR / "alembic.ini")), "head")


def create_mission_and_vehicle(client: TestClient, name: str) -> tuple[str, str]:
    mission = client.post("/api/v1/missions", json={"objective": name})
    assert mission.status_code == 201, mission.text
    mission_id = mission.json()["id"]
    vehicle = client.post(
        "/api/v1/vehicles",
        json={
            "mission_id": mission_id,
            "vehicle_type": "AMBULANCE",
            "call_sign": f"ROUTE-{uuid4()}",
        },
    )
    assert vehicle.status_code == 201, vehicle.text
    return mission_id, vehicle.json()["id"]


def candidate_payload(vehicle_id: str, *, high_failure: bool = False) -> dict[str, object]:
    origin = {"latitude": 0, "longitude": 0}
    destination = {"latitude": 1, "longitude": 1}
    candidates = [
        {
            "name": "north corridor",
            "geometry": [origin, {"latitude": 0.7, "longitude": 0.3}, destination],
            "distance_meters": 1000,
            "estimated_duration_seconds": 100,
            "risk_score": 0.1,
            "predicted_failure_probability": 0.95 if high_failure else None,
            "congestion_score": 0.2,
            "hazard_exposure": 0.1,
            "road_segment_ids": ["NORTH-1", "NORTH-2"],
        },
        {
            "name": "south corridor",
            "geometry": [origin, {"latitude": 0.3, "longitude": 0.7}, destination],
            "distance_meters": 1300,
            "estimated_duration_seconds": 130,
            "risk_score": 0.2,
            "congestion_score": 0.3,
            "hazard_exposure": 0.2,
            "road_segment_ids": ["SOUTH-1", "SOUTH-2"],
        },
        {
            "name": "east corridor",
            "geometry": [origin, {"latitude": 0.8, "longitude": 0.8}, destination],
            "distance_meters": 1500,
            "estimated_duration_seconds": 150,
            "risk_score": 0.3,
            "congestion_score": 0.4,
            "hazard_exposure": 0.3,
            "road_segment_ids": ["EAST-1", "EAST-2"],
        },
    ]
    return {
        "vehicle_id": vehicle_id,
        "origin": origin,
        "destination": destination,
        "candidates": candidates,
        "routing_parameters": {"profile": "emergency"},
    }


def make_monitor() -> tuple[RouteMonitorService, object]:
    from redis.asyncio import Redis

    redis = Redis.from_url("redis://localhost:6379/0", decode_responses=True)
    event_service = EventService(EventPublisher(redis))
    prediction_service = PredictionService(event_service)
    return RouteMonitorService(event_service, prediction_service), redis


def run_monitor(mission_id: str, route_id: str) -> dict[str, object]:
    async def evaluate() -> dict[str, object]:
        service, redis = make_monitor()
        engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                result = await service.evaluate_route_health(
                    session, UUID(mission_id), UUID(route_id)
                )
                return result.model_dump(mode="json")
        finally:
            await engine.dispose()
            await redis.aclose()

    return asyncio.run(evaluate())


def test_candidate_generation_prediction_scoring_resilience_and_activation() -> None:
    with TestClient(app) as client:
        mission_id, vehicle_id = create_mission_and_vehicle(client, "Route workflow")

        prediction = client.post(
            f"/api/v1/missions/{mission_id}/predictions",
            json={"prediction_type": "ROUTE_FAILURE"},
        )
        assert prediction.status_code == 201, prediction.text
        assert prediction.json()["probability"] == 0.45

        select_statements: list[str] = []

        def count_selects(connection, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                select_statements.append(statement)

        sqlalchemy_event.listen(
            app_engine.sync_engine, "before_cursor_execute", count_selects
        )
        try:
            generated = client.post(
                f"/api/v1/missions/{mission_id}/routes/candidates",
                json=candidate_payload(vehicle_id),
            )
        finally:
            sqlalchemy_event.remove(
                app_engine.sync_engine, "before_cursor_execute", count_selects
            )
        assert generated.status_code == 201, generated.text
        body = generated.json()
        assert body["provider"] == "baseline_development_provider"
        assert len(body["candidates"]) == 3
        assert body["candidates"][0]["road_segment_ids"]
        assert {item["resilience_role"] for item in body["candidates"]} == {
            "PRIMARY",
            "BACKUP",
            "CONTINGENCY",
        }
        assert all(item["predicted_failure_probability"] is None for item in body["candidates"])
        assert len(select_statements) <= 5
        assert body["resilience"]["resilience_level"] == "HIGH_RESILIENCE"
        assert body["resilience"]["route_diversity"] > 0.9

        mission_routes = client.get(
            f"/api/v1/missions/{mission_id}/routes?status=CANDIDATE&limit=10"
        )
        assert mission_routes.status_code == 200
        assert len(mission_routes.json()["items"]) == 3
        primary_id = body["resilience"]["primary_route_id"]
        route_detail = client.get(f"/api/v1/missions/{mission_id}/routes/{primary_id}")
        assert route_detail.status_code == 200
        assert route_detail.json()["resilience_role"] == "PRIMARY"

        resilience = client.get(
            f"/api/v1/missions/{mission_id}/routes/resilience"
            f"?vehicle_id={vehicle_id}&planning_cycle_id={body['planning_cycle_id']}"
        )
        assert resilience.status_code == 200
        assert resilience.json()["primary_route_id"] == primary_id

        correlation_id = str(uuid4())
        activated = client.post(
            f"/api/v1/missions/{mission_id}/routes/{primary_id}/activate",
            json={"correlation_id": correlation_id},
        )
        assert activated.status_code == 200, activated.text
        assert activated.json()["correlation_id"] == correlation_id
        assert activated.json()["route"]["status"] == "ACTIVE"

        events = client.get(
            f"/api/v1/missions/{mission_id}/events?event_type=ROUTE_ASSIGNED"
        )
        assert events.status_code == 200
        assert any(
            event["payload"].get("route_id") == primary_id
            and event["correlation_id"] == correlation_id
            for event in events.json()["items"]
        )

        # A second planning cycle replaces the currently active route atomically.
        second_cycle = client.post(
            f"/api/v1/missions/{mission_id}/routes/candidates",
            json=candidate_payload(vehicle_id),
        )
        assert second_cycle.status_code == 201, second_cycle.text
        replacement_id = second_cycle.json()["resilience"]["primary_route_id"]
        replacement = client.post(
            f"/api/v1/missions/{mission_id}/routes/{replacement_id}/activate"
        )
        assert replacement.status_code == 200, replacement.text
        prior = client.get(f"/api/v1/missions/{mission_id}/routes/{primary_id}")
        assert prior.json()["status"] == "ABORTED"


def test_invalid_candidate_mission_vehicle_and_failed_activation() -> None:
    with TestClient(app) as client:
        mission_id, vehicle_id = create_mission_and_vehicle(client, "Route validation")
        payload = candidate_payload(vehicle_id, high_failure=True)
        generated = client.post(
            f"/api/v1/missions/{mission_id}/routes/candidates", json=payload
        )
        assert generated.status_code == 201, generated.text
        failed_candidate = generated.json()["candidates"][0]
        assert failed_candidate["viable"] is False
        activation = client.post(
            f"/api/v1/missions/{mission_id}/routes/{failed_candidate['route_id']}/activate"
        )
        assert activation.status_code == 409

        wrong_vehicle = client.post(
            f"/api/v1/missions/{mission_id}/routes/candidates",
            json=candidate_payload(str(uuid4())),
        )
        assert wrong_vehicle.status_code == 404
        missing_mission = client.post(
            f"/api/v1/missions/{uuid4()}/routes/candidates",
            json=candidate_payload(vehicle_id),
        )
        assert missing_mission.status_code == 404

        invalid_geometry = candidate_payload(vehicle_id)
        invalid_geometry["candidates"] = [
            {
                **invalid_geometry["candidates"][0],
                "geometry": [
                    {"latitude": 0.1, "longitude": 0.1},
                    {"latitude": 1, "longitude": 1},
                ],
            }
        ]
        invalid = client.post(
            f"/api/v1/missions/{mission_id}/routes/candidates",
            json=invalid_geometry,
        )
        assert invalid.status_code == 422


def test_monitor_detects_deviation_and_persists_replan_events() -> None:
    with TestClient(app) as client:
        mission_id, vehicle_id = create_mission_and_vehicle(client, "Deviation monitor")
        generated = client.post(
            f"/api/v1/missions/{mission_id}/routes/candidates",
            json=candidate_payload(vehicle_id),
        )
        primary_id = generated.json()["resilience"]["primary_route_id"]
        activation = client.post(
            f"/api/v1/missions/{mission_id}/routes/{primary_id}/activate"
        )
        assert activation.status_code == 200
        healthy = run_monitor(mission_id, primary_id)
        assert healthy["health"] == "HEALTHY"
        assert healthy["replan_required"] is False

        telemetry = client.post(
            f"/api/v1/missions/{mission_id}/telemetry",
            json={
                "vehicle_id": vehicle_id,
                "timestamp": "2026-10-02T12:00:00Z",
                "latitude": 10,
                "longitude": 10,
                "speed": 10,
            },
        )
        assert telemetry.status_code == 201, telemetry.text
        degraded = run_monitor(mission_id, primary_id)
        assert degraded["health"] == "DEGRADED"
        assert degraded["deviation_meters"] > 100
        assert degraded["replan_required"] is True
        event_types = {
            event["event_type"]
            for event in client.get(f"/api/v1/missions/{mission_id}/events?limit=50").json()["items"]
        }
        assert "ROUTE_DEVIATION" in event_types
        assert "REPLAN_TRIGGERED" in event_types
        assert client.post(
            f"/api/v1/missions/{mission_id}/routes/{primary_id}/activate"
        ).status_code == 409
        resilience = client.get(
            f"/api/v1/missions/{mission_id}/routes/resilience"
            f"?vehicle_id={vehicle_id}&planning_cycle_id={generated.json()['planning_cycle_id']}"
        )
        assert resilience.status_code == 200
        assert primary_id not in {
            resilience.json()["primary_route_id"],
            resilience.json()["backup_route_id"],
            resilience.json()["contingency_route_id"],
        }
        degraded_detail = client.get(
            f"/api/v1/missions/{mission_id}/routes/{primary_id}"
        )
        assert degraded_detail.status_code == 200
        assert degraded_detail.json()["resilience_role"] is None
        route_list = client.get(
            f"/api/v1/missions/{mission_id}/routes?vehicle_id={vehicle_id}&role=PRIMARY"
        )
        assert route_list.status_code == 200
        assert all(item["status"] != "DEGRADED" for item in route_list.json()["items"])
        state = client.get(f"/api/v1/missions/{mission_id}/state").json()
        assert state["mission_id"] == mission_id


def test_monitor_marks_failed_route_and_emits_route_failure_event() -> None:
    with TestClient(app) as client:
        mission_id, vehicle_id = create_mission_and_vehicle(client, "Failure monitor")
        generated = client.post(
            f"/api/v1/missions/{mission_id}/routes/candidates",
            json=candidate_payload(vehicle_id, high_failure=True),
        )
        route_id = generated.json()["candidates"][0]["route_id"]
        failure = run_monitor(mission_id, route_id)
        assert failure["health"] == "FAILED"
        assert failure["failure_detected"] is True
        assert failure["replan_required"] is True
        activation = client.post(
            f"/api/v1/missions/{mission_id}/routes/{route_id}/activate"
        )
        assert activation.status_code == 409
        route_failed = client.get(
            f"/api/v1/missions/{mission_id}/events?event_type=ROUTE_FAILED"
        )
        assert route_failed.status_code == 200
        assert any(event["payload"]["route_id"] == route_id for event in route_failed.json()["items"])


def test_mission_prediction_does_not_apply_to_unrelated_route_but_scoped_one_does() -> None:
    with TestClient(app) as client:
        mission_id, vehicle_id = create_mission_and_vehicle(client, "Prediction scope")
        generated = client.post(
            f"/api/v1/missions/{mission_id}/routes/candidates",
            json=candidate_payload(vehicle_id),
        )
        assert generated.status_code == 201, generated.text
        route_id = generated.json()["candidates"][0]["route_id"]

        async def insert_prediction(route_scope: UUID | None) -> None:
            engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            try:
                async with session_factory() as session:
                    async with session.begin():
                        session.add(
                            Prediction(
                                mission_id=UUID(mission_id),
                                route_id=route_scope,
                                prediction_type=PredictionType.ROUTE_FAILURE,
                                predicted_value={
                                    "status": "AVAILABLE",
                                    "value": {"risk_level": "HIGH"},
                                    "severity": "HIGH",
                                    "reason": None,
                                    "missing_inputs": [],
                                },
                                probability=0.95,
                                confidence=0.8,
                                factors=[],
                                source="scope-test",
                                model_name="scope-test",
                                model_version="scope-test",
                            )
                        )
            finally:
                await engine.dispose()

        asyncio.run(insert_prediction(None))
        mission_only = run_monitor(mission_id, route_id)
        assert mission_only["failure_detected"] is False

        asyncio.run(insert_prediction(UUID(route_id)))
        route_scoped = run_monitor(mission_id, route_id)
        assert route_scoped["failure_detected"] is True
        assert route_scoped["health"] == "FAILED"


def test_backup_degradation_emits_event_and_replan_trigger() -> None:
    with TestClient(app) as client:
        mission_id, vehicle_id = create_mission_and_vehicle(client, "Backup monitor")
        generated = client.post(
            f"/api/v1/missions/{mission_id}/routes/candidates",
            json=candidate_payload(vehicle_id),
        )
        body = generated.json()
        primary_id = body["resilience"]["primary_route_id"]
        backup_id = body["resilience"]["backup_route_id"]

        async def make_backup_unsafe() -> None:
            engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            try:
                async with session_factory() as session:
                    async with session.begin():
                        candidate = await session.scalar(
                            select(RouteCandidate).where(
                                RouteCandidate.route_id == UUID(backup_id)
                            )
                        )
                        assert candidate is not None
                        candidate.predicted_failure_probability = 0.95
            finally:
                await engine.dispose()

        asyncio.run(make_backup_unsafe())
        activated = client.post(
            f"/api/v1/missions/{mission_id}/routes/{primary_id}/activate"
        )
        assert activated.status_code == 200
        health = run_monitor(mission_id, primary_id)
        assert health["backup_degraded"] is True
        assert health["replan_required"] is True
        resilience = client.get(
            f"/api/v1/missions/{mission_id}/routes/resilience"
            f"?vehicle_id={vehicle_id}&planning_cycle_id={body['planning_cycle_id']}"
        )
        assert resilience.status_code == 200
        assert resilience.json()["backup_route_id"] != backup_id
        event_types = {
            event["event_type"]
            for event in client.get(f"/api/v1/missions/{mission_id}/events?limit=50").json()["items"]
        }
        assert "BACKUP_ROUTE_DEGRADED" in event_types
        assert "REPLAN_TRIGGERED" in event_types