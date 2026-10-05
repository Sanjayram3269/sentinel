"""Phase 7 mission-plan CLEARPATH validation over PostGIS, using the fake adapter.

Covers brief items A-S that need a database. Real SUMO is exercised separately
in ``test_plan_clearpath_sumo.py``.

Network note: these tests build a dedicated ``phase7_dev_v1`` road network whose
canonical ``RoadEdge.id`` values map onto the development fixture's SUMO edge
ids. That keeps the imported ``osm_urban_v1`` reference data untouched and lets
the canonical -> SUMO translation be exercised for real without requiring SUMO.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from geoalchemy2 import WKTElement
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings, get_settings
from app.main import app
from app.models import (
    Mission,
    MissionPlan,
    RoadEdge,
    RoadNetwork,
    Route,
    RouteCandidate,
    TrafficSignal,
    Vehicle,
)
from app.models.enums import PlanStatus, RouteStatus
from app.models.road import RoadEdge as RoadEdgeModel

pytestmark = pytest.mark.integration
BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
DEV_NETWORK_KEY = "phase7_dev_v1"
PHASE7_SIMULATION_NETWORK_ID = "phase7-development-test-network"
PHASE7_FIXTURE_PATH = BACKEND_DIR / "tests" / "fixtures" / "phase7_development_fixture.json"
# SUMO edge ids of the bundled development fixture.
DEV_EDGE_A = "p7-edge-0"
DEV_EDGE_B = "p7-edge-1"
DEV_SIGNAL_METADATA = {
    "sumo_signal_id": "p7-tls-1",
    "edge_id": DEV_EDGE_B,
    "valid_phases": ["R", "G", "Y"],
    "initial_phase": "R",
    "preemption_phase": "G",
    "release_phase": "R",
    "safe_transitions": {"R": ["G"], "G": ["R"], "Y": ["R"]},
    "maximum_duration_seconds": 2,
}


def _engine():
    return create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)


def run_async(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module", autouse=True)
def prepared_database() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip("set TEST_DATABASE_URL to run Phase 7 integration tests")
    command.upgrade(Config(str(BACKEND_DIR / "alembic.ini")), "head")


@pytest.fixture(scope="module")
def dev_network():
    """Create ``phase7_dev_v1`` with two canonical edges mapped to fixture edges."""
    engine = _engine()

    async def _build():
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                async with session.begin():
                    network = await session.scalar(
                        select(RoadNetwork).where(
                            RoadNetwork.network_key == DEV_NETWORK_KEY
                        )
                    )
                    if network is None:
                        network = RoadNetwork(
                            network_key=DEV_NETWORK_KEY,
                            proj_parameter="+proj=longlat +datum=WGS84 +no_defs",
                            orig_boundary="phase7-development-fixture",
                            source_checksum="phase7-dev-fixture-checksum",
                        )
                        session.add(network)
                        await session.flush()
                    edge_a = await session.scalar(
                        select(RoadEdge).where(
                            RoadEdge.network_id == network.id,
                            RoadEdge.external_id == DEV_EDGE_A,
                        )
                    )
                    if edge_a is None:
                        edge_a = RoadEdge(
                            network_id=network.id,
                            source="development_fixture",
                            external_id=DEV_EDGE_A,
                            from_node="dev-n0",
                            to_node="dev-n1",
                            length_m=500.0,
                            speed_limit_kmh=50.0,
                            road_class="primary",
                            lanes=1,
                            has_signal=False,
                            geometry=WKTElement("LINESTRING(77.59 12.97, 77.591 12.971)", srid=4326),
                        )
                        session.add(edge_a)
                        await session.flush()
                    edge_b = await session.scalar(
                        select(RoadEdge).where(
                            RoadEdge.network_id == network.id,
                            RoadEdge.external_id == DEV_EDGE_B,
                        )
                    )
                    if edge_b is None:
                        edge_b = RoadEdge(
                            network_id=network.id,
                            source="development_fixture",
                            external_id=DEV_EDGE_B,
                            from_node="dev-n1",
                            to_node="dev-n2",
                            length_m=500.0,
                            speed_limit_kmh=50.0,
                            road_class="primary",
                            lanes=1,
                            has_signal=True,
                            geometry=WKTElement("LINESTRING(77.591 12.971, 77.592 12.972)", srid=4326),
                        )
                        session.add(edge_b)
                        await session.flush()
                    return str(edge_a.id), str(edge_b.id)
        finally:
            await engine.dispose()

    return run_async(_build())


@pytest.fixture(scope="module")
def corridor_signal():
    """A CLEARPATH-capable signal on the second route edge."""
    engine = _engine()

    async def _build():
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                async with session.begin():
                    existing = await session.scalar(
                        select(TrafficSignal).where(
                            TrafficSignal.external_id == "phase7-dev-corridor-signal"
                        )
                    )
                    if existing is not None:
                        # Reconcile metadata: an earlier run of this suite may
                        # have created the row with the previous signal id, and
                        # a stale row would silently produce an empty corridor.
                        existing.signal_metadata = DEV_SIGNAL_METADATA
                        existing.enabled = True
                        return str(existing.id)
                    signal = TrafficSignal(
                        external_id="phase7-dev-corridor-signal",
                        location=WKTElement("POINT(77.591 12.971)", srid=4326),
                        current_phase="R",
                        signal_metadata=DEV_SIGNAL_METADATA,
                        enabled=True,
                    )
                    session.add(signal)
                    await session.flush()
                    return str(signal.id)
        finally:
            await engine.dispose()

    return run_async(_build())


@pytest.fixture(autouse=True)
def dev_settings(monkeypatch):
    """Point Phase 7 at its own network, fixture and fake adapter.

    Isolation matters here: the shared test database also holds traffic signals
    from the earlier simulation suites, and two signals may not claim the same
    SUMO signal id. Phase 7 therefore uses a dedicated fixture rather than
    fighting over the other suites' reference data.
    """
    monkeypatch.setenv("ROAD_NETWORK_KEY", DEV_NETWORK_KEY)
    monkeypatch.delenv("SUMO_NETWORK_ID", raising=False)
    monkeypatch.setenv(
        "SIMULATION_DEVELOPMENT_NETWORK_ID", PHASE7_SIMULATION_NETWORK_ID
    )
    monkeypatch.setenv("SIMULATION_FIXTURE_PATH", str(PHASE7_FIXTURE_PATH))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _make_plan(
    client: TestClient,
    canonical_ids: list[str],
    *,
    route_status: RouteStatus = RouteStatus.ACTIVE,
    feasible: bool = True,
    with_candidate: bool = True,
    plan_ids: list[str] | None = None,
) -> dict:
    """Create a mission whose plan selects a route over ``canonical_ids``."""
    mission_id = client.post(
        "/api/v1/missions", json={"objective": "phase 7 validation"}
    ).json()["id"]
    vehicle_id = client.post(
        "/api/v1/vehicles",
        json={
            "mission_id": mission_id,
            "vehicle_type": "AMBULANCE",
            "call_sign": f"P7-{uuid4()}",
        },
    ).json()["id"]
    route_id = uuid4()
    candidate_id = uuid4()
    cycle_id = uuid4()

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
                            status=route_status,
                            name="phase 7 route",
                            geometry=WKTElement(
                                "LINESTRING(77.59 12.97, 77.592 12.972)", srid=4326
                            ),
                            distance_meters=1000.0,
                            estimated_duration_seconds=100,
                            risk_score=0.1,
                        )
                    )
                    if with_candidate:
                        session.add(
                            RouteCandidate(
                                id=candidate_id,
                                mission_id=UUID(mission_id),
                                vehicle_id=UUID(vehicle_id),
                                route_id=route_id,
                                planning_cycle_id=cycle_id,
                                origin=WKTElement("POINT(77.59 12.97)", srid=4326),
                                destination=WKTElement("POINT(77.592 12.972)", srid=4326),
                                status=RouteStatus.CANDIDATE,
                                route_rank=1,
                                estimated_duration_seconds=100,
                                distance_meters=1000.0,
                                risk_score=0.1,
                                score=0.2,
                                confidence=0.6,
                                backup_viable=True,
                                road_segment_ids=list(canonical_ids),
                                provider="road_graph_routing_provider",
                            )
                        )
                    payload_ids = plan_ids if plan_ids is not None else list(canonical_ids)
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
                                    "canonical_road_edge_count": len(payload_ids),
                                    "canonical_road_edge_ids": payload_ids,
                                },
                            },
                            score=0.5,
                            feasible=feasible,
                            rationale="phase 7 fixture plan",
                        )
                    )
        finally:
            await engine.dispose()

    run_async(_persist())
    return {
        "mission_id": mission_id,
        "vehicle_id": vehicle_id,
        "route_id": str(route_id),
        "plan_id": None,
    }


def _plan_id(client: TestClient, mission_id: str) -> str:
    return client.get(f"/api/v1/missions/{mission_id}/plans/latest").json()["id"]


def _simulate(client: TestClient, mission_id: str, plan_id: str, **body):
    return client.post(
        f"/api/v1/missions/{mission_id}/plans/{plan_id}/simulate", json=body
    )


# ======================================================================
# A/B/C/D/E/J/K/L/P/T
# ======================================================================


def test_A_plan_maps_to_simulation_and_uses_selected_route(
    client, dev_network, corridor_signal
):
    world = _make_plan(client, list(dev_network))
    world["plan_id"] = _plan_id(client, world["mission_id"])
    response = _simulate(client, world["mission_id"], world["plan_id"], seed=7)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["route_id"] == world["route_id"]
    assert body["vehicle_id"] == world["vehicle_id"]
    assert body["corridor"]["canonical_road_edge_count"] == 2
    assert body["corridor"]["sumo_edge_count"] == 2
    assert body["network"]["road_network_key"] == DEV_NETWORK_KEY
    assert body["seed"] == 7


def test_B_and_C_sumo_edges_are_derived_in_canonical_order(
    client, dev_network, corridor_signal
):
    world = _make_plan(client, list(dev_network))
    world["plan_id"] = _plan_id(client, world["mission_id"])
    body = _simulate(client, world["mission_id"], world["plan_id"]).json()
    runs = _runs(client, world["mission_id"])
    scenario = runs["BASELINE"]["configuration"]["scenario"]
    assert scenario["route_edge_ids"] == [DEV_EDGE_A, DEV_EDGE_B]


def test_D_ownership_is_validated(client, dev_network, corridor_signal):
    world = _make_plan(client, list(dev_network))
    world["plan_id"] = _plan_id(client, world["mission_id"])
    other = client.post("/api/v1/missions", json={"objective": "other"}).json()["id"]
    # A plan from another mission must not be reachable through this mission.
    response = _simulate(client, other, world["plan_id"])
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "PLAN_MISSION_MISMATCH"

    missing = _simulate(client, world["mission_id"], str(uuid4()))
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "PLAN_NOT_FOUND"


def test_E_pair_is_comparable(client, dev_network, corridor_signal):
    world = _make_plan(client, list(dev_network))
    world["plan_id"] = _plan_id(client, world["mission_id"])
    body = _simulate(client, world["mission_id"], world["plan_id"], seed=11).json()
    assert body["baseline"]["mode"] == "BASELINE"
    assert body["clearpath"]["mode"] == "CLEARPATH"
    assert body["seed"] == 11
    # Same shared identity, two distinct runs.
    assert body["baseline"]["simulation_id"] != body["clearpath"]["simulation_id"]
    assert body["baseline"]["metrics"] is not None
    assert body["evidence_status"] == "COMPARABLE"


def test_F_corridor_is_derived_from_the_selected_route(
    client, dev_network, corridor_signal
):
    world = _make_plan(client, list(dev_network))
    world["plan_id"] = _plan_id(client, world["mission_id"])
    body = _simulate(client, world["mission_id"], world["plan_id"]).json()
    assert body["corridor"]["status"] == "ELIGIBLE_CORRIDOR"
    assert [item["signal_id"] for item in body["corridor"]["signals"]] == ["p7-tls-1"]
    assert body["corridor"]["signals"][0]["sumo_edge_id"] == DEV_EDGE_B


def test_G_off_corridor_signal_is_excluded(client, dev_network, corridor_signal):
    """A signal on an edge the route never uses cannot join the corridor."""
    world = _make_plan(client, [dev_network[0]])  # only dev-edge-0
    world["plan_id"] = _plan_id(client, world["mission_id"])
    response = _simulate(client, world["mission_id"], world["plan_id"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["corridor"]["status"] == "NO_ELIGIBLE_CORRIDOR"
    assert body["corridor"]["signals"] == []
    assert body["safety_status"] == "NO_ACTION_PROPOSED"


def test_H_and_R_unsafe_or_malformed_configuration_fails_closed(
    client, dev_network, corridor_signal
):
    world = _make_plan(client, list(dev_network))
    world["plan_id"] = _plan_id(client, world["mission_id"])
    # Negative activation cannot be requested through this endpoint at all,
    # because the caller does not supply signal actions.
    response = client.post(
        f"/api/v1/missions/{world['mission_id']}/plans/{world['plan_id']}/simulate",
        json={"seed": -1},
    )
    assert response.status_code == 422


def test_I_clearpath_stays_simulation_only(client, dev_network, corridor_signal):
    world = _make_plan(client, list(dev_network))
    world["plan_id"] = _plan_id(client, world["mission_id"])
    body = _simulate(client, world["mission_id"], world["plan_id"]).json()
    assert body["simulation_only"] is True
    assert "No real-world traffic signal is controlled" in body["scope_notice"]
    assert body["safety_status"] in {
        "APPROVED_SIMULATION_ONLY",
        "NO_ACTION_PROPOSED",
        "REJECTED_SIMULATION_ONLY",
    }


def test_J_and_K_both_runs_report_measured_metrics(
    client, dev_network, corridor_signal
):
    world = _make_plan(client, list(dev_network))
    world["plan_id"] = _plan_id(client, world["mission_id"])
    body = _simulate(client, world["mission_id"], world["plan_id"]).json()
    for side in ("baseline", "clearpath"):
        metrics = body[side]["metrics"]
        assert metrics is not None, side
        # Measured by the adapter, not asserted against a magic number here.
        assert metrics["route_completed"] is True
        assert metrics["emergency_vehicle_travel_time_seconds"] is not None


def test_L_and_M_comparison_is_measured_and_guards_unavailable(
    client, dev_network, corridor_signal
):
    world = _make_plan(client, list(dev_network))
    world["plan_id"] = _plan_id(client, world["mission_id"])
    body = _simulate(client, world["mission_id"], world["plan_id"]).json()
    comparison = body["comparison"]
    base = body["baseline"]["metrics"]["emergency_vehicle_travel_time_seconds"]
    clear = body["clearpath"]["metrics"]["emergency_vehicle_travel_time_seconds"]
    assert comparison["travel_time_delta_seconds"] == pytest.approx(clear - base)
    if base and base > 0:
        assert comparison["travel_time_improvement_percent"] == pytest.approx(
            (base - clear) / base * 100
        )
    else:
        assert comparison["travel_time_improvement_percent"] is None


def test_N_simulation_does_not_mutate_the_plan(client, dev_network, corridor_signal):
    world = _make_plan(client, list(dev_network))
    world["plan_id"] = _plan_id(client, world["mission_id"])
    before = client.get(f"/api/v1/missions/{world['mission_id']}/plans/{world['plan_id']}").json()
    _simulate(client, world["mission_id"], world["plan_id"])
    after = client.get(f"/api/v1/missions/{world['mission_id']}/plans/{world['plan_id']}").json()
    assert before == after
    # Still version 1 and still the latest plan: no new version was created.
    latest = client.get(f"/api/v1/missions/{world['mission_id']}/plans/latest").json()
    assert latest["id"] == world["plan_id"]
    assert latest["version"] == before["version"]


def test_P_determinism_under_the_same_seed(client, dev_network, corridor_signal):
    world = _make_plan(client, list(dev_network))
    world["plan_id"] = _plan_id(client, world["mission_id"])
    first = _simulate(client, world["mission_id"], world["plan_id"], seed=99).json()
    second = _simulate(client, world["mission_id"], world["plan_id"], seed=99).json()
    assert first["comparison"] == second["comparison"]
    assert first["baseline"]["metrics"] == second["baseline"]["metrics"]
    assert first["clearpath"]["metrics"] == second["clearpath"]["metrics"]


def test_S_no_eligible_corridor_still_runs_both_modes(
    client, dev_network, corridor_signal
):
    world = _make_plan(client, [dev_network[0]])
    world["plan_id"] = _plan_id(client, world["mission_id"])
    body = _simulate(client, world["mission_id"], world["plan_id"]).json()
    assert body["corridor"]["status"] == "NO_ELIGIBLE_CORRIDOR"
    assert "no intervention is fabricated" in body["corridor"]["note"]
    assert body["baseline"]["status"] == "COMPLETED"
    assert body["clearpath"]["status"] == "COMPLETED"
    assert body["actions_requested"] == 0


def test_route_not_active_fails_closed(client, dev_network, corridor_signal):
    world = _make_plan(client, list(dev_network), route_status=RouteStatus.CANDIDATE)
    world["plan_id"] = _plan_id(client, world["mission_id"])
    response = _simulate(client, world["mission_id"], world["plan_id"])
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ROUTE_NOT_ACTIVE"


def test_unmapped_edge_fails_closed(client, dev_network, corridor_signal):
    world = _make_plan(client, [str(uuid4()), dev_network[1]])
    world["plan_id"] = _plan_id(client, world["mission_id"])
    response = _simulate(client, world["mission_id"], world["plan_id"])
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "MISSING_SUMO_EDGE_MAPPING"


def test_plan_candidate_divergence_fails_closed(client, dev_network, corridor_signal):
    """A plan that disagrees with its route candidate is not simulable."""
    world = _make_plan(
        client,
        list(dev_network),
        plan_ids=[dev_network[1], dev_network[0]],
    )
    world["plan_id"] = _plan_id(client, world["mission_id"])
    response = _simulate(client, world["mission_id"], world["plan_id"])
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_ROUTE"


def test_infeasible_plan_cannot_be_simulated(client, dev_network, corridor_signal):
    world = _make_plan(client, list(dev_network), feasible=False)
    world["plan_id"] = _plan_id(client, world["mission_id"])
    response = _simulate(client, world["mission_id"], world["plan_id"])
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PLAN_HAS_NO_SELECTED_ROUTE"


def test_missing_route_candidate_fails_closed(client, dev_network, corridor_signal):
    world = _make_plan(client, list(dev_network), with_candidate=False)
    world["plan_id"] = _plan_id(client, world["mission_id"])
    response = _simulate(client, world["mission_id"], world["plan_id"])
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_ROUTE"


def _runs(client: TestClient, mission_id: str) -> dict:
    """Simulation rows keyed by mode, for asserting the persisted scenario."""
    engine = _engine()

    async def _load():
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                from app.models import SimulationRun

                rows = (
                    await session.scalars(
                        select(SimulationRun)
                        .where(SimulationRun.mission_id == UUID(mission_id))
                        .order_by(SimulationRun.created_at, SimulationRun.id)
                    )
                ).all()
                return {
                    row.configuration["mode"]: {
                        "id": str(row.id),
                        "configuration": row.configuration,
                    }
                    for row in rows
                }
        finally:
            await engine.dispose()

    return run_async(_load())


# ======================================================================
# O / Q
# ======================================================================


def _plan_count(mission_id: str) -> int:
    """How many MissionPlan rows exist for a mission, counted directly.

    There is no plan-collection endpoint, and asserting on a single retrieved
    plan would miss a hypothetical that was persisted as a *new* version.
    """
    engine = _engine()

    async def _count():
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                from sqlalchemy import func

                from app.models import MissionPlan

                return await session.scalar(
                    select(func.count())
                    .select_from(MissionPlan)
                    .where(MissionPlan.mission_id == UUID(mission_id))
                )
        finally:
            await engine.dispose()

    return run_async(_count())


def test_O_what_if_remains_non_persistent_through_simulation(
    client, dev_network, corridor_signal
):
    """A counterfactual plan is never stored, so simulation cannot persist one either.

    The Phase 6 what-if endpoint computes a hypothetical in memory. This proves
    that calling it -- and then simulating the baseline -- leaves the stored plan
    set untouched: simulation is evidence, not a new plan version.
    """
    world = _make_plan(client, list(dev_network))
    world["plan_id"] = _plan_id(client, world["mission_id"])
    before = client.get(f"/api/v1/missions/{world['mission_id']}/plans/{world['plan_id']}").json()

    what_if = client.post(
        f"/api/v1/missions/{world['mission_id']}/plans/whatif",
        json={
            "component": "ROUTE",
            "component_id": world["route_id"],
            "baseline_plan_id": world["plan_id"],
        },
    )
    # Either the counterfactual is infeasible (only one route exists) or it
    # succeeds; both are legitimate. What must never happen is persistence.
    assert what_if.status_code in (200, 422), what_if.text

    _simulate(client, world["mission_id"], world["plan_id"])

    after = client.get(f"/api/v1/missions/{world['mission_id']}/plans/{world['plan_id']}").json()
    assert after == before
    assert after["version"] == 1

    assert _plan_count(world["mission_id"]) == 1


def test_Q_sumo_unavailable_is_an_explicit_result_not_a_crash(
    client, dev_network, corridor_signal, monkeypatch
):
    """An unconfigured SUMO installation yields an explicit status, not a 500.

    The measurement is authoritative: the pair still returns evidence with the
    failure recorded, and the notification path never rewrites it as a success.
    """
    # Unbind the development network so the real SUMO adapter is selected:
    # with the fixture network still bound, the fake adapter would answer and
    # the test would prove nothing about SUMO availability.
    monkeypatch.setenv("SIMULATION_FIXTURE_PATH", str(PHASE7_FIXTURE_PATH))
    monkeypatch.setenv("ROAD_NETWORK_KEY", DEV_NETWORK_KEY)
    monkeypatch.delenv("SIMULATION_DEVELOPMENT_NETWORK_ID", raising=False)
    monkeypatch.setenv("SUMO_NETWORK_ID", PHASE7_SIMULATION_NETWORK_ID)
    monkeypatch.setenv("SUMO_CONFIG_PATH", str(BACKEND_DIR / "no-such-network.sumocfg"))
    monkeypatch.setenv("SUMO_BINARY", "sumo-does-not-exist")
    get_settings.cache_clear()
    try:
        world = _make_plan(client, list(dev_network))
        world["plan_id"] = _plan_id(client, world["mission_id"])
        response = _simulate(client, world["mission_id"], world["plan_id"])
    finally:
        get_settings.cache_clear()

    # Whatever the outcome, it must be a structured response with a status --
    # never an unhandled 500 and never a fabricated improvement.
    assert response.status_code in (201, 409, 422), response.status_code
    if response.status_code == 201:
        body = response.json()
        assert body["evidence_status"] in {
            "BASELINE_FAILED",
            "CLEARPATH_FAILED",
            "COMPARISON_UNAVAILABLE",
        }, body["evidence_status"]
        assert body["comparison"]["travel_time_improvement_percent"] is None
    else:
        assert response.json()["detail"]["code"] in {
            "SUMO_UNAVAILABLE",
            "SIMULATION_CONFIGURATION_ERROR",
        }
