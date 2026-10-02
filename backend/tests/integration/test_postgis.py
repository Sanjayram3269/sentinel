"""PostgreSQL/PostGIS integration coverage; requires TEST_DATABASE_URL."""

import asyncio
import os
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, TypeVar
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from geoalchemy2 import WKTElement
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.main import app
from app.models import Event, Incident, Mission, Route, Vehicle, VehicleTelemetry
from app.models.enums import (
    IncidentType,
    MissionStatus,
    RouteStatus,
    VehicleStatus,
    VehicleType,
)

pytestmark = pytest.mark.integration
BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
ResultT = TypeVar("ResultT")


@pytest.fixture(scope="module", autouse=True)
def upgrade_test_database() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip("set TEST_DATABASE_URL to run PostGIS integration tests")
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    command.upgrade(config, "head")


def run_async(awaitable: Coroutine[Any, Any, ResultT]) -> ResultT:
    return asyncio.run(awaitable)


def make_session_factory() -> tuple[async_sessionmaker[AsyncSession], Any]:
    url = TEST_DATABASE_URL or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("TEST_DATABASE_URL or DATABASE_URL must be set for PostGIS integration tests.")
    engine = create_async_engine(url, pool_pre_ping=True)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    return session_factory, engine


async def persist(*entities: Any) -> list[UUID]:
    session_factory, engine = make_session_factory()
    try:
        async with session_factory() as session:
            async with session.begin():
                session.add_all(entities)
                await session.flush()
                identifiers = [entity.id for entity in entities]
        return identifiers
    finally:
        await engine.dispose()


def test_migration_applies_and_revision_is_current() -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    command.upgrade(config, "head")
    command.current(config)


def test_mission_creation_persists() -> None:
    mission = Mission(objective="Integration-test response", priority=2)
    (mission_id,) = run_async(persist(mission))

    async def read_back() -> None:
        session_factory, engine = make_session_factory()
        try:
            async with session_factory() as session:
                result = await session.get(Mission, mission_id)
                assert result is not None
                assert result.status is MissionStatus.CREATED
                assert result.objective == "Integration-test response"
        finally:
            await engine.dispose()

    run_async(read_back())


def test_incident_links_to_mission() -> None:
    mission = Mission(objective="Incident relationship test")
    incident = Incident(
        mission=mission,
        type=IncidentType.FIRE,
        severity=3,
        description="Simulated incident",
        location=WKTElement("POINT(0 0)", srid=4326),
    )
    mission_id, incident_id = run_async(persist(mission, incident))

    async def read_back() -> None:
        session_factory, engine = make_session_factory()
        try:
            async with session_factory() as session:
                result = await session.get(Incident, incident_id)
                assert result is not None
                assert result.mission_id == mission_id
        finally:
            await engine.dispose()

    run_async(read_back())


def test_vehicle_links_to_mission() -> None:
    mission = Mission(objective="Vehicle relationship test")
    vehicle = Vehicle(
        mission=mission,
        vehicle_type=VehicleType.AMBULANCE,
        status=VehicleStatus.AVAILABLE,
        call_sign=f"TEST-{uuid4()}",
        capability={"simulation": True},
    )
    mission_id, vehicle_id = run_async(persist(mission, vehicle))

    async def read_back() -> None:
        session_factory, engine = make_session_factory()
        try:
            async with session_factory() as session:
                result = await session.get(Vehicle, vehicle_id)
                assert result is not None
                assert result.mission_id == mission_id
        finally:
            await engine.dispose()

    run_async(read_back())


def test_vehicle_telemetry_persists() -> None:
    vehicle = Vehicle(
        vehicle_type=VehicleType.RESCUE,
        call_sign=f"TEST-{uuid4()}",
    )
    telemetry = VehicleTelemetry(
        vehicle=vehicle,
        position=WKTElement("POINT(0.1 0.2)", srid=4326),
        source="integration-test",
    )
    _, telemetry_id = run_async(persist(vehicle, telemetry))

    async def read_back() -> None:
        session_factory, engine = make_session_factory()
        try:
            async with session_factory() as session:
                row = (
                    await session.execute(
                        select(VehicleTelemetry, Vehicle.call_sign)
                        .join(Vehicle)
                        .where(VehicleTelemetry.id == telemetry_id)
                    )
                ).one()
                assert row.VehicleTelemetry.source == "integration-test"
                assert row.call_sign == vehicle.call_sign
        finally:
            await engine.dispose()

    run_async(read_back())


