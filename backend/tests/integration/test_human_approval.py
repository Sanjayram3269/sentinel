"""Phase 8 human-approval integration tests (brief section 24, A-AD).

These exercise the authorization boundary against real PostgreSQL/PostGIS, with
no simulator: the point of Phase 8 is that a plan becomes executable only
through a persisted human decision, and that fact must not depend on a
simulation having run.

The recurring assertion across this file is negative -- an unapproved plan must
not execute, a wrong version must not approve, a superseded version must not
execute, a stale network must not authorize. Those are the cases a
demonstration would never reach, so they are the ones worth automating.
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
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.main import app
from app.models import (
    MissionPlan,
    RoadEdge,
    RoadNetwork,
    Route,
    RouteCandidate,
    SimulationRun,
)
from app.models.enums import PlanStatus, RouteStatus

pytestmark = pytest.mark.integration

BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
NETWORK_KEY = "phase8_dev_v1"
CHECKSUM = "phase8-dev-fixture-checksum"
EDGE_A = "p8-edge-a"
EDGE_B = "p8-edge-b"


def _engine():
    return create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)


def run_async(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module", autouse=True)
def prepared_database() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip("set TEST_DATABASE_URL to run Phase 8 integration tests")
    command.upgrade(Config(str(BACKEND_DIR / "alembic.ini")), "head")


@pytest.fixture(scope="module", autouse=True)
def network_settings(monkeypatch_module=None):
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(scope="module")
def dev_network():
    """A development-fixture network whose checksum the staleness test can move."""
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
                            proj_parameter="+proj=longlat +datum=WGS84 +no_defs",
                            orig_boundary="phase8-development-fixture",
                            source_checksum=CHECKSUM,
                        )
                        session.add(network)
                        await session.flush()
                    for index, external in enumerate((EDGE_A, EDGE_B)):
                        existing = await session.scalar(
                            select(RoadEdge).where(
                                RoadEdge.network_id == network.id,
                                RoadEdge.external_id == external,
                            )
                        )
                        if existing is None:
                            session.add(
                                RoadEdge(
                                    network_id=network.id,
                                    source="development_fixture",
                                    external_id=external,
                                    from_node=f"p8-n{index}",
                                    to_node=f"p8-n{index + 1}",
                                    length_m=500.0,
                                    speed_limit_kmh=50.0,
                                    road_class="primary",
                                    lanes=1,
                                    has_signal=False,
                                    geometry=WKTElement(
                                        f"LINESTRING(77.59 12.9{index}, 77.591 12.9{index + 1})",
                                        srid=4326,
                                    ),
                                )
                            )
                            await session.flush()
                    return [
                        str(item.id)
                        for item in (
                            await session.scalars(
                                select(RoadEdge)
                                .where(RoadEdge.network_id == network.id)
                                .order_by(RoadEdge.external_id)
                            )
                        ).all()
                    ]
        finally:
            await engine.dispose()

    return run_async(_build())


@pytest.fixture
def client():
    get_settings.cache_clear()
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


# ======================================================================
# World building
# ======================================================================


def _make_mission(client: TestClient) -> dict:
    mission_id = client.post(
        "/api/v1/missions", json={"objective": "phase 8 authorization"}
    ).json()["id"]
    vehicle_id = client.post(
        "/api/v1/vehicles",
        json={
            "mission_id": mission_id,
            "vehicle_type": "AMBULANCE",
            "call_sign": f"P8-{uuid4()}",
        },
    ).json()["id"]
    return {"mission_id": mission_id, "vehicle_id": vehicle_id}


def _write_plan(
    client: TestClient,
    world: dict,
    *,
    canonical_ids: list[str],
    status: PlanStatus = PlanStatus.READY_FOR_REVIEW,
    feasible: bool = True,
    checksum: str | None = CHECKSUM,
    version: int = 1,
) -> str:
    """Persist a plan that selects a route over the given canonical edges."""
    route_id = uuid4()
    engine = _engine()

    async def _persist():
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                async with session.begin():
                    session.add(
                        Route(
                            id=route_id,
                            mission_id=UUID(world["mission_id"]),
                            vehicle_id=UUID(world["vehicle_id"]),
                            status=RouteStatus.ACTIVE,
                            name="phase 8 route",
                            geometry=WKTElement(
                                "LINESTRING(77.59 12.97, 77.592 12.972)", srid=4326
                            ),
                            distance_meters=1000.0,
                            estimated_duration_seconds=100,
                            risk_score=0.1,
                        )
                    )
                    session.add(
                        RouteCandidate(
                            mission_id=UUID(world["mission_id"]),
                            vehicle_id=UUID(world["vehicle_id"]),
                            route_id=route_id,
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
                    session.add(
                        MissionPlan(
                            mission_id=UUID(world["mission_id"]),
                            version=version,
                            status=status,
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
                                    "diversity_from_primary": 0.42,
                                    "eta_seconds": 100,
                                    "distance_meters": 1000.0,
                                },
                                "score": {"value": 0.0714, "coverage": 0.7},
                            },
                            score=0.0714,
                            feasible=feasible,
                            rationale="phase 8 fixture plan",
                            network_key=NETWORK_KEY,
                            network_checksum=checksum,
                        )
                    )
                    return str(route_id)
        finally:
            await engine.dispose()

    run_async(_persist())
    plan_id = client.get(
        f"/api/v1/missions/{world['mission_id']}/plans/latest"
    ).json()["id"]
    return plan_id


def _approve(client: TestClient, world: dict, plan_id: str, version: int = 1, **body):
    payload = {"plan_version": version, "reviewer_id": "dispatcher-1", **body}
    return client.post(
        f"/api/v1/missions/{world['mission_id']}/plans/{plan_id}/approve", json=payload
    )


def _execute(client: TestClient, world: dict, plan_id: str):
    return client.post(
        f"/api/v1/missions/{world['mission_id']}/plans/{plan_id}/execute"
    )


def _review(client: TestClient, world: dict, plan_id: str):
    return client.get(
        f"/api/v1/missions/{world['mission_id']}/plans/{plan_id}/review"
    )


def _plan_row(plan_id: str) -> dict:
    engine = _engine()

    async def _load():
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                row = await session.get(MissionPlan, UUID(plan_id))
                return {
                    "status": row.status.value,
                    "version": row.version,
                    "score": row.score,
                    "feasible": row.feasible,
                }
        finally:
            await engine.dispose()

    return run_async(_load())


def _approvals(plan_id: str) -> list[dict]:
    engine = _engine()

    async def _load():
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                from app.models.plan import PlanApproval

                rows = (
                    await session.scalars(
                        select(PlanApproval)
                        .where(PlanApproval.plan_id == UUID(plan_id))
                        .order_by(PlanApproval.created_at)
                    )
                ).all()
                return [
                    {
                        "status": item.status.value,
                        "plan_version": item.plan_version,
                        "decision": item.decision,
                        "reviewer_id": item.reviewer_id,
                        "previous_plan_status": item.previous_plan_status,
                        "new_plan_status": item.new_plan_status,
                    }
                    for item in rows
                ]
        finally:
            await engine.dispose()

    return run_async(_load())


def _events(mission_id: str, event_type: str) -> list[dict]:
    engine = _engine()

    async def _load():
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                rows = (
                    await session.execute(
                        text(
                            "SELECT id, correlation_id, payload FROM events "
                            "WHERE mission_id = :mission_id AND event_type = :event_type "
                            "ORDER BY occurred_at, id"
                        ),
                        {"mission_id": mission_id, "event_type": event_type},
                    )
                ).all()
                return [
                    {"id": str(row[0]), "correlation_id": str(row[1]), "payload": row[2]}
                    for row in rows
                ]
        finally:
            await engine.dispose()

    return run_async(_load())


def _audit_rows(mission_id: str) -> list[dict]:
    engine = _engine()

    async def _load():
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                from app.models import AuditLog

                rows = (
                    await session.scalars(
                        select(AuditLog)
                        .where(AuditLog.mission_id == UUID(mission_id))
                        .order_by(AuditLog.created_at)
                    )
                ).all()
                return [
                    {"action": item.action, "actor_type": item.actor_type}
                    for item in rows
                ]
        finally:
            await engine.dispose()

    return run_async(_load())


# ======================================================================
# A / B: review package
# ======================================================================


def test_A_review_package_returns_complete_plan_context(client, dev_network):
    world = _make_mission(client)
    plan_id = _write_plan(client, world, canonical_ids=dev_network)

    response = _review(client, world, plan_id)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["mission"]["id"] == world["mission_id"]
    assert body["plan"]["id"] == plan_id
    assert body["plan"]["version"] == 1
    assert body["plan"]["feasible"] is True
    assert body["selected"]["route_id"]
    assert body["selected"]["vehicle_id"] == world["vehicle_id"]
    assert body["route"]["canonical_road_edge_ids"] == dev_network
    assert body["route"]["diversity_from_primary"] == 0.42
    assert body["route"]["alternatives"], "route alternatives must be shown"
    # The score is labelled as an objective, never as confidence.
    assert "not a calibrated" in body["plan"]["score_note"]


def test_B_review_includes_phase7_evidence_or_says_why_absent(client, dev_network):
    world = _make_mission(client)
    plan_id = _write_plan(client, world, canonical_ids=dev_network)
    body = _review(client, world, plan_id).json()

    evidence = body["simulation_evidence"]
    assert evidence["execution_mode"] == "PROTOTYPE"
    assert evidence["evidence_status"] in {
        "NO_EVIDENCE",
        "MEASURED_COMPARISON",
        "INCOMPLETE_PAIR",
    }
    if evidence["evidence_status"] == "NO_EVIDENCE":
        # Absence must be explicit and explained, never a zero improvement.
        assert evidence["reason"]
        assert evidence["travel_time_improvement_percent"] is None
        assert any("No CLEARPATH simulation evidence" in w for w in body["warnings"])
    assert body["approval"]["authorized_for_execution"] is False
    assert body["network"]["matches_plan"] is True


# ======================================================================
# C / D / M / N: approval persistence, binding, idempotency, conflict
# ======================================================================


def test_C_approval_persists_with_reviewer_and_reason(client, dev_network):
    world = _make_mission(client)
    plan_id = _write_plan(client, world, canonical_ids=dev_network)

    response = _approve(
        client,
        world,
        plan_id,
        comment="Reviewed route, hospital suitability and CLEARPATH evidence.",
    )
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["approval"]["plan_version"] == 1
    assert body["approval"]["decision"] == "APPROVED"
    assert body["approval"]["reviewer_id"] == "dispatcher-1"
    assert "Reviewed route" in body["approval"]["comment"]
    assert body["approval"]["previous_plan_status"] == "READY_FOR_REVIEW"
    assert body["approval"]["new_plan_status"] == "APPROVED"
    # Recorded so no reader mistakes it for an authenticated principal.
    assert (
        body["approval"]["context"]["reviewer_identity_kind"]
        == "development_prototype_identity"
    )
    # Approval alone does not execute anything.
    assert body["execution_authorized"] is False

    rows = _approvals(plan_id)
    assert len(rows) == 1
    assert rows[0]["status"] == "APPROVED"
    assert _plan_row(plan_id)["status"] == "APPROVED"


def test_D_approval_binds_to_the_exact_plan_version(client, dev_network):
    world = _make_mission(client)
    plan_id = _write_plan(client, world, canonical_ids=dev_network)

    # Version 2 has never been decided.
    response = client.post(
        f"/api/v1/missions/{world['mission_id']}/plans/{plan_id}/approve",
        json={"plan_version": 2, "reviewer_id": "dispatcher-2"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PLAN_VERSION_MISMATCH"
    assert len(_approvals(plan_id)) == 0


def test_M_duplicate_approval_is_idempotent(client, dev_network):
    world = _make_mission(client)
    plan_id = _write_plan(client, world, canonical_ids=dev_network)

    first = _approve(client, world, plan_id)
    assert first.status_code == 201
    assert first.json()["idempotent_replay"] is False

    second = _approve(client, world, plan_id)
    assert second.status_code == 201, second.text
    assert second.json()["idempotent_replay"] is True
    # One row, not two contradictory approvals.
    assert len(_approvals(plan_id)) == 1
    assert _approve(client, world, plan_id).json()["approval"]["approval_id"] == first.json()[
        "approval"
    ]["approval_id"]


def test_N_conflicting_decision_is_rejected(client, dev_network):
    world = _make_mission(client)
    plan_id = _write_plan(client, world, canonical_ids=dev_network)
    _approve(client, world, plan_id)

    conflict = client.post(
        f"/api/v1/missions/{world['mission_id']}/plans/{plan_id}/reject",
        json={
            "plan_version": 1,
            "reviewer_id": "dispatcher-2",
            "comment": "changed my mind",
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "CONFLICTING_DECISION"
    assert len(_approvals(plan_id)) == 1
    assert _approvals(plan_id)[0]["status"] == "APPROVED"


# ======================================================================
# E / F / G / H: execution gate refusals
# ======================================================================


def test_E_wrong_version_cannot_approve(client, dev_network):
    world = _make_mission(client)
    plan_id = _write_plan(client, world, canonical_ids=dev_network)
    response = _approve(client, world, plan_id, version=99)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PLAN_VERSION_MISMATCH"


def test_F_rejected_plan_cannot_execute(client, dev_network):
    world = _make_mission(client)
    plan_id = _write_plan(client, world, canonical_ids=dev_network)
    response = client.post(
        f"/api/v1/missions/{world['mission_id']}/plans/{plan_id}/reject",
        json={
            "plan_version": 1,
            "reviewer_id": "supervisor-1",
            "comment": "Primary route too risky under current hazard state.",
        },
    )
    assert response.status_code == 201

    denied = _execute(client, world, plan_id)
    assert denied.status_code == 409
    assert denied.json()["detail"]["code"] in {"SAFETY_REJECTED", "EXECUTION_NOT_AUTHORIZED"}
    assert _plan_row(plan_id)["status"] == "REJECTED"


def test_G_unapproved_plan_cannot_execute(client, dev_network):
    world = _make_mission(client)
    plan_id = _write_plan(client, world, canonical_ids=dev_network)

    denied = _execute(client, world, plan_id)
    assert denied.status_code == 409
    codes = denied.json()["detail"]["code"]
    assert codes == "APPROVAL_NOT_FOUND"
    assert _approvals(plan_id) == []


def test_H_approved_plan_passes_the_execution_gate(client, dev_network):
    world = _make_mission(client)
    plan_id = _write_plan(client, world, canonical_ids=dev_network)
    _approve(client, world, plan_id)

    allowed = _execute(client, world, plan_id)
    assert allowed.status_code == 201, allowed.text
    body = allowed.json()
    assert body["execution_mode"] == "PROTOTYPE"
    assert body["authorized_by_approval_id"]
    assert body["reviewer_id"] == "dispatcher-1"
    assert "No real vehicle" in body["notice"]
    assert _plan_row(plan_id)["status"] == "EXECUTING"


def test_infeasible_plan_cannot_be_approved_or_execute(client, dev_network):
    world = _make_mission(client)
    plan_id = _write_plan(
        client, world, canonical_ids=dev_network, feasible=False
    )
    denied = _approve(client, world, plan_id)
    assert denied.status_code == 409
    assert denied.json()["detail"]["code"] == "PLAN_INFEASIBLE"

    # Even if the status were forced, the gate refuses.
    engine = _engine()

    async def _force():
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                async with session.begin():
                    row = await session.get(MissionPlan, UUID(plan_id))
                    row.status = PlanStatus.APPROVED
        finally:
            await engine.dispose()

    run_async(_force())
    refused = _execute(client, world, plan_id)
    assert refused.status_code == 409
    assert "PLAN_INFEASIBLE" in _gate_reasons(client, world, plan_id)


def test_authorization_endpoint_explains_every_reason(client, dev_network):
    world = _make_mission(client)
    plan_id = _write_plan(client, world, canonical_ids=dev_network)
    body = client.get(
        f"/api/v1/missions/{world['mission_id']}/plans/{plan_id}/authorization"
    ).json()
    assert body["decision"] == "NOT_AUTHORIZED"
    assert any(item["code"] == "APPROVAL_NOT_FOUND" for item in body["reasons"])
    # Read-only: it changed nothing.
    assert _plan_row(plan_id)["status"] == "READY_FOR_REVIEW"


# ======================================================================
# I / J / W: modification, supersession, immutability
# ======================================================================


def test_I_modified_plan_creates_a_new_version_and_invalidates_approval(
    client, dev_network
):
    world = _make_mission(client)
    plan_id = _write_plan(client, world, canonical_ids=dev_network)
    _approve(client, world, plan_id)

    response = client.post(
        f"/api/v1/missions/{world['mission_id']}/plans/{plan_id}/modify",
        json={
            "plan_version": 1,
            "reviewer_id": "dispatcher-1",
            "comment": "Changed route selection.",
            "modifications": [
                {"field": "ROUTE", "value": None, "reason": "manual override"}
            ],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["previous_plan_version"] == 1
    assert body["previous_plan_status"] == "APPROVED"
    assert body["new_plan_version"] == 2
    assert body["new_plan_status"] == "READY_FOR_REVIEW"
    assert body["requires_human_approval"] is True
    assert body["previous_approval_invalidated"] is True

    # The old version is frozen and still carries its own approval.
    assert _plan_row(plan_id)["status"] == "SUPERSEDED"
    assert _approvals(plan_id)[0]["status"] == "APPROVED"

    # The new version has no approval and cannot execute.
    denied = _execute(client, world, body["new_plan_id"])
    assert denied.status_code == 409
    assert denied.json()["detail"]["code"] == "APPROVAL_NOT_FOUND"


def test_J_superseded_plan_cannot_execute(client, dev_network):
    world = _make_mission(client)
    plan_id = _write_plan(client, world, canonical_ids=dev_network)
    _approve(client, world, plan_id)
    client.post(
        f"/api/v1/missions/{world['mission_id']}/plans/{plan_id}/modify",
        json={
            "plan_version": 1,
            "reviewer_id": "dispatcher-1",
            "modifications": [{"field": "HOSPITAL", "reason": "closer"}],
        },
    )
    denied = _execute(client, world, plan_id)
    assert denied.status_code == 409
    assert "PLAN_SUPERSEDED" in _gate_reasons(client, world, plan_id)


def test_W_old_approved_plan_remains_immutable(client, dev_network):
    world = _make_mission(client)
    plan_id = _write_plan(client, world, canonical_ids=dev_network)
    before = _plan_row(plan_id)
    _approve(client, world, plan_id)
    client.post(
        f"/api/v1/missions/{world['mission_id']}/plans/{plan_id}/modify",
        json={
            "plan_version": 1,
            "reviewer_id": "dispatcher-1",
            "modifications": [{"field": "HOSPITAL", "reason": "closer"}],
        },
    )
    after = _plan_row(plan_id)
    # Only the status reflects supersession; score, version and feasibility are
    # the historical record and are untouched.
    assert after["score"] == before["score"]
    assert after["version"] == before["version"]
    assert after["feasible"] == before["feasible"]
    assert after["status"] == "SUPERSEDED"


# ======================================================================
# K: stale network
# ======================================================================


def test_K_stale_network_blocks_approval_and_authorization(client, dev_network):
    world = _make_mission(client)
    plan_id = _write_plan(
        client, world, canonical_ids=dev_network, checksum="checksum-from-long-ago"
    )

    denied = _approve(client, world, plan_id)
    assert denied.status_code == 409
    assert denied.json()["detail"]["code"] == "STALE_NETWORK"
    assert _approvals(plan_id) == []

    # Even a status forced past approval is refused by the gate.
    engine = _engine()

    async def _force():
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                async with session.begin():
                    row = await session.get(MissionPlan, UUID(plan_id))
                    row.status = PlanStatus.APPROVED
        finally:
            await engine.dispose()

    run_async(_force())
    refused = _execute(client, world, plan_id)
    assert refused.status_code == 409
    # Asserted on the structured reason, not on prose: the gate refuses for a
    # reason the caller can act on, not for a phrase it happens to contain.
    assert "STALE_NETWORK" in _gate_reasons(client, world, plan_id)


def test_plan_without_a_checksum_cannot_be_authorized(client, dev_network):
    world = _make_mission(client)
    plan_id = _write_plan(client, world, canonical_ids=dev_network, checksum=None)
    denied = _approve(client, world, plan_id)
    assert denied.status_code == 409
    assert denied.json()["detail"]["code"] == "STALE_NETWORK"


# ======================================================================
# O / P / Q / R: events
# ======================================================================


def test_O_approval_event_is_emitted_with_identity_and_version(client, dev_network):
    world = _make_mission(client)
    plan_id = _write_plan(client, world, canonical_ids=dev_network)
    _approve(client, world, plan_id)

    rows = _events(world["mission_id"], "PLAN_APPROVED")
    assert len(rows) == 1
    payload = rows[0]["payload"]
    assert payload["plan_id"] == plan_id
    assert payload["plan_version"] == 1
    assert payload["reviewer_id"] == "dispatcher-1"
    assert payload["reviewer_identity_kind"] == "development_prototype_identity"
    assert payload["previous_plan_status"] == "READY_FOR_REVIEW"
    assert payload["new_plan_status"] == "APPROVED"


def test_P_rejection_event_is_emitted(client, dev_network):
    world = _make_mission(client)
    plan_id = _write_plan(client, world, canonical_ids=dev_network)
    client.post(
        f"/api/v1/missions/{world['mission_id']}/plans/{plan_id}/reject",
        json={"plan_version": 1, "reviewer_id": "sup", "comment": "too risky"},
    )
    rows = _events(world["mission_id"], "PLAN_REJECTED")
    assert len(rows) == 1
    assert rows[0]["payload"]["comment"] == "too risky"


def test_Q_modification_event_is_emitted(client, dev_network):
    world = _make_mission(client)
    plan_id = _write_plan(client, world, canonical_ids=dev_network)
    client.post(
        f"/api/v1/missions/{world['mission_id']}/plans/{plan_id}/modify",
        json={
            "plan_version": 1,
            "reviewer_id": "dispatcher-1",
            "modifications": [{"field": "HOSPITAL", "reason": "closer"}],
        },
    )
    rows = _events(world["mission_id"], "PLAN_CREATED")
    payloads = [item["payload"] for item in rows]
    assert any(item.get("action") == "PLAN_MODIFIED" for item in payloads)
    modified = next(i for i in payloads if i.get("action") == "PLAN_MODIFIED")
    assert modified["requires_human_approval"] is True


def test_R_execution_event_is_emitted_as_prototype(client, dev_network):
    world = _make_mission(client)
    plan_id = _write_plan(client, world, canonical_ids=dev_network)
    _approve(client, world, plan_id)
    _execute(client, world, plan_id)

    rows = _events(world["mission_id"], "MISSION_EXECUTION_STARTED")
    starts = [i["payload"] for i in rows if i["payload"].get("action") == "MISSION_EXECUTION_STARTED"]
    assert len(starts) == 1
    assert starts[0]["execution_mode"] == "PROTOTYPE"
    assert "No real vehicle" in starts[0]["notice"]


def test_execution_cannot_start_twice(client, dev_network):
    world = _make_mission(client)
    plan_id = _write_plan(client, world, canonical_ids=dev_network)
    _approve(client, world, plan_id)
    assert _execute(client, world, plan_id).status_code == 201
    again = _execute(client, world, plan_id)
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "EXECUTION_ALREADY_STARTED"


# ======================================================================
# S: observation
# ======================================================================


def test_S_observation_reports_no_telemetry_as_its_own_outcome(client, dev_network):
    world = _make_mission(client)
    plan_id = _write_plan(client, world, canonical_ids=dev_network)
    body = client.post(
        f"/api/v1/missions/{world['mission_id']}/plans/{plan_id}/observe", json={}
    ).json()

    assert body["observed_samples"] == 0
    # Absent data is not evidence of health, and not evidence of deviation.
    assert body["deviation_detected"] is False
    assert body["detail"]["telemetry_available"] is False
    assert "No vehicle telemetry" in body["detail"]["note"]


def test_S_observation_detects_an_offline_vehicle(client, dev_network):
    from app.models import Vehicle
    from app.models.enums import VehicleStatus

    world = _make_mission(client)
    plan_id = _write_plan(client, world, canonical_ids=dev_network)
    _approve(client, world, plan_id)
    _execute(client, world, plan_id)

    engine = _engine()

    async def _offline():
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                async with session.begin():
                    row = await session.get(Vehicle, UUID(world["vehicle_id"]))
                    row.status = VehicleStatus.OFFLINE
        finally:
            await engine.dispose()

    run_async(_offline())
    body = client.post(
        f"/api/v1/missions/{world['mission_id']}/plans/{plan_id}/observe", json={}
    ).json()
    assert body["deviation_detected"] is True
    assert "VEHICLE_DEVIATION" in body["reasons"]
    assert body["replan_required"] is True


# ======================================================================
# AC / AD: persistence integrity
# ======================================================================


def test_AD_failed_approval_rolls_back_completely(client, dev_network):
    world = _make_mission(client)
    plan_id = _write_plan(client, world, canonical_ids=dev_network, checksum="stale")

    # The approval fails on the stale network. Nothing may be left behind.
    assert _approve(client, world, plan_id).status_code == 409
    assert _approvals(plan_id) == []
    assert _plan_row(plan_id)["status"] == "READY_FOR_REVIEW"
    assert _events(world["mission_id"], "PLAN_APPROVED") == []
    assert not any(
        item["action"] == "PLAN_APPROVED" for item in _audit_rows(world["mission_id"])
    )


def test_audit_trail_records_who_and_what(client, dev_network):
    world = _make_mission(client)
    plan_id = _write_plan(client, world, canonical_ids=dev_network)
    _approve(client, world, plan_id, comment="looks good")

    rows = _audit_rows(world["mission_id"])
    assert any(item["action"] == "PLAN_APPROVED" for item in rows)
    entry = next(item for item in rows if item["action"] == "PLAN_APPROVED")
    assert entry["actor_type"] == "development_prototype_identity"


def test_AC_what_if_remains_non_persistent_through_approval(client, dev_network):
    world = _make_mission(client)
    plan_id = _write_plan(client, world, canonical_ids=dev_network)
    _approve(client, world, plan_id)

    what_if = client.post(
        f"/api/v1/missions/{world['mission_id']}/plans/whatif",
        json={
            "component": "ROUTE",
            "component_id": _selected_route_id(plan_id),
            "baseline_plan_id": plan_id,
        },
    )
    assert what_if.status_code in (200, 422), what_if.text

    plans = _plan_versions(world["mission_id"])
    assert len(plans) == 1, "what-if must not persist a plan version"


def _gate_reasons(client: TestClient, world: dict, plan_id: str) -> list[str]:
    """The gate's structured reason codes, which is what the rule is stated in."""
    body = client.get(
        f"/api/v1/missions/{world['mission_id']}/plans/{plan_id}/authorization"
    ).json()
    return [item["code"] for item in body.get("reasons", [])]


