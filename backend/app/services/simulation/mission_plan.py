"""Mission-plan to CLEARPATH simulation bridge (Phase 7).

This module is the seam between the Phase 6 decision layer and the existing
digital twin. It **connects** them; it does not replace any part of either.

What it is responsible for:

* proving the selected mission plan is internally consistent and owned by the
  mission that is asking to be simulated;
* translating canonical ``RoadEdge.id`` values into SUMO bridge identifiers
  *in route order*, and refusing to continue if any edge is unmapped;
* deriving the CLEARPATH corridor from the selected route rather than from a
  hardcoded corridor;
* handing a ``SimulationRequest`` to the existing ``SimulationService`` and
  packaging its measured comparison as simulation evidence.

What it deliberately does not do:

* It never generates or re-scores a route. Phase 5 owns routing.
* It never alters the plan, its score, its version, or mission state. Simulation
  is evidence; replanning is Phase 8.
* It never writes a signal phase. Every action still goes through the existing
  ``ClearPathSafetyGuard`` inside ``SimulationRunner``.

Identity boundary
-----------------
``RoadEdge.id`` is the canonical SENTINEL identifier and is what a plan stores.
``RoadEdge.external_id`` is the SUMO bridge identifier and exists only inside
this translation and inside the simulator's own contract. A SUMO edge id is
never written back onto canonical route persistence.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models import Mission, MissionPlan, RoadEdge, RoadNetwork, Route, RouteCandidate, Vehicle
from app.models.enums import RouteStatus, SimulationStatus
from app.schemas.simulation import SafetyDecision, SimulationRequest
from app.schemas.simulation_evidence import (
    CorridorRead,
    CorridorSignalRead,
    CorridorStatus,
    EvidenceStatus,
    PlanSimulationCode,
    PlanSimulateRequest,
    SafetyStatus,
    SimulationComparisonEvidence,
    SimulationEvidenceRead,
    SimulationRunEvidence,
)
from app.services.simulation.service import SimulationRunRead, SimulationService

logger = logging.getLogger(__name__)

#: Status codes for validation failures. A meaningful failure must never become
#: a generic 500, so each code maps to the closest honest HTTP status.
_FAILURE_STATUS: dict[PlanSimulationCode, int] = {
    PlanSimulationCode.MISSION_NOT_FOUND: 404,
    PlanSimulationCode.PLAN_NOT_FOUND: 404,
    # A plan belonging to another mission is reported as "not found" for that
    # mission rather than confirmed, so the endpoint cannot enumerate plans.
    PlanSimulationCode.PLAN_MISSION_MISMATCH: 404,
    PlanSimulationCode.PLAN_HAS_NO_SELECTED_ROUTE: 409,
    PlanSimulationCode.ROUTE_NOT_FOUND: 404,
    PlanSimulationCode.ROUTE_NOT_ACTIVE: 409,
    PlanSimulationCode.ROUTE_VEHICLE_MISMATCH: 409,
    PlanSimulationCode.INVALID_ROUTE: 422,
    PlanSimulationCode.ROUTE_NETWORK_MISMATCH: 422,
    PlanSimulationCode.MISSING_SUMO_EDGE_MAPPING: 422,
    PlanSimulationCode.SIMULATION_CONFIGURATION_ERROR: 422,
    PlanSimulationCode.SUMO_UNAVAILABLE: 503,
}


class PlanSimulationError(HTTPException):
    """A validation failure carrying its machine-readable code."""

    def __init__(self, code: PlanSimulationCode, detail: str) -> None:
        super().__init__(
            status_code=_FAILURE_STATUS.get(code, 422),
            detail={"code": code.value, "message": detail},
        )
        self.code = code


def translate_route_to_sumo_edges(
    canonical_edge_ids: Sequence[str],
    external_by_canonical: Mapping[str, str],
) -> list[str]:
    """Map canonical ``RoadEdge.id`` values to SUMO edge ids, preserving order.

    Route ordering is traffic-relevant: reversing or reordering the edge
    sequence would simulate a different journey than the one that was planned.
    The output therefore follows the input order exactly.

    Fails closed on the first unmapped edge rather than dropping it, because a
    silently shortened edge list would produce a *measured* result for a route
    the optimizer never chose.
    """
    if not canonical_edge_ids:
        raise PlanSimulationError(
            PlanSimulationCode.INVALID_ROUTE,
            "Selected route carries no canonical RoadEdge ids",
        )
    translated: list[str] = []
    for canonical_id in canonical_edge_ids:
        external_id = external_by_canonical.get(str(canonical_id))
        if not external_id:
            raise PlanSimulationError(
                PlanSimulationCode.MISSING_SUMO_EDGE_MAPPING,
                (
                    f"Canonical road edge {canonical_id} has no simulator edge "
                    "mapping in the selected road network"
                ),
            )
        translated.append(external_id)
    return translated


class MissionPlanSimulationService:
    """Validate a selected mission plan against the CLEARPATH digital twin."""

    def __init__(
        self,
        simulation_service: SimulationService,
        settings: Settings | None = None,
    ) -> None:
        self.simulation_service = simulation_service
        self.settings = settings or get_settings()

    async def validate_plan(
        self,
        db: AsyncSession,
        mission_id: UUID,
        plan_id: UUID,
        request: PlanSimulateRequest | None = None,
    ) -> SimulationEvidenceRead:
        """Run the comparable baseline/CLEARPATH pair for a selected plan.

        The plan is read-only here. Nothing in this method writes to
        ``MissionPlan``, ``Route``, ``Vehicle`` or ``Mission``.
        """
        request = request or PlanSimulateRequest()

        plan, route, vehicle = await self._load_and_validate(db, mission_id, plan_id)
        canonical_ids, network_key = await self._resolve_canonical_route(db, plan, route)
        sumo_edges = await self._translate_edges(db, canonical_ids)
        corridor_signals = await self._derive_corridor(db, sumo_edges)

        simulation_request = SimulationRequest(
            vehicle_id=vehicle.id,
            route_id=route.id,
            network_id=self._network_id(),
            route_edge_ids=sumo_edges,
            traffic_signal_ids=[
                str(item.traffic_signal_id) for item in corridor_signals
            ],
            emergency_vehicle_configuration=request.emergency_vehicle_configuration,
            seed=request.seed,
            max_simulation_seconds=request.max_simulation_seconds,
            correlation_id=request.correlation_id,
        )

        # The existing service owns execution, persistence and events. Phase 7
        # supplies the scenario and reads the measured result back.
        comparison = await self.simulation_service.compare(
            db, mission_id, simulation_request
        )

        corridor_read = CorridorRead(
            status=CorridorStatus.NO_ELIGIBLE if not corridor_signals else CorridorStatus.ELIGIBLE,
            signals=corridor_signals,
            canonical_road_edge_count=len(canonical_ids),
            sumo_edge_count=len(sumo_edges),
            note=(
                "Corridor signals are derived from the selected route's edge "
                "sequence. A signal off this corridor can never be proposed."
                if corridor_signals
                else (
                    "No CLEARPATH-capable signal intersects the selected route. "
                    "Both runs still execute, and no intervention is fabricated."
                )
            ),
        )

        evidence_status = _evidence_status(comparison.baseline, comparison.clearpath)
        approved = sum(
            1
            for action in comparison.clearpath.clearpath_actions
            if action.decision is SafetyDecision.APPROVED
        )
        released = sum(
            1
            for action in comparison.clearpath.clearpath_actions
            if action.released_at_seconds is not None
        )

        return SimulationEvidenceRead(
            simulation_pair_id=comparison.correlation_id,
            mission_id=mission_id,
            plan_id=plan.id,
            route_id=route.id,
            vehicle_id=vehicle.id,
            plan_version=plan.version,
            baseline=_run_evidence(comparison.baseline),
            clearpath=_run_evidence(comparison.clearpath),
            comparison=SimulationComparisonEvidence(
                travel_time_delta_seconds=comparison.comparison.travel_time_delta_seconds,
                travel_time_improvement_percent=comparison.comparison.travel_time_improvement_percent,
                stopped_time_delta_seconds=comparison.comparison.stopped_time_delta_seconds,
                stops_delta=comparison.comparison.stops_delta,
            ),
            evidence_status=evidence_status,
            safety_status=_safety_status(approved, len(comparison.clearpath.clearpath_actions)),
            corridor=corridor_read,
            actions_requested=len(comparison.clearpath.clearpath_actions),
            actions_approved=approved,
            actions_executed=released,
            actions_rejected=len(comparison.clearpath.clearpath_actions) - approved,
            seed=request.seed,
            max_simulation_seconds=request.max_simulation_seconds,
            network={
                "road_network_key": network_key,
                "simulation_network_id": simulation_request.network_id,
                "canonical_edge_count": len(canonical_ids),
                "sumo_edge_count": len(sumo_edges),
                "identity_note": (
                    "Canonical identity is RoadEdge.id. SUMO external ids are a "
                    "simulation bridge only and are never persisted onto a route."
                ),
            },
            generated_at=datetime.now(timezone.utc),
        )

    # -- validation ----------------------------------------------------

    async def _load_and_validate(
        self, db: AsyncSession, mission_id: UUID, plan_id: UUID
    ) -> tuple[MissionPlan, Route, Vehicle]:
        mission = await db.get(Mission, mission_id)
        if mission is None:
            raise PlanSimulationError(
                PlanSimulationCode.MISSION_NOT_FOUND, "Mission not found"
            )
        plan = await db.get(MissionPlan, plan_id)
        if plan is None:
            raise PlanSimulationError(
                PlanSimulationCode.PLAN_NOT_FOUND, "Mission plan not found"
            )
        if plan.mission_id != mission_id:
            raise PlanSimulationError(
                PlanSimulationCode.PLAN_MISSION_MISMATCH,
                "Mission plan does not belong to this mission",
            )
        if not plan.feasible:
            raise PlanSimulationError(
                PlanSimulationCode.PLAN_HAS_NO_SELECTED_ROUTE,
                "Mission plan is not feasible and has no selected route to simulate",
            )
        payload = plan.plan_payload if isinstance(plan.plan_payload, dict) else {}
        selected = payload.get("selected") if isinstance(payload.get("selected"), dict) else {}
        route_id = _as_uuid(selected.get("route_id"))
        if route_id is None:
            raise PlanSimulationError(
                PlanSimulationCode.PLAN_HAS_NO_SELECTED_ROUTE,
                "Mission plan has no selected route",
            )

        route = await db.scalar(
            select(Route).where(Route.id == route_id, Route.mission_id == mission_id)
        )
        if route is None:
            raise PlanSimulationError(
                PlanSimulationCode.ROUTE_NOT_FOUND,
                "Selected route is not part of this mission",
            )
        if route.status is not RouteStatus.ACTIVE:
            # Phase 5 owns activation. Phase 7 never activates a route itself.
            raise PlanSimulationError(
                PlanSimulationCode.ROUTE_NOT_ACTIVE,
                f"Selected route is {route.status.value}; an ACTIVE route is required",
            )
        vehicle = await db.get(Vehicle, route.vehicle_id)
        if vehicle is None:
            raise PlanSimulationError(
                PlanSimulationCode.ROUTE_VEHICLE_MISMATCH,
                "Selected route references a vehicle that no longer exists",
            )
        if vehicle.mission_id != mission_id:
            raise PlanSimulationError(
                PlanSimulationCode.ROUTE_VEHICLE_MISMATCH,
                "Mission does not own the selected route's vehicle",
            )
        return plan, route, vehicle

    async def _resolve_canonical_route(
        self, db: AsyncSession, plan: MissionPlan, route: Route
    ) -> tuple[list[str], str]:
        """Read the canonical edge sequence, cross-checked against Phase 5.

        The plan payload and the persisted ``RouteCandidate`` must agree. A
        disagreement means the plan and the routing record have diverged, and
        simulating either one alone would be measuring something other than the
        decision that was actually made.
        """
        candidate = await db.scalar(
            select(RouteCandidate).where(RouteCandidate.route_id == route.id)
        )
        if candidate is None or not candidate.road_segment_ids:
            raise PlanSimulationError(
                PlanSimulationCode.INVALID_ROUTE,
                "Selected route has no persisted canonical road edge sequence",
            )
        canonical_ids = [str(item) for item in candidate.road_segment_ids]

        plan_ids = _plan_edge_ids(plan)
        if plan_ids is not None and plan_ids != canonical_ids:
            raise PlanSimulationError(
                PlanSimulationCode.INVALID_ROUTE,
                "Plan payload and route candidate disagree on the canonical edge sequence",
            )
        if len(set(canonical_ids)) != len(canonical_ids):
            raise PlanSimulationError(
                PlanSimulationCode.INVALID_ROUTE,
                "Canonical route contains duplicate road edges",
            )
        return canonical_ids, self.settings.road_network_key

    def _mission_of(self, db: AsyncSession, route_id: UUID):  # pragma: no cover
        raise NotImplementedError

    async def _translate_edges(
        self, db: AsyncSession, canonical_ids: Sequence[str]
    ) -> list[str]:
        rows = (
            await db.execute(
                select(RoadEdge.id, RoadEdge.external_id)
                .join(RoadNetwork, RoadNetwork.id == RoadEdge.network_id)
                .where(
                    RoadNetwork.network_key == self.settings.road_network_key,
                    RoadEdge.id.in_([UUID(str(item)) for item in canonical_ids]),
                )
            )
        ).all()
        external_by_canonical = {str(row[0]): row[1] for row in rows}
        return translate_route_to_sumo_edges(canonical_ids, external_by_canonical)

    async def _derive_corridor(
        self, db: AsyncSession, sumo_edges: Sequence[str]
    ) -> list[CorridorSignalRead]:
        """Signals whose mapped edge lies on the selected route.

        Derivation, not configuration: the corridor cannot be widened by a
        caller, and a signal on an edge the route never uses is excluded even if
        it is enabled and CLEARPATH-capable.
        """
        from app.models import TrafficSignal

        if not sumo_edges:
            return []
        rows = (
            await db.scalars(
                select(TrafficSignal)
                .where(
                    TrafficSignal.enabled.is_(True),
                    TrafficSignal.signal_metadata["edge_id"].astext.in_(list(sumo_edges)),
                )
                .order_by(TrafficSignal.id)
            )
        ).all()
        corridor: list[CorridorSignalRead] = []
        for signal in rows:
            metadata = signal.signal_metadata or {}
            signal_id = metadata.get("sumo_signal_id")
            if not signal_id:
                logger.warning(
                    "Traffic signal %s on corridor has no sumo_signal_id and was skipped",
                    signal.id,
                )
                continue
            corridor.append(
                CorridorSignalRead(
                    traffic_signal_id=signal.id,
                    signal_id=str(signal_id),
                    sumo_edge_id=str(metadata["edge_id"]),
                )
            )
        return corridor

    def _network_id(self) -> str:
        if self.settings.sumo_network_id:
            return self.settings.sumo_network_id
        if self.settings.simulation_development_network_id:
            return self.settings.simulation_development_network_id
        raise PlanSimulationError(
            PlanSimulationCode.SIMULATION_CONFIGURATION_ERROR,
            "No simulation network is configured",
        )

def _plan_edge_ids(plan: MissionPlan) -> list[str] | None:
    """Canonical edge sequence recorded in the Phase 6 plan payload, if present.

    Returns ``None`` when the payload predates the field rather than treating
    its absence as agreement; the persisted candidate remains authoritative in
    that case, and a *present but different* sequence is a hard failure.
    """
    payload = plan.plan_payload if isinstance(plan.plan_payload, dict) else {}
    route = payload.get("route") if isinstance(payload.get("route"), dict) else {}
    ids = route.get("canonical_road_edge_ids")
    if not isinstance(ids, list) or not ids:
        return None
    return [str(item) for item in ids]


def _as_uuid(value: object) -> UUID | None:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _run_evidence(run: SimulationRunRead) -> SimulationRunEvidence:
    metadata = run.metadata if isinstance(run.metadata, dict) else {}
    return SimulationRunEvidence(
        simulation_id=run.simulation_id,
        mode=run.mode,
        status=run.status,
        simulator=run.simulator,
        network_id=run.network_id,
        metrics=run.metrics,
        sumo_version=metadata.get("sumo_version"),
        error_code=run.error_code,
        error_message=run.error_message,
        started_at=run.started_at,
        completed_at=run.completed_at,
    )


def _evidence_status(
    baseline: SimulationRunRead, clearpath: SimulationRunRead
) -> EvidenceStatus:
    if baseline.status is not SimulationStatus.COMPLETED:
        return EvidenceStatus.BASELINE_FAILED
    if clearpath.status is not SimulationStatus.COMPLETED:
        return EvidenceStatus.CLEARPATH_FAILED
    if baseline.metrics is None or clearpath.metrics is None:
        return EvidenceStatus.COMPARISON_UNAVAILABLE
    return EvidenceStatus.COMPARABLE


def _safety_status(approved: int, requested: int) -> SafetyStatus:
    if requested == 0:
        return SafetyStatus.NO_ACTION_PROPOSED
    if approved == 0:
        return SafetyStatus.REJECTED_SIMULATION_ONLY
    return SafetyStatus.APPROVED_SIMULATION_ONLY