def test_postgis_accepts_point_and_linestring_geometries() -> None:
    mission = Mission(objective="Spatial type integration test")
    vehicle = Vehicle(
        mission=mission,
        vehicle_type=VehicleType.FIRE_TRUCK,
        call_sign=f"TEST-{uuid4()}",
    )
    incident = Incident(
        mission=mission,
        type=IncidentType.FIRE,
        severity=2,
        description="Synthetic spatial test incident",
        location=WKTElement("POINT(0.1 0.2)", srid=4326),
    )
    route = Route(
        mission=mission,
        vehicle=vehicle,
        status=RouteStatus.ACTIVE,
        name="Synthetic test route",
        geometry=WKTElement("LINESTRING(0 0, 0.1 0.2)", srid=4326),
        distance_meters=100,
        estimated_duration_seconds=60,
    )
    _, _, incident_id, route_id = run_async(persist(mission, vehicle, incident, route))

    async def verify() -> None:
        session_factory, engine = make_session_factory()
        try:
            async with session_factory() as session:
                point_srid, point_type = (
                    await session.execute(
                        select(
                            func.ST_SRID(Incident.location),
                            func.GeometryType(Incident.location),
                        ).where(Incident.id == incident_id)
                    )
                ).one()
                line_srid, line_type = (
                    await session.execute(
                        select(
                            func.ST_SRID(Route.geometry),
                            func.GeometryType(Route.geometry),
                        ).where(Route.id == route_id)
                    )
                ).one()
                assert (point_srid, point_type) == (4326, "POINT")
                assert (line_srid, line_type) == (4326, "LINESTRING")
        finally:
            await engine.dispose()

    run_async(verify())


def test_mission_api_creates_and_reads_mission() -> None:
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/missions",
            json={"objective": "API integration test response", "priority": 1},
        )
        assert created.status_code == 201
        mission_id = created.json()["id"]
        fetched = client.get(f"/api/v1/missions/{mission_id}")

    assert fetched.status_code == 200
    assert fetched.json()["objective"] == "API integration test response"


def test_mission_state_returns_aggregate() -> None:
    mission = Mission(objective="Mission state aggregation test")
    vehicle = Vehicle(
        mission=mission,
        vehicle_type=VehicleType.POLICE,
        call_sign=f"TEST-{uuid4()}",
        current_location=WKTElement("POINT(0.2 0.3)", srid=4326),
    )
    incident = Incident(
        mission=mission,
        type=IncidentType.ACCIDENT,
        severity=1,
        description="Synthetic aggregate incident",
        location=WKTElement("POINT(0.2 0.3)", srid=4326),
    )
    route = Route(
        mission=mission,
        vehicle=vehicle,
        status=RouteStatus.ACTIVE,
        name="Synthetic aggregate route",
        geometry=WKTElement("LINESTRING(0.2 0.3, 0.3 0.4)", srid=4326),
        distance_meters=100,
        estimated_duration_seconds=60,
    )
    event = Event(
        mission=mission,
        event_type="PLAN_CREATED",
        source="integration-test",
        payload={"simulation": True},
    )
    mission_id, _, _, _, _ = run_async(persist(mission, vehicle, incident, route, event))

    with TestClient(app) as client:
        response = client.get(f"/api/v1/missions/{mission_id}/state")

    assert response.status_code == 200
    body = response.json()
    assert body["mission"]["id"] == str(mission_id)
    assert len(body["incidents"]) == 1
    assert body["vehicles"][0]["call_sign"] == vehicle.call_sign
    assert len(body["active_routes"]) == 1
    assert body["latest_events"][0]["event_type"] == "PLAN_CREATED"