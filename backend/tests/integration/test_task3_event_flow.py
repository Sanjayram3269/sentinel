"""Database and Redis integration coverage for Task 3 event-driven flows."""

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.main import app

pytestmark = pytest.mark.integration
BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@pytest.fixture(scope="module", autouse=True)
def upgrade_test_database() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip("set TEST_DATABASE_URL to run Task 3 database/Redis integration tests")
    command.upgrade(Config(str(BACKEND_DIR / "alembic.ini")), "head")


def create_mission(client: TestClient, objective: str = "Task 3 integration") -> str:
    response = client.post("/api/v1/missions", json={"objective": objective})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_initial_mission_state_is_empty_and_deterministic() -> None:
    with TestClient(app) as client:
        mission_id = create_mission(client, "Initial state")
        response = client.get(f"/api/v1/missions/{mission_id}/state")
        assert response.status_code == 200
        state = response.json()
        assert state["mission_id"] == mission_id
        assert state["status"] == "CREATED"
        assert state["incident"] is None
        assert state["vehicles"] == []
        assert state["routes"] == []
        assert state["active_plan"] is None
        assert state["latest_events"] == []


def test_event_create_history_filter_and_missing_mission() -> None:
    with TestClient(app) as client:
        mission_id = create_mission(client)
        created = client.post(
            f"/api/v1/missions/{mission_id}/events",
            json={
                "event_type": "PLAN_CREATED",
                "timestamp": "2026-10-01T10:00:00Z",
                "source": "integration-test",
                "payload": {"plan": 1},
            },
        )
        assert created.status_code == 201, created.text
        event = created.json()
        assert event["event_id"]
        assert event["mission_id"] == mission_id
        assert event["correlation_id"]
        assert event["timestamp"] == "2026-10-01T10:00:00Z"

        second = client.post(
            f"/api/v1/missions/{mission_id}/events",
            json={
                "event_type": "PLAN_APPROVED",
                "source": "integration-test",
                "payload": {},
            },
        )
        assert second.status_code == 201, second.text
        history = client.get(f"/api/v1/missions/{mission_id}/events?limit=1")
        assert history.status_code == 200
        assert len(history.json()["items"]) == 1
        assert history.json()["has_more"] is True
        filtered = client.get(
            f"/api/v1/missions/{mission_id}/events?event_type=PLAN_CREATED"
        )
        assert filtered.status_code == 200
        assert [item["event_type"] for item in filtered.json()["items"]] == [
            "PLAN_CREATED"
        ]
        window = client.get(
            f"/api/v1/missions/{mission_id}/events"
            "?after=2026-10-01T09:59:00Z&before=2026-10-01T10:01:00Z"
        )
        assert window.status_code == 200
        assert [item["event_type"] for item in window.json()["items"]] == [
            "PLAN_CREATED"
        ]
        invalid_create = client.post(
            f"/api/v1/missions/{uuid4()}/events",
            json={"event_type": "PLAN_CREATED", "source": "integration-test"},
        )
        assert invalid_create.status_code == 404
        invalid_type = client.post(
            f"/api/v1/missions/{mission_id}/events",
            json={"event_type": "NOT_A_SENTINEL_EVENT", "source": "integration-test"},
        )
        assert invalid_type.status_code == 422
        missing = client.get(f"/api/v1/missions/{uuid4()}/events")
        assert missing.status_code == 404


def test_status_events_apply_only_allowed_mission_transitions() -> None:
    with TestClient(app) as client:
        mission_id = create_mission(client)

        invalid = client.post(
            f"/api/v1/missions/{mission_id}/events",
            json={
                "event_type": "MISSION_STATUS_CHANGED",
                "source": "integration-test",
                "payload": {"status": "COMPLETED"},
            },
        )
        assert invalid.status_code == 409

        for status_value in ("DISPATCHED", "ACTIVE"):
            response = client.post(
                f"/api/v1/missions/{mission_id}/events",
                json={
                    "event_type": "MISSION_STATUS_CHANGED",
                    "source": "integration-test",
                    "payload": {"status": status_value},
                },
            )
            assert response.status_code == 201, response.text

        state = client.get(f"/api/v1/missions/{mission_id}/state")
        assert state.status_code == 200
        assert state.json()["status"] == "ACTIVE"
        assert state.json()["mission"]["status"] == "ACTIVE"
        assert len(state.json()["latest_events"]) == 2
        completed = client.post(
            f"/api/v1/missions/{mission_id}/events",
            json={
                "event_type": "MISSION_STATUS_CHANGED",
                "source": "integration-test",
                "payload": {"status": "COMPLETED"},
            },
        )
        assert completed.status_code == 201
        assert client.get(f"/api/v1/missions/{mission_id}/state").json()["status"] == "COMPLETED"


