"""Insert deterministic, clearly synthetic development/simulation records."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncio
from datetime import datetime, timezone
from uuid import UUID

from geoalchemy2 import WKTElement

from app.db.session import async_session_factory, engine
from app.models import (
    Event,
    Hazard,
    Hospital,
    Incident,
    Mission,
    Shelter,
    TrafficSignal,
    Vehicle,
)
from app.models.enums import (
    HazardStatus,
    HazardType,
    IncidentType,
    MissionStatus,
    OperationalStatus,
    VehicleStatus,
    VehicleType,
)


def seed_records() -> list[object]:
    """Return fixed-ID records describing a synthetic emergency scenario."""
    mission_id = UUID("11111111-1111-4111-8111-111111111111")
    mission = Mission(
        id=mission_id,
        status=MissionStatus.ACTIVE,
        priority=2,
        objective="DEVELOPMENT SIMULATION: coordinate a response to a synthetic fire.",
    )
    occurred_at = datetime(2026, 1, 1, tzinfo=timezone.utc)

    vehicles = [
        Vehicle(
            id=UUID(f"22222222-2222-4222-8222-00000000000{suffix}"),
            mission_id=mission_id,
            vehicle_type=vehicle_type,
            status=VehicleStatus.AVAILABLE,
            call_sign=f"SIM-{call_sign}",
            capability={"simulation": True, **capability},
            current_location=WKTElement(f"POINT({x} {y})", srid=4326),
        )
        for suffix, (vehicle_type, call_sign, x, y, capability) in enumerate(
            (
                (VehicleType.AMBULANCE, "AMB-01", 0.01, 0.01, {"medical": True}),
                (VehicleType.FIRE_TRUCK, "FIRE-01", 0.02, 0.01, {"water_liters": 4000}),
                (VehicleType.POLICE, "POL-01", 0.03, 0.01, {"traffic_control": True}),
            ),
            start=1,
        )
    ]

    hospitals = [
        Hospital(
            id=UUID(f"33333333-3333-4333-8333-00000000000{suffix}"),
            name=f"Development Simulation Hospital {suffix}",
            location=WKTElement(f"POINT({x} {y})", srid=4326),
            capacity_total=100,
            capacity_available=70,
            capability={"simulation": True, "emergency": True},
            operational_status=OperationalStatus.OPERATIONAL,
        )
        for suffix, x, y in ((1, 0.04, 0.02), (2, 0.05, 0.02))
    ]
    shelters = [
        Shelter(
            id=UUID(f"44444444-4444-4444-8444-00000000000{suffix}"),
            name=f"Development Simulation Shelter {suffix}",
            location=WKTElement(f"POINT({x} {y})", srid=4326),
            capacity_total=500,
            capacity_available=450,
            operational_status=OperationalStatus.OPERATIONAL,
        )
        for suffix, x, y in ((1, 0.06, 0.03), (2, 0.07, 0.03))
    ]
    traffic_signals = [
        TrafficSignal(
            id=UUID(f"55555555-5555-4555-8555-00000000000{suffix}"),
            external_id=f"SIM-SIGNAL-{suffix:03d}",
            location=WKTElement(f"POINT({x} {y})", srid=4326),
            current_phase="SIMULATED_GREEN",
            signal_metadata={"simulation": True},
            enabled=True,
        )
        for suffix, x, y in ((1, 0.01, 0.02), (2, 0.02, 0.02), (3, 0.03, 0.02))
    ]

    incident = Incident(
        id=UUID("66666666-6666-4666-8666-000000000001"),
        mission_id=mission_id,
        type=IncidentType.FIRE,
        severity=3,
        description="Synthetic development fire incident; not a real event.",
        location=WKTElement("POINT(0.02 0.03)", srid=4326),
        occurred_at=occurred_at,
        active=True,
    )
    hazard = Hazard(
        id=UUID("77777777-7777-4777-8777-000000000001"),
        mission_id=mission_id,
        hazard_type=HazardType.FIRE,
        severity=2,
        geometry=WKTElement(
            "MULTIPOLYGON(((0.01 0.02, 0.03 0.02, 0.03 0.04, "
            "0.01 0.04, 0.01 0.02)))",
            srid=4326,
        ),
        start_time=occurred_at,
        expected_end_time=None,
        status=HazardStatus.ACTIVE,
        description="Synthetic development hazard extent.",
    )
    events = [
        Event(
            id=UUID(f"88888888-8888-4888-8888-00000000000{suffix}"),
            mission_id=mission_id,
            event_type=event_type,
            source="development-seed",
            occurred_at=occurred_at,
            payload={"simulation": True},
        )
        for suffix, event_type in enumerate(
            ("PLAN_CREATED", "VEHICLE_POSITION_UPDATE", "PREDICTION_UPDATED"),
            start=1,
        )
    ]
    return [mission, incident, *vehicles, *hospitals, *shelters, *traffic_signals, hazard, *events]


async def seed() -> None:
    async with async_session_factory() as session:
        async with session.begin():
            for record in seed_records():
                await session.merge(record)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())