def _selected_route_id(plan_id: str) -> str:
    engine = _engine()

    async def _load():
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                row = await session.get(MissionPlan, UUID(plan_id))
                return row.plan_payload["selected"]["route_id"]
        finally:
            await engine.dispose()

    return run_async(_load())


def _plan_versions(mission_id: str) -> list[dict]:
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
                return [{"id": str(r.id), "version": r.version} for r in rows]
        finally:
            await engine.dispose()

    return run_async(_load())


# ======================================================================
# AA: safety boundary
# ======================================================================


def test_AA_no_real_signal_or_dispatch_client_exists():
    import ast
    import pathlib

    offenders: list[str] = []
    for path in pathlib.Path("app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in {"requests", "traffic_signal_sdk"}:
                        offenders.append(f"{path}:{node.lineno}:{alias.name}")
    assert offenders == [], offenders


def test_Z_execution_response_is_explicitly_prototype(client, dev_network):
    world = _make_mission(client)
    plan_id = _write_plan(client, world, canonical_ids=dev_network)
    _approve(client, world, plan_id)
    body = _execute(client, world, plan_id).json()
    assert body["execution_mode"] == "PROTOTYPE"
    assert "no actuator" in body["notice"].lower()


def test_version_binding_is_load_bearing_on_its_own(client, dev_network):
    """The approval must bind to the version, not merely to a plan.

    Everything else in the gate -- plan status, feasibility, network freshness
    -- could in principle be satisfied by a bug elsewhere that marks a plan
    APPROVED. This is the case where the version binding is the only thing
    standing between an old approval and a new plan that nobody reviewed, so it
    is tested in isolation: the plan's status is forced to APPROVED and the
    only approval on record belongs to a different version.
    """
    world = _make_mission(client)
    old_id = _write_plan(client, world, canonical_ids=dev_network)
    _approve(client, world, old_id)

    modified = client.post(
        f"/api/v1/missions/{world['mission_id']}/plans/{old_id}/modify",
        json={
            "plan_version": 1,
            "reviewer_id": "dispatcher-1",
            "modifications": [{"field": "HOSPITAL", "reason": "closer trauma bay"}],
        },
    ).json()
    new_id = modified["new_plan_id"]

    # Simulate the failure mode the version binding exists to catch: something
    # marks the new version APPROVED without a human deciding it.
    engine = _engine()

    async def _force():
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                async with session.begin():
                    row = await session.get(MissionPlan, UUID(new_id))
                    row.status = PlanStatus.APPROVED
        finally:
            await engine.dispose()

    run_async(_force())

    # The version-1 approval must not satisfy version 2. Which code reports
    # that depends on which of the two version defences fires first -- the
    # lookup filter, or the explicit comparison -- so either is accepted here
    # and the safety property asserted is the refusal itself.
    reasons = _gate_reasons(client, world, new_id)
    assert reasons, "the gate must refuse a version nobody approved"
    assert set(reasons) <= {
        "PLAN_VERSION_MISMATCH",
        "APPROVAL_NOT_FOUND",
        "SAFETY_REJECTED",
    }, reasons

    denied = _execute(client, world, new_id)
    assert denied.status_code == 409
    # The gate refused without changing the plan it refused.
    assert _plan_row(new_id)["status"] == "APPROVED", "the forced status is untouched"


def test_approval_for_one_version_never_satisfies_another(client, dev_network):
    """Directly: no approval row exists for the version under review."""
    world = _make_mission(client)
    old_id = _write_plan(client, world, canonical_ids=dev_network)
    _approve(client, world, old_id)
    modified = client.post(
        f"/api/v1/missions/{world['mission_id']}/plans/{old_id}/modify",
        json={
            "plan_version": 1,
            "reviewer_id": "dispatcher-1",
            "modifications": [{"field": "HOSPITAL", "reason": "closer"}],
        },
    ).json()
    new_id = modified["new_plan_id"]

    # Version 2 carries only MODIFIED provenance -- it records that a reviewer
    # changed it, which is not a decision authorising it. No APPROVED or
    # REJECTED decision exists for it, even though version 1 has one.
    decisions = [
        item for item in _approvals(new_id) if item["status"] != "MODIFIED"
    ]
    assert decisions == [], decisions
    assert _approvals(old_id)[0]["plan_version"] == 1
    assert "APPROVAL_NOT_FOUND" in _gate_reasons(client, world, new_id)


def test_approval_row_bound_to_a_different_version_cannot_authorize(client, dev_network):
    """A mismatched approval row must not authorize the plan it points at.

    Each plan version is its own row, so ``plan_id`` already binds a normal
    approval. The ``plan_version`` column is the second, independent defence:
    it stops an approval row whose version does not match its plan -- written
    by a migration, a bad backfill, or a concurrency bug -- from silently
    authorizing the wrong revision. This crafts that mismatch directly, because
    no legitimate code path produces one.
    """
    from app.models.enums import ApprovalStatus

    world = _make_mission(client)
    plan_id = _write_plan(client, world, canonical_ids=dev_network)

    engine = _engine()

    async def _write_mismatched():
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                async with session.begin():
                    from app.models.plan import PlanApproval

                    session.add(
                        PlanApproval(
                            plan_id=UUID(plan_id),
                            # The plan is version 1. This row claims version 7.
                            plan_version=7,
                            status=ApprovalStatus.APPROVED,
                            decision="APPROVED",
                            reviewer_id="ghost-reviewer",
                            previous_plan_status="READY_FOR_REVIEW",
                            new_plan_status="APPROVED",
                        )
                    )
                    row = await session.get(MissionPlan, UUID(plan_id))
                    row.status = PlanStatus.APPROVED
        finally:
            await engine.dispose()

    run_async(_write_mismatched())

    reasons = _gate_reasons(client, world, plan_id)
    assert reasons, "a mismatched approval must not authorize anything"
    assert "APPROVAL_NOT_FOUND" in reasons or "PLAN_VERSION_MISMATCH" in reasons, reasons

    denied = _execute(client, world, plan_id)
    assert denied.status_code == 409, denied.text


def test_rejected_decision_blocks_even_if_status_disagrees(client, dev_network):
    """A REJECTED decision is refused on its own, not only via plan status.

    ``PlanStatus.REJECTED`` and a ``REJECTED`` approval normally agree, so
    removing either check alone still blocks execution through the other. This
    crafts the disagreement -- a plan whose status says APPROVED while the only
    human decision on record is a rejection -- which is what the explicit
    decision check exists to catch.
    """
    from app.models.enums import ApprovalStatus

    world = _make_mission(client)
    plan_id = _write_plan(client, world, canonical_ids=dev_network)
    client.post(
        f"/api/v1/missions/{world['mission_id']}/plans/{plan_id}/reject",
        json={
            "plan_version": 1,
            "reviewer_id": "supervisor-1",
            "comment": "Primary route too risky under current hazard state.",
        },
    )

    engine = _engine()

    async def _force():
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                async with session.begin():
                    row = await session.get(MissionPlan, UUID(plan_id))
                    row.status = PlanStatus.APPROVED
        finally:
            await engine.dispose()

    run_async(_force())

    reasons = _gate_reasons(client, world, plan_id)
    assert reasons, "a rejected decision must not authorize execution"
    assert "SAFETY_REJECTED" in reasons or "APPROVAL_NOT_FOUND" in reasons, reasons

    denied = _execute(client, world, plan_id)
    assert denied.status_code == 409, denied.text
    assert _plan_row(plan_id)["status"] == "APPROVED", "the gate did not mutate state"


def test_approval_and_execution_authorization_are_distinct_states(client, dev_network):
    """APPROVED is the human's yes; EXECUTION_AUTHORIZED is the server's re-check.

    Collapsing them would make it impossible to tell from the persisted history
    whether a plan was merely approved, or whether it also cleared the gate.
    """
    world = _make_mission(client)
    plan_id = _write_plan(client, world, canonical_ids=dev_network)

    _approve(client, world, plan_id)
    # Approved, but not yet authorized for execution.
    assert _plan_row(plan_id)["status"] == "APPROVED"
    body = client.get(
        f"/api/v1/missions/{world['mission_id']}/plans/{plan_id}/authorization"
    ).json()
    # The gate agrees it is executable, but reading it must not change state.
    assert body["decision"] == "AUTHORIZED", body
    assert _plan_row(plan_id)["status"] == "APPROVED", "the gate read is read-only"

    allowed = _execute(client, world, plan_id)
    assert allowed.status_code == 201
    assert _plan_row(plan_id)["status"] == "EXECUTING"

    events = _events(world["mission_id"], "EXECUTION_AUTHORIZED")
    assert len(events) == 1
    assert events[0]["payload"]["action"] == "EXECUTION_AUTHORIZED"
    assert events[0]["payload"]["previous_plan_status"] == "APPROVED"
