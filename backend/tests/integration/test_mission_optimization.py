"""Persistence and real-network coverage for Phase 6 (tests 19-25).

Runs against real PostGIS with the operator-imported ``osm_urban_v1`` network.
Route travel times, distances, and the canonical ``RoadEdge.id`` values asserted
here are the ones Phase 5 persisted: the optimizer reads them and never
recomputes them.

Hospital rows in these tests are **development fixtures**. SENTINEL has no
hospital dataset, and a fixture invented here is not a real Bengaluru hospital.
They are labelled as such at the point of creation and exist only so the
decision layer has something to rank.

A note on the fixture shape: every database read happens in module-scoped
fixtures that run *before* any ``TestClient`` exists, and the global engine is
disposed straight after. A ``TestClient`` owns one event loop; calling
``asyncio.run`` inside a test would bind the module-scoped engine pool to a
second, already-closed loop.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import networkx as nx
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from geoalchemy2 import WKTElement
from sqlalchemy import select

from app.db.session import async_session_factory
from app.main import app
from app.models import Hospital, RoadEdge, RoadNetwork
from app.models.enums import OperationalStatus
from app.services.ai_road_graph import clear_road_graph_cache, get_road_graph

pytestmark = pytest.mark.integration
BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
NETWORK_KEY = "osm_urban_v1"
NET_FILE = BACKEND_DIR / "simulation" / "networks" / "osm_urban_v1" / "osm.net.xml"

#: Marks the hospital fixtures as development data, not real hospitals.
FIXTURE_PREFIX = "OPT6-DEVFIX"
FIXTURE_NOTE = "development fixture hospital; not a real hospital"


def _dispose_global_engine() -> None:
    from app.db.session import engine as global_engine

    asyncio.run(global_engine.dispose())


def run_async(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module", autouse=True)
def prepared_database() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip("set TEST_DATABASE_URL to run mission optimization integration tests")
    command.upgrade(Config(str(BACKEND_DIR / "alembic.ini")), "head")
    if not NET_FILE.is_file():
        pytest.skip(f"real OSM network missing at {NET_FILE}")
    environment = os.environ.copy()
    environment["DATABASE_URL"] = TEST_DATABASE_URL
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/import_road_network.py",
            "--net",
            str(NET_FILE),
            "--network-key",
            NETWORK_KEY,
        ],
        cwd=BACKEND_DIR,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    _dispose_global_engine()
    clear_road_graph_cache()


def _farthest_pair(graph):
    """Two real graph nodes in the largest strongly connected component.

    Node ids are sorted before the component is chosen: a component is a
    ``set``, and set iteration order over strings varies between processes,
    which would silently change which pair these tests route between.
    """
    components = sorted(
        (sorted(component) for component in nx.strongly_connected_components(graph)),
        key=lambda nodes: (-len(nodes), nodes[0]),
    )
    nodes = components[0]
    coords = [(float(graph.nodes[n]["x"]), float(graph.nodes[n]["y"])) for n in nodes]
    best = None
    for index, first in enumerate(coords):
        for second in coords[index + 1 :]:
            span = (first[0] - second[0]) ** 2 + (first[1] - second[1]) ** 2
            if best is None or span > best[0]:
                best = (span, first, second)
    return best[1], best[2]


@pytest.fixture(scope="module")
def real_network():
    """Real graph endpoints and the canonical edge-id set, loaded once.

    ``get_road_graph`` returns the ``AiRoadGraph`` wrapper; the networkx
    algorithms operate on the underlying DiGraph.
    """

    async def _load():
        async with async_session_factory() as session:
            graph = (await get_road_graph(session, NETWORK_KEY)).graph
            edge_ids = {
                str(item)
                for item in (
                    await session.scalars(
                        select(RoadEdge.id)
                        .join(RoadNetwork, RoadNetwork.id == RoadEdge.network_id)
                        .where(RoadNetwork.network_key == NETWORK_KEY)
                    )
                ).all()
            }
            return graph, edge_ids

    graph, edge_ids = run_async(_load())
    origin, destination = _farthest_pair(graph)
    assert edge_ids, "the imported network must have canonical edges"
    # Release the pool created on this fixture's loop before any TestClient
    # builds one of its own.
    _dispose_global_engine()
    clear_road_graph_cache()
    return {"origin": origin, "destination": destination, "edge_ids": edge_ids}


@pytest.fixture(scope="module")
def fixture_hospitals(real_network):
    """Two development-fixture hospitals pinned to real graph node positions."""

    async def _ensure():
        async with async_session_factory() as session:
            existing = (
                await session.scalars(
                    select(Hospital).where(Hospital.name.like(f"{FIXTURE_PREFIX}-%"))
                )
            ).all()
            if existing:
                return [(str(row.id), row.name) for row in existing]
            points = [real_network["origin"], real_network["destination"]]
            created = []
            for index, (longitude, latitude) in enumerate(points):
                hospital = Hospital(
                    name=f"{FIXTURE_PREFIX}-{index}",
                    location=WKTElement(f"POINT({longitude} {latitude})", srid=4326),
                    capacity_total=20,
                    capacity_available=20 - index * 6,
                    capability={"capabilities": ["trauma", "emergency"], "emergency": True},
                    operational_status=OperationalStatus.OPERATIONAL,
                    reliability=0.95 - index * 0.15,
                )
                session.add(hospital)
                created.append((hospital, index))
            await session.flush()
            # Commit, not just flush: leaving the context manager would roll the
            # fixtures back and every later test would see an empty hospital set.
            await session.commit()
            return [(str(hospital.id), hospital.name) for hospital, _ in created]

    hospitals = run_async(_ensure())
    _dispose_global_engine()
    return hospitals


@pytest.fixture
def client():
    """One TestClient portal loop per test, matching the Phase 5 suites."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _isolate_engine_pool():
    yield
    _dispose_global_engine()