def test_telemetry_updates_latest_mission_state_and_validates_input() -> None:
    with TestClient(app) as client:
        mission_id = create_mission(client, "Telemetry mission")
        vehicle_response = client.post(
            "/api/v1/vehicles",
            json={
                "mission_id": mission_id,
                "vehicle_type": "AMBULANCE",
                "call_sign": f"TASK3-{uuid4()}",
            },
        )
        assert vehicle_response.status_code == 201, vehicle_response.text
        vehicle_id = vehicle_response.json()["id"]

        telemetry = client.post(
            f"/api/v1/missions/{mission_id}/telemetry",
            json={
                "vehicle_id": vehicle_id,
                "timestamp": "2026-10-02T10:00:00Z",
                "latitude": 12.9716,
                "longitude": 77.5946,
                "speed": 42.3,
                "heading": 90,
                "status": "EN_ROUTE",
            },
        )
        assert telemetry.status_code == 201, telemetry.text
        state = client.get(f"/api/v1/missions/{mission_id}/state").json()
        latest_vehicle = state["vehicles"][0]
        assert latest_vehicle["id"] == vehicle_id
        assert latest_vehicle["status"] == "EN_ROUTE"
        assert latest_vehicle["latitude"] == 12.9716
        assert latest_vehicle["longitude"] == 77.5946
        assert latest_vehicle["current_location"] == {
            "latitude": 12.9716,
            "longitude": 77.5946,
        }
        assert latest_vehicle["speed"] == 42.3
        assert {item["event_type"] for item in state["latest_events"]} >= {
            "TELEMETRY_RECEIVED",
            "VEHICLE_POSITION_UPDATE",
        }
        telemetry_events = client.get(
            f"/api/v1/missions/{mission_id}/events?event_type=TELEMETRY_RECEIVED"
        ).json()["items"]
        position_events = client.get(
            f"/api/v1/missions/{mission_id}/events?event_type=VEHICLE_POSITION_UPDATE"
        ).json()["items"]
        assert telemetry_events[0]["correlation_id"] == position_events[0]["correlation_id"]

        bad_coordinates = client.post(
            f"/api/v1/missions/{mission_id}/telemetry",
            json={
                "vehicle_id": vehicle_id,
                "timestamp": "2026-10-02T10:01:00Z",
                "latitude": 91,
                "longitude": 0,
                "speed": 2,
            },
        )
        assert bad_coordinates.status_code == 422
        bad_speed = client.post(
            f"/api/v1/missions/{mission_id}/telemetry",
            json={
                "vehicle_id": vehicle_id,
                "timestamp": "2026-10-02T10:01:00Z",
                "latitude": 0,
                "longitude": 0,
                "speed": -1,
            },
        )
        assert bad_speed.status_code == 422


def test_telemetry_rejects_vehicle_outside_mission() -> None:
    with TestClient(app) as client:
        first_mission = create_mission(client, "Telemetry owner")
        second_mission = create_mission(client, "Different mission")
        vehicle = client.post(
            "/api/v1/vehicles",
            json={
                "mission_id": second_mission,
                "vehicle_type": "POLICE",
                "call_sign": f"TASK3-{uuid4()}",
            },
        )
        response = client.post(
            f"/api/v1/missions/{first_mission}/telemetry",
            json={
                "vehicle_id": vehicle.json()["id"],
                "timestamp": "2026-10-02T10:00:00Z",
                "latitude": 0,
                "longitude": 0,
                "speed": 0,
            },
        )
        assert response.status_code == 409

        missing_vehicle = client.post(
            f"/api/v1/missions/{first_mission}/telemetry",
            json={
                "vehicle_id": str(uuid4()),
                "timestamp": "2026-10-02T10:00:00Z",
                "latitude": 0,
                "longitude": 0,
                "speed": 0,
            },
        )
        assert missing_vehicle.status_code == 404
        missing_mission = client.post(
            f"/api/v1/missions/{uuid4()}/telemetry",
            json={
                "vehicle_id": vehicle.json()["id"],
                "timestamp": "2026-10-02T10:00:00Z",
                "latitude": 0,
                "longitude": 0,
                "speed": 0,
            },
        )
        assert missing_mission.status_code == 404


def test_websocket_streams_are_mission_scoped_and_normalized() -> None:
    with TestClient(app) as client:
        first_mission = create_mission(client, "WebSocket first")
        second_mission = create_mission(client, "WebSocket second")
        with client.websocket_connect(f"/ws/missions/{first_mission}") as first_socket:
            with client.websocket_connect(f"/ws/missions/{first_mission}") as first_peer:
                with client.websocket_connect(
                    f"/ws/missions/{second_mission}"
                ) as second_socket:
                    first_event = client.post(
                        f"/api/v1/missions/{first_mission}/events",
                        json={"event_type": "PLAN_CREATED", "source": "ws-test"},
                    )
                    assert first_event.status_code == 201
                    first_message = first_socket.receive_json()
                    peer_message = first_peer.receive_json()
                    assert first_message["type"] == "MISSION_EVENT"
                    assert first_message["mission_id"] == first_mission
                    assert first_message["event"]["event_id"] == first_event.json()["event_id"]
                    assert peer_message == first_message

                    second_event = client.post(
                        f"/api/v1/missions/{second_mission}/events",
                        json={"event_type": "PLAN_CREATED", "source": "ws-test"},
                    )
                    assert second_event.status_code == 201
                    second_message = second_socket.receive_json()
                    assert second_message["mission_id"] == second_mission
                    assert second_message["event"]["event_id"] == second_event.json()["event_id"]