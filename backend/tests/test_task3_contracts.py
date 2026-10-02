"""Database-free contracts for the Task 3 event infrastructure."""

import json
import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from redis.exceptions import ConnectionError as RedisConnectionError

from app.models import Event
from app.models.enums import MissionStatus
from app.schemas.events import EventCreate, EventType
from app.schemas.telemetry import TelemetryCreate
from app.services.event_bus import EventPublisher, mission_channel
from app.services.event_service import EventService, validate_transition
from app.services.websocket_manager import WebSocketManager


def test_event_contract_rejects_unknown_type_and_naive_timestamp() -> None:
    with pytest.raises(ValidationError):
        EventCreate(event_type="UNKNOWN", source="test")
    with pytest.raises(ValidationError):
        EventCreate(
            event_type=EventType.PLAN_CREATED,
            source="test",
            timestamp=datetime(2026, 1, 1),
        )


def test_telemetry_contract_validates_coordinates_speed_and_heading() -> None:
    base = {
        "vehicle_id": str(uuid4()),
        "timestamp": "2026-01-01T00:00:00Z",
        "latitude": 91,
        "longitude": 0,
        "speed": 1,
    }
    with pytest.raises(ValidationError):
        TelemetryCreate(**base)
    with pytest.raises(ValidationError):
        TelemetryCreate(**{**base, "latitude": 0, "speed": -1})
    with pytest.raises(ValidationError):
        TelemetryCreate(**{**base, "latitude": 0, "heading": 360})
    with pytest.raises(ValidationError):
        TelemetryCreate(**{**base, "latitude": float("nan"), "longitude": 0})


def test_event_publisher_serializes_normalized_envelope_and_channel() -> None:
    mission_id = uuid4()
    event_id = uuid4()
    redis = Mock()
    redis.publish = AsyncMock(return_value=1)
    event = Event(
        id=event_id,
        mission_id=mission_id,
        event_type=EventType.PLAN_CREATED.value,
        source="unit-test",
        payload={"sequence": 1},
    )
    event.correlation_id = uuid4()
    event.occurred_at = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
    event.created_at = event.occurred_at

    import asyncio

    asyncio.run(EventPublisher(redis).publish(event))

    channel, serialized = redis.publish.await_args.args
    body = json.loads(serialized)
    assert channel == f"sentinel:mission:{mission_id}:events"
    assert channel == mission_channel(mission_id)
    assert body["type"] == "MISSION_EVENT"
    assert body["mission_id"] == str(mission_id)
    assert body["event"]["event_id"] == str(event_id)
    assert body["event"]["event_type"] == "PLAN_CREATED"


def test_mission_transition_rules_are_explicit() -> None:
    validate_transition(MissionStatus.CREATED, MissionStatus.DISPATCHED)
    validate_transition(MissionStatus.DISPATCHED, MissionStatus.ACTIVE)
    validate_transition(MissionStatus.ACTIVE, MissionStatus.COMPLETED)
    validate_transition(MissionStatus.ACTIVE, MissionStatus.CANCELLED)
    with pytest.raises(HTTPException) as error:
        validate_transition(MissionStatus.CREATED, MissionStatus.COMPLETED)
    assert error.value.status_code == 409


def test_publisher_failure_returns_controlled_error_after_commit_boundary() -> None:
    db = AsyncMock()
    mission = Mock(status=MissionStatus.CREATED)
    db.scalar.return_value = mission
    transaction = AsyncMock()
    db.begin = Mock(return_value=transaction)
    db.add = Mock()
    publisher = Mock()
    publisher.publish = AsyncMock(side_effect=RedisConnectionError("unavailable"))
    service = EventService(publisher)
    payload = EventCreate(event_type=EventType.PLAN_CREATED, source="unit-test")

    with pytest.raises(HTTPException) as error:
        asyncio.run(service.create(db, uuid4(), payload))

    assert error.value.status_code == 503
    assert "persisted" in error.value.detail
    db.add.assert_called_once()
    transaction.__aexit__.assert_awaited_once()


def test_websocket_broadcast_is_mission_isolated() -> None:
    class Socket:
        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = []

        async def send_json(self, message: dict[str, object]) -> None:
            self.messages.append(message)

    first_mission = uuid4()
    second_mission = uuid4()
    first_socket = Socket()
    second_socket = Socket()
    manager = WebSocketManager()
    manager._connections[first_mission].add(first_socket)
    manager._connections[second_mission].add(second_socket)

    asyncio.run(manager.broadcast(first_mission, {"mission_id": str(first_mission)}))

    assert len(first_socket.messages) == 1
    assert second_socket.messages == []