def _mission_with_vehicle(client, call_sign_prefix: str = "OPT"):
    mission_id = client.post(
        "/api/v1/missions", json={"objective": "phase 6 mission optimization"}
    ).json()["id"]
    vehicle = client.post(
        "/api/v1/vehicles",
        json={
            "mission_id": mission_id,
            "vehicle_type": "AMBULANCE",
            "call_sign": f"{call_sign_prefix}-{uuid4()}",
            "speed": 8.0,
        },
    )
    assert vehicle.status_code == 201, vehicle.text
    return mission_id, vehicle.json()["id"]


def _generate_routes(client, mission_id, vehicle_id, origin, destination):
    response = client.post(
        f"/api/v1/missions/{mission_id}/routes/candidates",
        json={
            "vehicle_id": vehicle_id,
            "origin": {"latitude": origin[1], "longitude": origin[0]},
            "destination": {"latitude": destination[1], "longitude": destination[0]},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _optimize(client, mission_id, **body):
    response = client.post(f"/api/v1/missions/{mission_id}/plans/optimize", json=body)
    assert response.status_code == 201, response.text
    return response.json()


# ======================================================================
# PERSISTENCE 19-22
# ======================================================================


def test_19_mission_plan_is_persisted(client, real_network, fixture_hospitals):
    mission_id, vehicle_id = _mission_with_vehicle(client)
    _generate_routes(
        client, mission_id, vehicle_id, real_network["origin"], real_network["destination"]
    )
    body = _optimize(
        client,
        mission_id,
        required_capabilities=["trauma"],
        required_vehicle_types=["AMBULANCE"],
    )
    assert body["feasible"] is True
    assert body["selected_hospital_id"] is not None
    assert body["selected_route_id"] is not None
    assert len(body["selected_resource_ids"]) == 1
    assert body["version"] == 1
    assert body["status"] == "DRAFT"
    assert body["objective"] == "MINIMIZE_WEIGHTED_MISSION_COST"

    stored = client.get(f"/api/v1/missions/{mission_id}/plans/{body['plan_id']}")
    assert stored.status_code == 200, stored.text
    assert stored.json()["id"] == body["plan_id"]

    latest = client.get(f"/api/v1/missions/{mission_id}/plans/latest")
    assert latest.status_code == 200, latest.text
    assert latest.json()["id"] == body["plan_id"]


def test_20_plan_retrieval_returns_the_selected_plan(client, real_network, fixture_hospitals):
    mission_id, vehicle_id = _mission_with_vehicle(client)
    _generate_routes(
        client, mission_id, vehicle_id, real_network["origin"], real_network["destination"]
    )
    created = _optimize(client, mission_id, required_capabilities=["trauma"])

    fetched = client.get(f"/api/v1/missions/{mission_id}/plans/{created['plan_id']}").json()
    assert fetched["mission_id"] == mission_id
    assert fetched["selected_hospital_id"] == created["selected_hospital_id"]
    assert fetched["selected_route_id"] == created["selected_route_id"]
    assert fetched["selected_resource_ids"] == created["selected_resource_ids"]
    assert fetched["score"] == created["score"]
    assert fetched["objective"] == created["objective"]
    assert fetched["confidence"] is None


def test_20b_superseded_plan_remains_retrievable_by_id(client, real_network, fixture_hospitals):
    """Recomputing versions the plan but never destroys the history."""
    mission_id, vehicle_id = _mission_with_vehicle(client)
    _generate_routes(
        client, mission_id, vehicle_id, real_network["origin"], real_network["destination"]
    )
    first = _optimize(client, mission_id)
    second = _optimize(client, mission_id)
    assert (first["version"], second["version"]) == (1, 2)
    assert second["status"] == "DRAFT"
    historical = client.get(f"/api/v1/missions/{mission_id}/plans/{first['plan_id']}").json()
    assert historical["status"] == "SUPERSEDED"
    assert historical["version"] == 1


def test_21_rationale_is_persisted_and_explains_the_plan(
    client, real_network, fixture_hospitals
):
    mission_id, vehicle_id = _mission_with_vehicle(client)
    _generate_routes(
        client, mission_id, vehicle_id, real_network["origin"], real_network["destination"]
    )
    created = _optimize(
        client,
        mission_id,
        required_capabilities=["trauma"],
        required_vehicle_types=["AMBULANCE"],
    )

    stored = client.get(f"/api/v1/missions/{mission_id}/plans/{created['plan_id']}").json()
    assert stored["rationale"]
    assert "Selected hospital" in stored["rationale"]
    assert "capability_match" in stored["rationale"]
    assert "Why this hospital" in stored["rationale"]
    assert "Resources:" in stored["rationale"]

    payload = stored["plan_payload"]
    assert payload["hospital"]["capacity_available_to_mission"] is True
    assert payload["hospital"]["capability_missing"] == []
    assert payload["route"]["eta_seconds"] > 0
    assert payload["resources"]["feasible"] is True
    assert payload["hard_constraints_applied"]
    assert payload["requirements"]["source"] == "caller_supplied"
    # A factor with no data is named and rendered as "unavailable", never as 0.
    for name in payload["score"]["unavailable_factors"]:
        assert payload["score"]["costs"][name] == "unavailable"


def test_22_plan_creation_emits_an_event_with_correlation(
    client, real_network, fixture_hospitals
):
    mission_id, vehicle_id = _mission_with_vehicle(client)
    _generate_routes(
        client, mission_id, vehicle_id, real_network["origin"], real_network["destination"]
    )

    correlation_id = str(uuid4())
    created = _optimize(
        client,
        mission_id,
        required_capabilities=["trauma"],
        correlation_id=correlation_id,
    )
    assert created["correlation_id"] == correlation_id

    events = client.get(
        f"/api/v1/missions/{mission_id}/events", params={"event_type": "PLAN_CREATED"}
    )
    assert events.status_code == 200, events.text
    items = events.json()["items"]
    assert items, "PLAN_CREATED must be persisted to the mission event history"
    record = items[0]
    assert record["payload"]["plan_id"] == created["plan_id"]
    assert record["payload"]["mission_id"] == mission_id
    assert record["payload"]["feasible"] is True
    assert record["correlation_id"] == correlation_id
    assert record["source"] == "mission_optimizer"


def test_22b_infeasible_mission_still_produces_an_explicit_result(client):
    """No feasible combination is a recorded outcome, not a silent absence."""
    mission_id, _ = _mission_with_vehicle(client)
    body = _optimize(client, mission_id, required_vehicle_types=["AMBULANCE"])
    assert body["feasible"] is False
    assert body["selected_hospital_id"] is None
    assert body["infeasible_reasons"]
    assert "INFEASIBLE" in body["rationale"]
    stored = client.get(f"/api/v1/missions/{mission_id}/plans/{body['plan_id']}")
    assert stored.status_code == 200
    assert stored.json()["feasible"] is False


def test_22c_what_if_replans_when_the_selected_hospital_is_removed(
    client, real_network, fixture_hospitals
):
    mission_id, vehicle_id = _mission_with_vehicle(client)
    _generate_routes(
        client, mission_id, vehicle_id, real_network["origin"], real_network["destination"]
    )
    baseline = _optimize(client, mission_id, required_capabilities=["trauma"])
    assert baseline["feasible"] is True

    response = client.post(
        f"/api/v1/missions/{mission_id}/plans/whatif",
        json={
            "component": "HOSPITAL",
            "component_id": baseline["selected_hospital_id"],
            "baseline_plan_id": baseline["plan_id"],
            "required_capabilities": ["trauma"],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["plan_changed"] is True
    assert body["hospital_changed"] is True
    assert body["recomputed_hospital_id"] != baseline["selected_hospital_id"]
    assert body["explanation"]

    # A hypothetical is not a plan version: it must not become the mission's
    # current plan or create a new row.
    latest = client.get(f"/api/v1/missions/{mission_id}/plans/latest").json()
    assert latest["id"] == baseline["plan_id"]
    assert latest["version"] == baseline["version"]


def test_22d_what_if_is_deterministic(client, real_network, fixture_hospitals):
    mission_id, vehicle_id = _mission_with_vehicle(client)
    _generate_routes(
        client, mission_id, vehicle_id, real_network["origin"], real_network["destination"]
    )
    baseline = _optimize(client, mission_id, required_capabilities=["trauma"])
    payload = {
        "component": "HOSPITAL",
        "component_id": baseline["selected_hospital_id"],
        "baseline_plan_id": baseline["plan_id"],
        "required_capabilities": ["trauma"],
    }
    first = client.post(f"/api/v1/missions/{mission_id}/plans/whatif", json=payload).json()
    second = client.post(f"/api/v1/missions/{mission_id}/plans/whatif", json=payload).json()
    assert first == second


# ======================================================================
# REAL NETWORK 23-25
# ======================================================================


def test_23_optimizer_consumes_actual_phase5_candidates(
    client, real_network, fixture_hospitals
):
    """The plan's route is a stored Phase 5 candidate, not a new computation."""
    mission_id, vehicle_id = _mission_with_vehicle(client)
    generated = _generate_routes(
        client, mission_id, vehicle_id, real_network["origin"], real_network["destination"]
    )
    created = _optimize(client, mission_id, required_capabilities=["trauma"])
    assert created["feasible"] is True

    stored = {item["route_id"]: item for item in generated["candidates"]}
    assert created["selected_route_id"] in stored
    chosen = stored[created["selected_route_id"]]
    payload = created["plan_payload"]["route"]
    assert payload["route_id"] == chosen["route_id"]
    # The resilience role comes from Phase 5, not from a second role decision.
    assert payload["resilience_role"] == chosen["resilience_role"]
    assert created["plan_payload"]["hospital_candidates"], "ranking must be persisted"
    assert created["plan_payload"]["options_considered"] >= 1


def test_24_real_route_travel_time_is_preserved_verbatim(
    client, real_network, fixture_hospitals
):
    """Phase 5's measured ETA and distance reach the plan unchanged."""
    mission_id, vehicle_id = _mission_with_vehicle(client)
    generated = _generate_routes(
        client, mission_id, vehicle_id, real_network["origin"], real_network["destination"]
    )
    created = _optimize(client, mission_id, required_capabilities=["trauma"])
    chosen = next(
        item for item in generated["candidates"] if item["route_id"] == created["selected_route_id"]
    )

    route = created["plan_payload"]["route"]
    assert route["eta_seconds"] == chosen["estimated_duration_seconds"]
    assert route["distance_meters"] == pytest.approx(chosen["distance_meters"], abs=0.01)
    # Derived from the real imported network, not a placeholder.
    assert chosen["estimated_duration_seconds"] > 0
    assert chosen["distance_meters"] > 100
    assert len(chosen["geometry"]) >= 2
    assert chosen["provider"] == "road_graph_routing_provider"


def test_25_canonical_road_edge_ids_are_preserved(client, real_network, fixture_hospitals):
    """``RoadEdge.id`` survives the whole chain: network -> candidate -> plan."""
    mission_id, vehicle_id = _mission_with_vehicle(client)
    generated = _generate_routes(
        client, mission_id, vehicle_id, real_network["origin"], real_network["destination"]
    )
    chosen = next(
        item for item in generated["candidates"] if item["resilience_role"] == "PRIMARY"
    )
    segment_ids = chosen["road_segment_ids"]
    assert segment_ids, "real graph routes must carry canonical edge ids"
    known = real_network["edge_ids"]
    # Every id Phase 5 recorded is a canonical RoadEdge.id -- not a SUMO
    # external_id and not a synthetic node-pair key.
    assert set(segment_ids) <= known


def test_25b_plan_payload_replays_the_canonical_edge_ids(
    client, real_network, fixture_hospitals
):
    """The persisted plan still carries the real edge identity end to end."""
    mission_id, vehicle_id = _mission_with_vehicle(client)
    generated = _generate_routes(
        client, mission_id, vehicle_id, real_network["origin"], real_network["destination"]
    )
    created = _optimize(client, mission_id, required_capabilities=["trauma"])
    chosen = next(
        item for item in generated["candidates"] if item["route_id"] == created["selected_route_id"]
    )
    route = created["plan_payload"]["route"]
    assert route["canonical_road_edge_ids"] == chosen["road_segment_ids"]
    assert route["canonical_road_edge_count"] == len(chosen["road_segment_ids"])
    assert set(route["canonical_road_edge_ids"]) <= real_network["edge_ids"]
