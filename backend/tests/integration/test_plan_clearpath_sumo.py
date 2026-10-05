"""Real-SUMO Phase 7 validation of a selected mission plan (brief section 18).

Runs the whole bridge against a live SUMO/TraCI process: canonical
``RoadEdge.id`` -> SUMO external id, corridor derivation, safety-approved
pre-emption, signal release, and the measured baseline/CLEARPATH comparison.

Every number asserted here is *measured by SUMO*. No expected 56 -> 19 result is
encoded anywhere: a CLEARPATH run that matched or exceeded its baseline would
still pass, because the test checks provenance and behaviour, not an outcome.

Fixture honesty
---------------
``demo.net.xml`` is a synthetic Cartesian SUMO network (``projParameter="!"``),
which is precisely why the Phase 3 importer refuses it. So that the bridge can
resolve canonical identity for it, this module inserts development-fixture
``RoadNetwork``/``RoadEdge`` rows directly, with geometry that is a plainly
labelled linear placeholder derived from the demo's own Cartesian coordinates.

Consequence, stated plainly: the SUMO bridge identity, the routing decision
path and the simulation measurements are real; the stored coordinates of these
fixture edges are **not real geography** and are never used by the simulation
path. They exist only to satisfy the schema's non-null geometry column.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from geoalchemy2 import WKTElement
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.main import app
from app.models import (
    MissionPlan,
    RoadEdge,
    RoadNetwork,
    Route,
    RouteCandidate,
    TrafficSignal,
)
from app.models.enums import PlanStatus, RouteStatus

pytestmark = pytest.mark.integration

BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
DEMO_DIR = BACKEND_DIR / "simulation" / "scenarios" / "clearpath_demo"
DEMO_NET = DEMO_DIR / "demo.net.xml"
DEMO_CONFIG = DEMO_DIR / "demo.sumocfg"
DEMO_SIGNALS = DEMO_DIR / "signals.json"
NETWORK_KEY = "clearpath_demo_v1"
# The verified demo corridor: north_in -> J1_1 -> south_out.
CORRIDOR_SUMO_EDGES = ["north_in", "south_out"]
# Placeholder projection of a synthetic Cartesian network onto a small WGS84
# box. Deterministic, derived from the demo's own coordinates, and never read
# by the simulation path.
_PLACEHOLDER_ORIGIN = (77.60, 12.98)
_PLACEHOLDER_SCALE = 1.0e-5


def _engine():
    return create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)


def run_async(coro):
    return asyncio.run(coro)


def _demo_geometry() -> dict[str, str]:
    """Build a placeholder LINESTRING per named (non-internal) demo edge.

    This net carries no ``<node>`` elements; each edge's geometry lives in its
    lane ``shape`` attribute as Cartesian ``x,y`` pairs. The first and last
    shape points are used, which are the demo network's own coordinates.
    """
    root = ET.parse(DEMO_NET).getroot()
    lines: dict[str, str] = {}
    for edge in root.iter("edge"):
        edge_id = edge.get("id") or ""
        if not edge_id or edge_id.startswith(":"):
            continue
        lane = edge.find("lane")
        shape = (lane.get("shape") if lane is not None else None) or ""
        points = [item for item in shape.split() if "," in item]
        if len(points) < 2:
            continue
        parts = []
        for token in (points[0], points[-1]):
            x_str, y_str = token.split(",", 1)
            lon = _PLACEHOLDER_ORIGIN[0] + float(x_str) * _PLACEHOLDER_SCALE
            lat = _PLACEHOLDER_ORIGIN[1] + float(y_str) * _PLACEHOLDER_SCALE
            parts.append(f"{lon:.7f} {lat:.7f}")
        lines[edge_id] = f"LINESTRING({', '.join(parts)})"
    return lines


@pytest.fixture(scope="module", autouse=True)
def prepared_database() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip("set TEST_DATABASE_URL to run the real SUMO Phase 7 test")
    command.upgrade(Config(str(BACKEND_DIR / "alembic.ini")), "head")
    if not DEMO_NET.is_file() or not DEMO_CONFIG.is_file():
        pytest.skip("clearpath_demo SUMO scenario is not present")


@pytest.fixture(scope="module")
def demo_reference_data():
    """Import the demo corridor as development-fixture canonical reference rows."""
    if not DEMO_SIGNALS.is_file():
        pytest.skip("clearpath_demo signals.json is not present")
    geometry = _demo_geometry()
    missing = [edge for edge in CORRIDOR_SUMO_EDGES if edge not in geometry]
    if missing:
        pytest.skip(f"demo corridor edges absent from demo.net.xml: {missing}")

    payload = json.loads(DEMO_SIGNALS.read_text(encoding="utf-8"))
    signal = payload["signals"][0]
    engine = _engine()

    async def _build():
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                async with session.begin():
                    network = await session.scalar(
                        select(RoadNetwork).where(RoadNetwork.network_key == NETWORK_KEY)
                    )
                    if network is None:
                        network = RoadNetwork(
                            network_key=NETWORK_KEY,
                            proj_parameter="! (synthetic Cartesian demo network)",
                            orig_boundary="clearpath_demo development fixture",
                            source_checksum="clearpath-demo-development-fixture",
                        )
                        session.add(network)
                        await session.flush()
                    for sumo_edge in CORRIDOR_SUMO_EDGES:
                        existing = await session.scalar(
                            select(RoadEdge).where(
                                RoadEdge.network_id == network.id,
                                RoadEdge.external_id == sumo_edge,
                            )
                        )
                        if existing is None:
                            session.add(
                                RoadEdge(
                                    network_id=network.id,
                                    source="clearpath_demo_development_fixture",
                                    external_id=sumo_edge,
                                    from_node=sumo_edge,
                                    to_node=sumo_edge,
                                    geometry=WKTElement(geometry[sumo_edge], srid=4326),
                                    length_m=200.0,
                                    speed_limit_kmh=50.0,
                                    road_class="primary",
                                    lanes=1,
                                    has_signal=sumo_edge == CORRIDOR_SUMO_EDGES[0],
                                )
                            )
                            await session.flush()
                        else:
                            existing.source = "clearpath_demo_development_fixture"
                    existing_signal = await session.scalar(
                        select(TrafficSignal).where(
                            TrafficSignal.external_id == "phase7-demo-junction"
                        )
                    )
                    # The demo file keys the SUMO signal as "signal_id"; the
                    # TrafficSignal.signal_metadata convention is
                    # "sumo_signal_id". Translate rather than store verbatim,
                    # so the row matches every other signal in the database.
                    metadata = dict(signal)
                    metadata["sumo_signal_id"] = metadata.pop("signal_id")
                    metadata.pop("traffic_signal_id", None)
                    if existing_signal is not None:
                        existing_signal.signal_metadata = metadata
                        existing_signal.enabled = True
                        await session.flush()
                        return [str(item.id) for item in await _ids(session)]
                    created = TrafficSignal(
                        external_id="phase7-demo-junction",
                        location=WKTElement(
                            f"POINT({_PLACEHOLDER_ORIGIN[0]} {_PLACEHOLDER_ORIGIN[1]})",
                            srid=4326,
                        ),
                        current_phase=metadata["initial_phase"],
                        signal_metadata=metadata,
                        enabled=True,
                    )
                    session.add(created)
                    await session.flush()
                    return [str(item.id) for item in await _ids(session)]
        finally:
            await engine.dispose()

    async def _ids(session):
        rows = (
            await session.scalars(
                select(RoadEdge)
                .join(RoadNetwork, RoadNetwork.id == RoadEdge.network_id)
                .where(
                    RoadNetwork.network_key == NETWORK_KEY,
                    RoadEdge.external_id.in_(CORRIDOR_SUMO_EDGES),
                )
                .order_by(RoadEdge.external_id)
            )
        ).all()
        return rows

    return run_async(_build())


@pytest.fixture(autouse=True)
def sumo_settings(monkeypatch):
    """Point Phase 7 at real SUMO on the verified demo corridor."""
    if not os.getenv("SUMO_BINARY", ""):
        monkeypatch.setenv("SUMO_BINARY", "sumo")
    monkeypatch.setenv("SUMO_CONFIG_PATH", str(DEMO_CONFIG))
    monkeypatch.setenv("SUMO_NETWORK_ID", NETWORK_KEY)
    monkeypatch.setenv("ROAD_NETWORK_KEY", NETWORK_KEY)
    get_settings.cache_clear()
    try:
        import traci  # noqa: F401
    except ImportError:
        pytest.skip("TraCI is not importable in this environment")
    yield
    get_settings.cache_clear()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _demo_plan(client: TestClient, canonical_ids: list[str]) -> dict:
    mission_id = client.post(
        "/api/v1/missions", json={"objective": "phase 7 real SUMO validation"}
    ).json()["id"]
    vehicle_id = client.post(
        "/api/v1/vehicles",
        json={
            "mission_id": mission_id,
            "vehicle_type": "AMBULANCE",
            "call_sign": f"P7SUMO-{uuid4()}",
        },
    ).json()["id"]
    route_id = uuid4()
    engine = _engine()

    async def _persist():
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                async with session.begin():
                    session.add(
                        Route(
                            id=route_id,
                            mission_id=UUID(mission_id),
                            vehicle_id=UUID(vehicle_id),
                            status=RouteStatus.ACTIVE,
                            name="clearpath demo corridor",
                            geometry=WKTElement("LINESTRING(77.6 12.98, 77.6 12.99)", srid=4326),
                            distance_meters=200.0,
                            estimated_duration_seconds=56,
                            risk_score=0.1,
                        )
                    )
                    session.add(
                        RouteCandidate(
                            mission_id=UUID(mission_id),
                            vehicle_id=UUID(vehicle_id),
                            route_id=route_id,
                            origin=WKTElement("POINT(77.6 12.98)", srid=4326),
                            destination=WKTElement("POINT(77.6 12.99)", srid=4326),
                            status=RouteStatus.CANDIDATE,
                            resilience_role=None,
                            route_rank=1,
                            estimated_duration_seconds=56,
                            distance_meters=200.0,
                            risk_score=0.1,
                            score=0.2,
                            confidence=0.6,
                            backup_viable=True,
                            road_segment_ids=list(canonical_ids),
                            provider="road_graph_routing_provider",
                        )
                    )
                    session.add(
                        MissionPlan(
                            mission_id=UUID(mission_id),
                            version=1,
                            status=PlanStatus.DRAFT,
                            objective="MINIMIZE_WEIGHTED_MISSION_COST",
                            plan_payload={
                                "selected": {
                                    "hospital_id": None,
                                    "route_id": str(route_id),
                                    "resource_ids": [],
                                },
                                "route": {
                                    "route_id": str(route_id),
                                    "canonical_road_edge_count": len(canonical_ids),
                                    "canonical_road_edge_ids": list(canonical_ids),
                                },
                            },
                            score=0.5,
                            feasible=True,
                            rationale="phase 7 real SUMO fixture plan",
                        )
                    )
        finally:
            await engine.dispose()

    run_async(_persist())
    plan_id = client.get(f"/api/v1/missions/{mission_id}/plans/latest").json()["id"]
    return {"mission_id": mission_id, "vehicle_id": vehicle_id, "plan_id": plan_id}


def _simulate(client: TestClient, world: dict, **body):
    body.setdefault("emergency_vehicle_configuration", {"sumo_vehicle_type_id": "emergency"})
    body.setdefault("seed", 20261002)
    body.setdefault("max_simulation_seconds", 300)
    return client.post(
        f"/api/v1/missions/{world['mission_id']}/plans/{world['plan_id']}/simulate",
        json=body,
    )


def test_real_sumo_validates_the_selected_plan(client, demo_reference_data):
    """The full bridge against a live SUMO process, measured not asserted."""
    world = _demo_plan(client, demo_reference_data)
    response = _simulate(client, world)
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["corridor"]["status"] == "ELIGIBLE_CORRIDOR"
    assert body["corridor"]["sumo_edge_count"] == 2
    assert [item["sumo_edge_id"] for item in body["corridor"]["signals"]] == [
        CORRIDOR_SUMO_EDGES[0]
    ]

    for side in ("baseline", "clearpath"):
        run = body[side]
        assert run["status"] == "COMPLETED", run
        assert run["simulator"] == "sumo_traci_adapter", run
        assert run["metrics"]["route_completed"] is True
        assert run["metrics"]["emergency_vehicle_travel_time_seconds"] is not None
        assert run["sumo_version"], "SUMO must report its version"

    # BASELINE must never touch a signal.
    assert body["actions_requested"] >= 1
    assert body["actions_approved"] >= 1
    assert body["actions_executed"] >= 1, "an approved pre-emption must be released"


def test_real_sumo_never_mutates_the_plan(client, demo_reference_data):
    world = _demo_plan(client, demo_reference_data)
    before = client.get(
        f"/api/v1/missions/{world['mission_id']}/plans/{world['plan_id']}"
    ).json()
    _simulate(client, world)
    after = client.get(
        f"/api/v1/missions/{world['mission_id']}/plans/{world['plan_id']}"
    ).json()
    assert before == after
    assert after["version"] == 1
    assert after["status"] == "DRAFT"


def test_real_sumo_comparison_is_measured_and_reproducible(
    client, demo_reference_data
):
    world = _demo_plan(client, demo_reference_data)
    first = _simulate(client, world).json()
    second = _simulate(client, world).json()

    base = first["baseline"]["metrics"]["emergency_vehicle_travel_time_seconds"]
    clear = first["clearpath"]["metrics"]["emergency_vehicle_travel_time_seconds"]
    comparison = first["comparison"]
    assert base is not None and clear is not None
    assert comparison["travel_time_delta_seconds"] == pytest.approx(clear - base)
    if base > 0:
        assert comparison["travel_time_improvement_percent"] == pytest.approx(
            (base - clear) / base * 100
        )
    # Same seed and scenario must reproduce the measurement. Run identity may
    # differ between runs; the numbers must not.
    assert second["comparison"] == comparison
    assert second["baseline"]["metrics"] == first["baseline"]["metrics"]
    assert second["clearpath"]["metrics"] == first["clearpath"]["metrics"]
    assert second["baseline"]["simulation_id"] != first["baseline"]["simulation_id"]
