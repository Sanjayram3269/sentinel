"""The review package a human sees before authorizing a plan.

Assembled here rather than in the endpoint because the same payload is needed
by :class:`~app.services.approval.ApprovalService` when it snapshots evidence at
decision time: the audit trail must record what the reviewer *could* see, so the
builder is the single definition of that view.

Two rules shape this payload:

* The optimizer score and the CLEARPATH measurement are reported separately and
  never combined. The score ranks selection options; the measurement is what
  one simulated run observed. Averaging them would produce a number that means
  neither.
* Anything unavailable is named. A missing metric is ``None`` with a reason,
  not a zero and not an estimate.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MissionPlan, RoadNetwork, Route, RouteCandidate, SimulationRun
from app.models.enums import SimulationStatus

logger = logging.getLogger(__name__)

_UNAVAILABLE = "unavailable"


async def latest_simulation_evidence(
    db: AsyncSession, plan: MissionPlan
) -> dict[str, Any]:
    """The most recent baseline/CLEARPATH pair measured for this plan version.

    Matched on the plan's selected route and vehicle, not merely on the
    mission: a Phase 7 measurement of a superseded route is not evidence about
    this plan, and presenting it as such would be the exact mistake Phase 8
    exists to prevent.
    """
    payload = plan.plan_payload or {}
    route_id = (payload.get("selected") or {}).get("route_id")
    if route_id is None:
        return _no_evidence("Plan selects no route, so no corridor can be simulated")

    route = await db.get(Route, UUID(str(route_id)))
    if route is None:
        return _no_evidence(f"Selected route {route_id} no longer exists")

    runs = (
        await db.scalars(
            select(SimulationRun)
            .where(
                SimulationRun.mission_id == plan.mission_id,
                SimulationRun.status == SimulationStatus.COMPLETED,
            )
            .order_by(SimulationRun.created_at.desc(), SimulationRun.id)
            .limit(40)
        )
    ).all()

    baseline = None
    clearpath = None
    for run in runs:
        configuration = run.configuration or {}
        if str(configuration.get("route_id") or "") != str(route_id):
            continue
        if configuration.get("vehicle_id") not in (None, str(route.vehicle_id)):
            continue
        mode = configuration.get("mode")
        if mode == "BASELINE" and baseline is None:
            baseline = run
        elif mode == "CLEARPATH" and clearpath is None:
            clearpath = run

    if baseline is None and clearpath is None:
        return _no_evidence(
            "No CLEARPATH simulation evidence exists for this plan's selected route",
            route_id=str(route_id),
        )

    # A pair is only comparable when both halves exist and shared a seed.
    baseline_metrics = (baseline.metrics or {}) if baseline else {}
    clearpath_metrics = (clearpath.metrics or {}) if clearpath else {}
    comparable = baseline is not None and clearpath is not None
    seed_match = (
        comparable
        and baseline.scenario_seed == clearpath.scenario_seed
        if comparable
        else False
    )
    if comparable and not seed_match:
        logger.warning(
            "Baseline and CLEARPATH for route %s were measured under different seeds",
            route_id,
        )

    travel_delta = _delta(
        baseline_metrics.get("emergency_vehicle_travel_time_seconds"),
        clearpath_metrics.get("emergency_vehicle_travel_time_seconds"),
    )
    improvement = _improvement(
        baseline_metrics.get("emergency_vehicle_travel_time_seconds"),
        clearpath_metrics.get("emergency_vehicle_travel_time_seconds"),
    )

    if comparable and seed_match:
        status = "MEASURED_COMPARISON"
    elif baseline is not None or clearpath is not None:
        status = "INCOMPLETE_PAIR"
    else:  # pragma: no cover - guarded above
        status = "NO_EVIDENCE"

    return {
        "evidence_status": status,
        "comparable": comparable and seed_match,
        "baseline_simulation_id": str(baseline.id) if baseline else None,
        "clearpath_simulation_id": str(clearpath.id) if clearpath else None,
        "baseline_metrics": baseline_metrics or None,
        "clearpath_metrics": clearpath_metrics or None,
        "travel_time_delta_seconds": travel_delta,
        "travel_time_improvement_percent": improvement,
        "stopped_time_delta_seconds": _delta(
            baseline_metrics.get("stopped_time_seconds"),
            clearpath_metrics.get("stopped_time_seconds"),
        ),
        "stops_delta": _delta(
            baseline_metrics.get("number_of_stops"),
            clearpath_metrics.get("number_of_stops"),
        ),
        "route_completed": {
            "baseline": baseline_metrics.get("route_completed"),
            "clearpath": clearpath_metrics.get("route_completed"),
        },
        "seed": baseline.scenario_seed if baseline else None,
        "safety_status": _safety_from_actions(clearpath),
        "execution_mode": "PROTOTYPE",
        "summary": (
            "CLEARPATH simulation measured for this plan's route"
            if status == "MEASURED_COMPARISON"
            else f"{status}: {status}"
        ),
    }


def _safety_from_actions(clearpath: SimulationRun | None) -> str:
    if clearpath is None:
        return _UNAVAILABLE
    actions = clearpath.clearpath_actions or []
    if not actions:
        return "NO_ACTION_PROPOSED"
    approved = sum(
        1 for item in actions if (item or {}).get("decision") == "APPROVED"
    )
    return "APPROVED_SIMULATION_ONLY" if approved else "REJECTED_SIMULATION_ONLY"


def _no_evidence(reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "evidence_status": "NO_EVIDENCE",
        "comparable": False,
        "baseline_simulation_id": None,
        "clearpath_simulation_id": None,
        "baseline_metrics": None,
        "clearpath_metrics": None,
        "travel_time_delta_seconds": None,
        "travel_time_improvement_percent": None,
        "stopped_time_delta_seconds": None,
        "stops_delta": None,
        "route_completed": {"baseline": None, "clearpath": None},
        "seed": None,
        "safety_status": _UNAVAILABLE,
        "execution_mode": "PROTOTYPE",
        "summary": reason,
        "reason": reason,
        **extra,
    }


def _delta(baseline: Any, clearpath: Any) -> float | None:
    if baseline is None or clearpath is None:
        return None
    return float(clearpath) - float(baseline)


def _improvement(baseline: Any, clearpath: Any) -> float | None:
    """Percentage travel-time reduction, or None when it is not defined.

    A zero baseline has no meaningful percentage, so it is reported as
    unavailable rather than as an infinite or zero improvement.
    """
    if baseline is None or clearpath is None:
        return None
    if float(baseline) <= 0:
        return None
    return (float(baseline) - float(clearpath)) / float(baseline) * 100


async def build_review_package(
    db: AsyncSession, plan: MissionPlan, *, settings: Any = None
) -> dict[str, Any]:
    """Everything needed to decide, in one payload, before any approval exists."""
    from app.models import Mission

    payload = plan.plan_payload or {}
    selected = payload.get("selected") or {}
    route_section = payload.get("route") or {}
    score_section = payload.get("score") or {}

    mission = await db.get(Mission, plan.mission_id)
    incident_ids: list[str] = []
    if mission is not None:
        # Loaded explicitly: touching ``mission.incidents`` lazily would need
        # IO outside the async context, and a plain count is all the reviewer
        # needs here.
        from app.models import Incident

        rows = (
            await db.execute(
                select(Incident.id).where(Incident.mission_id == plan.mission_id)
            )
        ).all()
        incident_ids = [str(row[0]) for row in rows]
    route = None
    candidates: list[dict[str, Any]] = []
    route_id = selected.get("route_id")
    if route_id is not None:
        route = await db.get(Route, UUID(str(route_id)))
        rows = (
            await db.scalars(
                select(RouteCandidate)
                .where(RouteCandidate.mission_id == plan.mission_id)
                .order_by(
                    RouteCandidate.route_rank, RouteCandidate.created_at, RouteCandidate.id
                )
            )
        ).all()
        candidates = [
            {
                "route_id": str(item.route_id),
                "resilience_role": item.resilience_role.value
                if item.resilience_role is not None
                else None,
                "route_rank": item.route_rank,
                "score": item.score,
                "is_selected": str(item.route_id) == str(route_id),
                "backup_viable": item.backup_viable,
                "estimated_duration_seconds": item.estimated_duration_seconds,
                "distance_meters": item.distance_meters,
                "risk_score": item.risk_score,
                "canonical_road_edge_count": len(item.road_segment_ids or []),
            }
            for item in rows
        ]

    evidence = await latest_simulation_evidence(db, plan)
    network = await _network_section(db, plan, settings)

    approval = {
        "approval_state": plan.status.value,
        "plan_id": str(plan.id),
        "plan_version": plan.version,
        "requires_human_decision": plan.status.value in {"READY_FOR_REVIEW", "SUBMITTED"},
        "authorized_for_execution": False,
        "authoritative_note": (
            "No plan is executable until a named human approves this exact version."
        ),
    }

    return {
        "mission": {
            "id": str(plan.mission_id),
            "objective": mission.objective if mission is not None else None,
            "status": mission.status.value if mission is not None else None,
            "incident_ids": incident_ids,
        },
        "plan": {
            "id": str(plan.id),
            "version": plan.version,
            "status": plan.status.value,
            "objective": plan.objective,
            "score": plan.score,
            "score_coverage": score_section.get("coverage"),
            "feasible": plan.feasible,
            "rationale": plan.rationale,
            "created_at": plan.created_at.isoformat() if plan.created_at else None,
            # Reported separately from score on purpose: a reviewer must not
            # read a routing objective as a measure of how good the plan is.
            "score_note": (
                "Optimizer score is a selection objective, not a calibrated "
                "confidence and not a measured outcome."
            ),
        },
        "selected": {
            "hospital_id": selected.get("hospital_id"),
            "route_id": selected.get("route_id"),
            "vehicle_id": str(route.vehicle_id) if route and route.vehicle_id else None,
            "resource_ids": selected.get("resource_ids") or [],
        },
        "route": {
            **route_section,
            "resilience_role": route_section.get("resilience_role"),
            "diversity_from_primary": route_section.get("diversity_from_primary"),
            "eta_seconds": route_section.get("eta_seconds"),
            "distance_meters": route_section.get("distance_meters"),
            "route_status": route.status.value if route is not None else None,
            "alternatives": candidates,
        },
        "simulation_evidence": evidence,
        "network": network,
        "approval": approval,
        "warnings": _warnings(plan, evidence, network),
        "known_limitations": _limitations(evidence),
    }


async def _network_section(
    db: AsyncSession, plan: MissionPlan, settings: Any
) -> dict[str, Any]:
    key = plan.network_key or (getattr(settings, "road_network_key", None) if settings else None)
    current = None
    if key is not None:
        current = await db.scalar(
            select(RoadNetwork.source_checksum).where(RoadNetwork.network_key == key)
        )
    matches = (
        plan.network_checksum is not None and plan.network_checksum == current
    )
    return {
        "road_network_key": key,
        "plan_checksum": plan.network_checksum,
        "current_checksum": current,
        "matches_plan": matches,
        "stale": plan.network_checksum is not None and not matches,
    }


def _warnings(plan: MissionPlan, evidence: dict[str, Any], network: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if plan.feasible is not True:
        warnings.append("Plan feasibility is not True; this plan cannot be approved.")
    if network.get("stale"):
        warnings.append(
            "The road network was re-imported after this plan was computed; "
            "approval is blocked until a new plan is produced."
        )
    if evidence.get("evidence_status") == "NO_EVIDENCE":
        warnings.append(
            "No CLEARPATH simulation evidence exists for this plan's route. "
            "Approval may still proceed, but no measured benefit was reviewed."
        )
    if evidence.get("evidence_status") == "INCOMPLETE_PAIR":
        warnings.append(
            "Only one half of the baseline/CLEARPATH pair completed, so the "
            "comparison is not meaningful."
        )
    return warnings


def _limitations(evidence: dict[str, Any]) -> list[str]:
    return [
        "CLEARPATH operates inside a SUMO/TraCI digital twin only. It never "
        "controls a real traffic signal and authorizes no physical action.",
        "Simulation metrics describe one simulated run under one seed. They are "
        "evidence, not a guarantee of real-world performance.",
        "Hospital capability and capacity records are development fixtures, not "
        "verified clinical capacity.",
        "Route response time is a modeled quantity; the optimizer's score is a "
        "selection objective and is not a calibrated probability.",
    ]
