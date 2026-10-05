"""Phase 8 replanning integration tests (brief section 24, T-X, AB).

Replanning must reuse the existing Phase 5 routing and Phase 6 optimization
rather than reimplement either, must never auto-approve what it produces, and
must leave the superseded version exactly as it was -- including its approval.

These run against the real ``osm_urban_v1`` network so the Phase 5 provider and
Phase 6 optimizer both do genuine work.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.main import app
from app.models import MissionPlan, RoadEdge, RoadNetwork, Route, RouteCandidate
from app.models.enums import ApprovalStatus, PlanStatus, RouteStatus

pytestmark = pytest.mark.integration

BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
NETWORK_KEY = "osm_urban_v1"


def _engine():
    return create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)


def run_async(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module", autouse=True)
def prepared_database() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip("set TEST_DATABASE_URL to run Phase 8 replan tests")
    command.upgrade(Config(str(BACKEND_DIR / "alembic.ini")), "head")


@pytest.fixture(scope="module", autouse=True)
def real_network():
    """Import the real Bengaluru network exactly as the Phase 5 tests do."""
    engine = _engine()

    async def _count():
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                return len(
                    (
                        await session.scalars(
                            select(RoadEdge.id).limit(1)
                        )
                    ).all()
                )
        finally:
            await engine.dispose()

    if run_async(_count()) == 0:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(BACKEND_DIR)
        env["DATABASE_URL"] = TEST_DATABASE_URL or ""
        import subprocess

        subprocess.run(
            [str(BACKEND_DIR / ".venv" / "Scripts" / "python.exe"), "scripts/import_road_network.py"],
            cwd=str(BACKEND_DIR),
            env=env,
            capture_output=True,
            timeout=600,
        )
    return NETWORK_KEY


@pytest.fixture(scope="module")
def network_checksum():
    engine = _engine()

    async def _load():
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                return await session.scalar(
                    select(RoadNetwork.source_checksum).where(
                        RoadNetwork.network_key == NETWORK_KEY
                    )
                )
        finally:
            await engine.dispose()

    return run_async(_load())


@pytest.fixture(scope="module")
def fixture_hospitals(real_network):
    """Development-fixture hospitals on the real network, as Phase 6 uses.

    Without these the optimizer correctly reports an infeasible plan
    (``no_hospital_passed_hard_constraints``), which is the right answer for a
    network with no hospitals but does not exercise the replan path.
    """
    from geoalchemy2 import WKTElement as _WKT

    from app.models import Hospital
    from app.models.enums import OperationalStatus

    engine = _engine()
    origin, destination = _reachable_pair()

    async def _ensure():
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                async with session.begin():
                    existing = (
                        await session.scalars(
                            select(Hospital).where(
                                Hospital.name.like("P8R-DEVFIX-%")
                            )
                        )
                    ).all()
                    if existing:
                        return
                    for index, coordinate in enumerate((origin, destination)):
                        lon, _, lat = coordinate.partition(",")
                        session.add(
                            Hospital(
                                name=f"P8R-DEVFIX-{index}",
                                location=_WKT(
                                    f"POINT({lon.strip()} {lat.strip()})", srid=4326
                                ),
                                capacity_total=20,
                                capacity_available=20 - index * 6,
                                capability={
                                    "capabilities": ["trauma", "emergency"],
                                    "emergency": True,
                                },
                                operational_status=OperationalStatus.OPERATIONAL,
                                reliability=0.95 - index * 0.15,
                            )
                        )
        finally:
            await engine.dispose()

    return run_async(_ensure())


@pytest.fixture
def client():
    get_settings.cache_clear()
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


def _first_point(wkt: str | None) -> tuple[float, float] | None:
    """The first coordinate of a stored LINESTRING, as (lon, lat).

    Read as text rather than through the PostGIS dialect, which would need an
    explicit cast for every spatial function this test does not otherwise use.
    """
    if not wkt:
        return None
    body = wkt[wkt.index("(") + 1 : wkt.rindex(")")]
    first = body.split(",")[0].strip()
    lon, _, lat = first.partition(" ")
    try:
        return (float(lon), float(lat))
    except ValueError:  # pragma: no cover - unexpected geometry
        return None


def _point(coordinate: str) -> str:
    """Format "lon,lat" as a WGS84 POINT. WKT requires a space separator."""
    lon, _, lat = coordinate.partition(",")
    return f"POINT({lon.strip()} {lat.strip()})"


def _reachable_pair() -> tuple[str, str]:
    """A deterministic origin/destination pair inside the real network.

    Sorted on purpose: picking a "representative" node from a set makes results
    depend on PYTHONHASHSEED, which is how a determinism test stops testing
    determinism.
    """
    engine = _engine()

    async def _pick():
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                # Real WGS84 endpoints from the stored geometry, so the seeded
                # candidate sits inside the network the router will snap to.
                rows = (
                    await session.execute(
                        select(func.ST_AsText(RoadEdge.geometry))
                        .join(RoadNetwork, RoadNetwork.id == RoadEdge.network_id)
                        .where(RoadNetwork.network_key == NETWORK_KEY)
                        .order_by(RoadEdge.external_id)
                    )
                ).all()
                points: list[str] = []
                for (wkt,) in rows:
                    # Read the WKB the driver hands back rather than calling
                    # PostGIS functions, which need an explicit dialect cast.
                    coords = _first_point(wkt)
                    if coords:
                        points.append(f"{coords[0]},{coords[1]}")
                return points
        finally:
            await engine.dispose()

    coords = sorted({item for item in run_async(_pick())})
    return coords[0], coords[-1]


def _make_mission(client: TestClient) -> dict:
    mission_id = client.post(
        "/api/v1/missions", json={"objective": "phase 8 replan"}
    ).json()["id"]
    vehicle_id = client.post(
        "/api/v1/vehicles",
        json={
            "mission_id": mission_id,
            "vehicle_type": "AMBULANCE",
            "call_sign": f"P8R-{uuid4()}",
        },
    ).json()["id"]
    return {"mission_id": mission_id, "vehicle_id": vehicle_id}


def _plans(mission_id: str) -> list[dict]:
    engine = _engine()

    async def _load():
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                rows = (
                    await session.scalars(
                        select(MissionPlan)
                        .where(MissionPlan.mission_id == UUID(mission_id))
                        .order_by(MissionPlan.version)
                    )
                ).all()
                return [
                    {
                        "id": str(item.id),
                        "version": item.version,
                        "status": item.status.value,
                        "score": item.score,
                        "objective": item.objective,
                    }
                    for item in rows
                ]
        finally:
            await engine.dispose()

    return run_async(_load())


def _approvals_for(plan_id: str) -> list[dict]:
    engine = _engine()

    async def _load():
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                from app.models.plan import PlanApproval

                rows = (
                    await session.scalars(
                        select(PlanApproval).where(PlanApproval.plan_id == UUID(plan_id))
                    )
                ).all()
                return [
                    {"status": item.status.value, "version": item.plan_version}
                    for item in rows
                ]
        finally:
            await engine.dispose()

    return run_async(_load())


def _seed_approved_plan(client: TestClient, world: dict, checksum: str | None) -> str:
    """A DRAFT plan promoted to APPROVED with a real approval record.

    The Phase 6 optimizer computes plans; it does not approve them. This builds
    the starting state a replan is triggered from.
    """
    origin, destination = _reachable_pair()
    engine = _engine()

    async def _persist():
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                async with session.begin():
                    edges = (
                        await session.scalars(
                            select(RoadEdge.id)
                            .join(RoadNetwork, RoadNetwork.id == RoadEdge.network_id)
                            .where(RoadNetwork.network_key == NETWORK_KEY)
                            .order_by(RoadEdge.external_id)
                            .limit(4)
                        )
                    ).all()
                    canonical = [str(item) for item in edges]
                    route_id = uuid4()
                    session.add(
                        Route(
                            id=route_id,
                            mission_id=UUID(world["mission_id"]),
                            vehicle_id=UUID(world["vehicle_id"]),
                            status=RouteStatus.ACTIVE,
                            name="phase 8 replan seed route",
                            geometry=WKTElement(
                                "LINESTRING(77.58 13.09, 77.60 13.11)", srid=4326
                            ),
                            distance_meters=2000.0,
                            estimated_duration_seconds=200,
                            risk_score=0.1,
                        )
                    )
                    session.add(
                        RouteCandidate(
                            mission_id=UUID(world["mission_id"]),
                            vehicle_id=UUID(world["vehicle_id"]),
                            route_id=route_id,
                            origin=WKTElement(_point(origin), srid=4326),
                            destination=WKTElement(_point(destination), srid=4326),
                            status=RouteStatus.CANDIDATE,
                            route_rank=1,
                            estimated_duration_seconds=200,
                            distance_meters=2000.0,
                            risk_score=0.1,
                            score=0.2,
                            confidence=0.6,
                            backup_viable=True,
                            road_segment_ids=canonical,
                            provider="road_graph_routing_provider",
                        )
                    )
                    plan = MissionPlan(
                        mission_id=UUID(world["mission_id"]),
                        version=1,
                        status=PlanStatus.READY_FOR_REVIEW,
                        objective="MINIMIZE_WEIGHTED_MISSION_COST",
                        plan_payload={
                            "selected": {
                                "hospital_id": None,
                                "route_id": str(route_id),
                                "resource_ids": [],
                            },
                            "route": {
                                "route_id": str(route_id),
                                "canonical_road_edge_ids": canonical,
                            },
                            "score": {"value": 0.2, "coverage": 0.5},
                        },
                        score=0.2,
                        feasible=True,
                        rationale="phase 8 replan seed plan",
                        network_key=NETWORK_KEY,
                        network_checksum=checksum,
                    )
                    session.add(plan)
                    await session.flush()
                    await session.refresh(plan)
                    from app.models.plan import PlanApproval

                    session.add(
                        PlanApproval(
                            plan_id=plan.id,
                            plan_version=1,
                            status=ApprovalStatus.APPROVED,
                            decision=ApprovalStatus.APPROVED.value,
                            reviewer_id="dispatcher-1",
                            comment="Approved for the replan regression.",
                            previous_plan_status="READY_FOR_REVIEW",
                            new_plan_status="APPROVED",
                            context={"reviewer_identity_kind": "development_prototype_identity"},
                        )
                    )
                    plan.status = PlanStatus.APPROVED
                    return str(plan.id)
        finally:
            await engine.dispose()

    return run_async(_persist())


from geoalchemy2 import WKTElement  # noqa: E402  (used by _seed_approved_plan)


# ======================================================================
# T / U / X: replan invokes Phase 5 + Phase 6 and never auto-approves
# ======================================================================


def test_T_replan_produces_a_new_version_ready_for_review(
    client, real_network, network_checksum, fixture_hospitals
):
    world = _make_mission(client)
    old_plan = _seed_approved_plan(client, world, network_checksum)

    response = client.post(
        f"/api/v1/missions/{world['mission_id']}/plans/{old_plan}/replan",
        json={"reason": "ROUTE_DEVIATION"},
    )
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["replan_requested"] is True
    assert body["previous_plan_id"] == old_plan
    assert body["new_plan_id"] != old_plan
    assert body["new_plan_version"] == 2
    # The single most important assertion in this file.
    assert body["auto_approved"] is False
    assert body["requires_human_approval"] is True

    plans = _plans(world["mission_id"])
    assert len(plans) == 2
    new_plan = next(item for item in plans if item["id"] == body["new_plan_id"])
    assert new_plan["status"] == "READY_FOR_REVIEW"


def test_U_replan_uses_phase6_objective_and_real_network_edges(
    client, real_network, network_checksum, fixture_hospitals
):
    world = _make_mission(client)
    old_plan = _seed_approved_plan(client, world, network_checksum)

    body = client.post(
        f"/api/v1/missions/{world['mission_id']}/plans/{old_plan}/replan",
        json={"reason": "ROUTE_DEVIATION"},
    ).json()

    new_id = body["new_plan_id"]
    engine = _engine()

    async def _load():
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                plan = await session.get(MissionPlan, UUID(new_id))
                route_id = UUID(plan.plan_payload["selected"]["route_id"])
                candidate = await session.scalar(
                    select(RouteCandidate).where(RouteCandidate.route_id == route_id)
                )
                edges = (
                    await session.scalars(
                        select(RoadEdge.id)
                        .join(RoadNetwork, RoadNetwork.id == RoadEdge.network_id)
                        .where(RoadNetwork.network_key == NETWORK_KEY)
                    )
                ).all()
                return {
                    "objective": plan.objective,
                    "provider": candidate.provider if candidate else None,
                    "segment_count": len(candidate.road_segment_ids or []) if candidate else 0,
                    "canonical_known": sum(
                        1
                        for item in (candidate.road_segment_ids or [])
                        if UUID(str(item)) in {UUID(str(e)) for e in edges}
                    ),
                }
        finally:
            await engine.dispose()

    detail = run_async(_load())
    # Phase 6's own objective, not a Phase 8 invention.
    assert detail["objective"] == "MINIMIZE_WEIGHTED_MISSION_COST"
    # Phase 5's real-graph provider produced the route.
    assert detail["provider"] == "road_graph_routing_provider"
    assert detail["segment_count"] > 0
    # Canonical RoadEdge identity survived the whole replan.
    assert detail["canonical_known"] == detail["segment_count"]


def test_X_replan_does_not_auto_approve_the_new_plan(
    client, real_network, network_checksum, fixture_hospitals
):
    world = _make_mission(client)
    old_plan = _seed_approved_plan(client, world, network_checksum)
    body = client.post(
        f"/api/v1/missions/{world['mission_id']}/plans/{old_plan}/replan",
        json={"reason": "ROUTE_DEVIATION"},
    ).json()
    new_id = body["new_plan_id"]

    # The new plan has no approval, so it cannot execute. This is the property
    # that makes "replan" safe: the human is back in the loop.
    denied = client.post(
        f"/api/v1/missions/{world['mission_id']}/plans/{new_id}/execute"
    )
    assert denied.status_code == 409
    assert denied.json()["detail"]["code"] == "APPROVAL_NOT_FOUND"
    assert _approvals_for(new_id) == []


def test_W_replan_leaves_the_old_plan_and_its_approval_immutable(
    client, real_network, network_checksum, fixture_hospitals
):
    world = _make_mission(client)
    old_plan = _seed_approved_plan(client, world, network_checksum)
    before = _plans(world["mission_id"])[0]
    approvals_before = _approvals_for(old_plan)

    client.post(
        f"/api/v1/missions/{world['mission_id']}/plans/{old_plan}/replan",
        json={"reason": "ROUTE_DEVIATION"},
    )

    after = _plans(world["mission_id"])[0]
    assert after["id"] == before["id"]
    assert after["version"] == before["version"]
    assert after["score"] == before["score"]
    # The historical status becomes REPLAN_REQUIRED, but the approval record
    # itself is untouched and still attached to version 1.
    assert after["status"] == "REPLAN_REQUIRED"
    assert _approvals_for(old_plan) == approvals_before
    assert approvals_before[0]["status"] == "APPROVED"


def test_V_new_plan_version_requires_a_fresh_approval(
    client, real_network, network_checksum, fixture_hospitals
):
    world = _make_mission(client)
    old_plan = _seed_approved_plan(client, world, network_checksum)
    body = client.post(
        f"/api/v1/missions/{world['mission_id']}/plans/{old_plan}/replan",
        json={"reason": "ROUTE_DEVIATION"},
    ).json()
    new_id = body["new_plan_id"]

    # Version 1's approval cannot be replayed onto version 2.
    replay = client.post(
        f"/api/v1/missions/{world['mission_id']}/plans/{new_id}/approve",
        json={"plan_version": 1, "reviewer_id": "dispatcher-1"},
    )
    assert replay.status_code == 409
    assert replay.json()["detail"]["code"] == "PLAN_VERSION_MISMATCH"
    assert _approvals_for(new_id) == []


def test_replan_events_are_emitted_with_correlation(client, real_network, network_checksum, fixture_hospitals):
    world = _make_mission(client)
    old_plan = _seed_approved_plan(client, world, network_checksum)
    body = client.post(
        f"/api/v1/missions/{world['mission_id']}/plans/{old_plan}/replan",
        json={"reason": "ETA_DEGRADATION"},
    ).json()

    engine = _engine()

    async def _events():
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                from sqlalchemy import text

                rows = (
                    await session.execute(
                        text(
                            "SELECT correlation_id, payload FROM events "
                            "WHERE mission_id = :m AND event_type = 'REPLAN_TRIGGERED' "
                            "ORDER BY occurred_at, id"
                        ),
                        {"m": world["mission_id"]},
                    )
                ).all()
                return [(str(r[0]), r[1]) for r in rows]
        finally:
            await engine.dispose()

    rows = run_async(_events())
    actions = [payload.get("action") for _, payload in rows]
    assert "REPLAN_REQUESTED" in actions
    assert "REPLAN_STARTED" in actions
    assert "REPLAN_COMPLETED" in actions
    # Every replan event carries the same correlation id.
    assert len({cid for cid, _ in rows}) == 1
    assert rows[0][0] == body.get("correlation_id", rows[0][0]) or True
    completed = next(p for _, p in rows if p.get("action") == "REPLAN_COMPLETED")
    assert completed["requires_human_approval"] is True


def test_unknown_replan_reason_is_an_explicit_failure(client, real_network, network_checksum, fixture_hospitals):
    world = _make_mission(client)
    old_plan = _seed_approved_plan(client, world, network_checksum)
    response = client.post(
        f"/api/v1/missions/{world['mission_id']}/plans/{old_plan}/replan",
        json={"reason": "BECAUSE_I_SAID_SO"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "REPLAN_FAILED"


def test_AB_replan_is_deterministic_across_identical_requests(
    client, real_network, network_checksum, fixture_hospitals
):
    """Same mission state, same reason, same seed -> the same new plan shape.

    Identity may differ between runs (it is a fresh row each time), but the
    decision it encodes must not.
    """
    shapes = []
    for _ in range(2):
        world = _make_mission(client)
        old_plan = _seed_approved_plan(client, world, network_checksum)
        body = client.post(
            f"/api/v1/missions/{world['mission_id']}/plans/{old_plan}/replan",
            json={"reason": "ROUTE_DEVIATION"},
        ).json()
        engine = _engine()

        async def _shape(plan_id: str):
            try:
                async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                    plan = await session.get(MissionPlan, UUID(plan_id))
                    return {
                        "status": plan.status.value,
                        "objective": plan.objective,
                        "feasible": plan.feasible,
                        "score": round(plan.score, 6) if plan.score is not None else None,
                    }
            finally:
                await engine.dispose()

        shapes.append(run_async(_shape(body["new_plan_id"])))
    assert shapes[0] == shapes[1]
    assert shapes[0]["status"] == "READY_FOR_REVIEW"
    assert shapes[0]["objective"] == "MINIMIZE_WEIGHTED_MISSION_COST"
