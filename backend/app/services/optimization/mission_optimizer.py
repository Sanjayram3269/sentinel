"""The Phase 6 mission optimizer.

Joins the two halves of the decision -- hospital suitability and resource
allocation -- around the route candidates Phase 5 already produced, and persists
the result as a versioned :class:`~app.models.plan.MissionPlan`.

Flow::

    Mission + Incident
        -> available hospitals            (hard constraints)
        -> persisted route candidates     (hard constraints, Phase 5 only)
        -> hospital + route pairs scored  (soft objective)
        -> available vehicles             (hard constraints)
        -> best plan or explicit infeasible result
        -> MissionPlan + PLAN_CREATED event

Three properties are load-bearing:

* **No route is generated here.** Route candidates are read from the database.
  If a hospital has no persisted candidate reaching it, that is a rejection, not
  an invitation to call the routing provider.
* **Resource allocation is mission-scoped.** It does not vary by hospital or
  route, so it is computed once instead of per option. That is not a shortcut:
  the resources that can reach an incident do not depend on which hospital the
  incident is later taken to, so multiplying the same assignment across options
  could not change the winner.
* **Infeasible is a result, not an absence.** A mission with no feasible
  combination still produces a persisted plan with ``feasible = False``, a
  rationale, and an event, so the failure is auditable.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models import (
    Hospital,
    Incident,
    Mission,
    MissionPlan,
    RouteCandidate,
    Vehicle,
)
from app.models.enums import MissionStatus, PlanStatus
from app.schemas.events import EventCreate, EventType
from app.services.event_service import EventService
from app.services.optimization.constraints import (
    HospitalProfile,
    ResourceProfile,
    RouteProfile,
    declared_capabilities,
    declares_emergency,
)
from app.services.optimization.hospital_suitability import (
    UNAVAILABLE,
    HospitalRanking,
    HospitalSuitabilityRanker,
)
from app.services.optimization.plan_scoring import PlanOption
from app.services.optimization.resource_allocation import (
    ResourceAllocation,
    ResourceAllocator,
)

OPTIMIZER_VERSION = "mission_optimizer_1.0.0"
OPTIMIZER_SOURCE = "mission_optimizer"
#: Lower total cost wins. Spelled out so the stored objective is self-describing
#: rather than requiring the reader to reconstruct it from the weights.
OBJECTIVE = "MINIMIZE_WEIGHTED_MISSION_COST"

#: Missions that are still consuming a vehicle's time. A vehicle attached to one
#: of these is committed and is never allocated to a different mission.
ACTIVE_MISSION_STATUSES = (MissionStatus.DISPATCHED, MissionStatus.ACTIVE)


@dataclass(frozen=True)
class OptimizationRequest:
    """What the caller asks for, and nothing that was not actually stated.

    Empty requirement lists mean "no requirement was stated", not "any will do".
    The plan records which of them were absent so a reader is never left
    guessing whether a field was empty or ignored.
    """

    required_capabilities: tuple[str, ...] = ()
    required_vehicle_types: tuple[str, ...] = ()
    hospital_ids: tuple[UUID, ...] = ()
    vehicle_ids: tuple[UUID, ...] = ()
    correlation_id: UUID | None = None
    unavailable_hospital_ids: frozenset[UUID] = frozenset()
    unavailable_route_ids: frozenset[UUID] = frozenset()
    unavailable_vehicle_ids: frozenset[UUID] = frozenset()
    exclude_hospital_ids: tuple[UUID, ...] = ()

    @staticmethod
    def build(correlation_id: UUID | None = None) -> "OptimizationRequest":
        return OptimizationRequest(correlation_id=correlation_id or uuid4())


@dataclass(frozen=True)
class MissionPlanOutcome:
    """What the optimizer decided, before it is projected onto an API schema."""

    mission_id: UUID
    feasible: bool
    objective: str
    rationale: str
    payload: dict[str, Any]
    score: float | None
    plan_id: UUID | None = None
    version: int | None = None
    status: PlanStatus = PlanStatus.DRAFT
    event_id: UUID | None = None
    correlation_id: UUID | None = None
    selected_option: PlanOption | None = None
    ranking: HospitalRanking | None = None
    allocation: ResourceAllocation | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class PlanDecision:
    """The verdict, with no persistence attached."""

    feasible: bool
    blocking: tuple[str, ...]
    option: PlanOption | None


def decide_plan(
    ranking: HospitalRanking, allocation: ResourceAllocation
) -> PlanDecision:
    """Choose the winning option, or say exactly why there is none.

    Pure and synchronous: the same ranked hospitals and the same allocation
    always produce the same decision, which is what makes the determinism
    guarantee testable without a database.
    """
    blocking: list[str] = []
    if not allocation.feasible:
        blocking.extend(
            f"resource_requirement_unmet:{item.requirement}" for item in allocation.unmet
        )
    if not ranking.accepted:
        blocking.append("no_hospital_passed_hard_constraints")
    if not ranking.options:
        blocking.append("no_hospital_and_route_combination_available")
    if blocking:
        return PlanDecision(feasible=False, blocking=tuple(blocking), option=None)
    return PlanDecision(feasible=True, blocking=(), option=ranking.options[0])


class MissionOptimizer:
    """Computes, persists and explains the best plan for one mission."""

    def __init__(
        self,
        event_service: EventService,
        settings: Settings | None = None,
    ) -> None:
        self.event_service = event_service
        self.settings = settings or get_settings()
        self.ranker = HospitalSuitabilityRanker(self.settings)

    async def optimize(
        self,
        db: AsyncSession,
        mission_id: UUID,
        request: OptimizationRequest | None = None,
        *,
        persist: bool = True,
    ) -> MissionPlanOutcome:
        """Produce a mission plan, or an explicit infeasible result."""
        request = request or OptimizationRequest.build()
        # The emitted event always needs a correlation id; generate one rather
        # than passing None over EventCreate's default factory.
        correlation_id = request.correlation_id or uuid4()
        mission = await db.get(Mission, mission_id)
        if mission is None:
            raise LookupError(f"Mission {mission_id} not found")

        incident = await self._active_incident(db, mission_id)
        hospitals = await self._hospitals(db, request)
        vehicles = await self._vehicles(db, mission_id, request)
        candidates = await self._route_candidates(db, mission_id, request)

        incident_position = (
            (incident.longitude, incident.latitude) if incident is not None else None
        )

        allocator = ResourceAllocator(mission_id)
        allocation = allocator.allocate(
            vehicles,
            required_vehicle_types=request.required_vehicle_types,
            # Deliberately not ``request.required_capabilities``: that list is
            # a clinical requirement on the destination hospital (does it have
            # a trauma bay?). An ambulance does not "declare trauma", so
            # applying the same list to vehicles would reject every unit for
            # lacking a capability that was never meant for them. Resource
            # capability requirements stay supported by the allocator for
            # callers that state them explicitly.
            required_capabilities=(),
            incident_position=incident_position,
        )

        ranking = self.ranker.rank(
            hospitals,
            candidates,
            required_capabilities=request.required_capabilities,
            origin_position=incident_position,
            resource_response_meters=allocation.response_meters,
        )

        decision = decide_plan(ranking, allocation)
        feasible = decision.feasible
        blocking = list(decision.blocking)
        best = decision.option

        payload = self._payload(
            mission=mission,
            incident=incident,
            request=request,
            ranking=ranking,
            allocation=allocation,
            best=best,
            candidates=candidates,
            feasible=feasible,
            blocking=blocking,
        )
        rationale = self._rationale(
            feasible=feasible,
            blocking=blocking,
            best=best,
            ranking=ranking,
            allocation=allocation,
        )
        score = best.score.total if best is not None else None

        outcome = MissionPlanOutcome(
            mission_id=mission_id,
            feasible=feasible,
            objective=OBJECTIVE,
            rationale=rationale,
            payload=payload,
            score=score,
            correlation_id=correlation_id,
            selected_option=best if feasible else None,
            ranking=ranking,
            allocation=allocation,
        )
        if not persist:
            return outcome
        # The load queries above opened an implicit transaction. Close it so the
        # explicit block in _persist is the only one on this session.
        await db.commit()
        return await self._persist(db, outcome)

    # -- persistence ---------------------------------------------------

    async def _persist(
        self, db: AsyncSession, outcome: MissionPlanOutcome
    ) -> MissionPlanOutcome:
        async with db.begin():
            version = await self._next_version(db, outcome.mission_id)
            # Retire the previous draft so plan history keeps one current
            # candidate. Approved plans are never touched: human approval is
            # outside this phase.
            await db.execute(
                MissionPlan.__table__.update()
                .where(
                    MissionPlan.mission_id == outcome.mission_id,
                    MissionPlan.status == PlanStatus.DRAFT,
                )
                .values(status=PlanStatus.SUPERSEDED.value)
            )
            plan = MissionPlan(
                mission_id=outcome.mission_id,
                version=version,
                status=PlanStatus.DRAFT,
                objective=outcome.objective,
                plan_payload=outcome.payload,
                score=outcome.score,
                feasible=outcome.feasible,
                rationale=outcome.rationale,
            )
            db.add(plan)
            await db.flush()
            await db.refresh(plan)
            event = await self.event_service.persist(
                db,
                outcome.mission_id,
                EventCreate(
                    event_type=EventType.PLAN_CREATED,
                    timestamp=datetime.now(timezone.utc),
                    source=OPTIMIZER_SOURCE,
                    correlation_id=outcome.correlation_id,
                    payload={
                        "plan_id": str(plan.id),
                        "mission_id": str(outcome.mission_id),
                        "version": version,
                        "feasible": outcome.feasible,
                        "objective": outcome.objective,
                        "score": outcome.score,
                        "score_coverage": outcome.payload["score"]["coverage"],
                        "selected_hospital_id": outcome.payload["selected"]["hospital_id"],
                        "selected_route_id": outcome.payload["selected"]["route_id"],
                        "selected_resource_ids": outcome.payload["selected"]["resource_ids"],
                        "infeasible_reasons": outcome.payload["infeasible_reasons"],
                    },
                ),
            )
        await self.event_service.publish_persisted(event)
        return replace(
            outcome,
            plan_id=plan.id,
            version=version,
            status=plan.status,
            event_id=event.id,
            created_at=plan.created_at,
        )

    async def _next_version(self, db: AsyncSession, mission_id: UUID) -> int:
        current = await db.scalar(
            select(func.max(MissionPlan.version)).where(
                MissionPlan.mission_id == mission_id
            )
        )
        return int(current or 0) + 1

    # -- loading -------------------------------------------------------

    async def _active_incident(
        self, db: AsyncSession, mission_id: UUID
    ) -> "_IncidentPosition | None":
        row = (
            await db.execute(
                select(Incident, func.ST_Y(Incident.location), func.ST_X(Incident.location))
                .where(Incident.mission_id == mission_id)
                .order_by(
                    Incident.active.desc(), Incident.occurred_at.desc(), Incident.id.desc()
                )
                .limit(1)
            )
        ).first()
        if row is None:
            return None
        return _IncidentPosition(
            incident_id=row.Incident.id,
            incident_type=row.Incident.type.value,
            severity=row.Incident.severity,
            latitude=row[1],
            longitude=row[2],
        )

    async def _hospitals(
        self, db: AsyncSession, request: OptimizationRequest
    ) -> list[HospitalProfile]:
        statement = select(
            Hospital, func.ST_Y(Hospital.location), func.ST_X(Hospital.location)
        ).order_by(Hospital.name, Hospital.id)
        wanted = set(request.hospital_ids) - set(request.unavailable_hospital_ids)
        excluded = set(request.exclude_hospital_ids) | set(request.unavailable_hospital_ids)
        if request.hospital_ids:
            statement = statement.where(Hospital.id.in_(wanted))
        if excluded:
            statement = statement.where(Hospital.id.not_in(excluded))
        profiles: list[HospitalProfile] = []
        for row in (await db.execute(statement)).all():
            hospital = row.Hospital
            profiles.append(
                HospitalProfile(
                    hospital_id=hospital.id,
                    name=hospital.name,
                    latitude=row[1],
                    longitude=row[2],
                    capacity_total=hospital.capacity_total,
                    capacity_available=hospital.capacity_available,
                    operational_status=hospital.operational_status.value,
                    declared_capabilities=declared_capabilities(hospital.capability),
                    emergency_capable=declares_emergency(hospital.capability),
                    reliability=hospital.reliability,
                )
            )
        return profiles

    async def _vehicles(
        self, db: AsyncSession, mission_id: UUID, request: OptimizationRequest
    ) -> list[ResourceProfile]:
        statement = (
            select(
                Vehicle,
                func.ST_Y(Vehicle.current_location),
                func.ST_X(Vehicle.current_location),
            )
            .select_from(Vehicle)
            .outerjoin(Mission, Mission.id == Vehicle.mission_id)
            .where(
                or_(
                    Vehicle.mission_id.is_(None),
                    Vehicle.mission_id == mission_id,
                    ~Mission.status.in_([status.value for status in ACTIVE_MISSION_STATUSES]),
                )
            )
            .order_by(Vehicle.call_sign, Vehicle.id)
        )
        if request.vehicle_ids:
            statement = statement.where(
                Vehicle.id.in_(
                    set(request.vehicle_ids) - set(request.unavailable_vehicle_ids)
                )
            )
        if request.unavailable_vehicle_ids:
            statement = statement.where(Vehicle.id.not_in(request.unavailable_vehicle_ids))
        profiles: list[ResourceProfile] = []
        for row in (await db.execute(statement)).all():
            vehicle = row.Vehicle
            profiles.append(
                ResourceProfile(
                    vehicle_id=vehicle.id,
                    call_sign=vehicle.call_sign,
                    vehicle_type=vehicle.vehicle_type.value,
                    status=vehicle.status.value,
                    mission_id=vehicle.mission_id,
                    declared_capabilities=declared_capabilities(vehicle.capability),
                    latitude=row[1],
                    longitude=row[2],
                )
            )
        return profiles

    async def _route_candidates(
        self, db: AsyncSession, mission_id: UUID, request: OptimizationRequest
    ) -> list[RouteProfile]:
        """Read Phase 5 candidates. The only routing input the optimizer has."""
        blocked = set(request.unavailable_route_ids)
        statement = (
            select(
                RouteCandidate,
                func.ST_Y(RouteCandidate.destination),
                func.ST_X(RouteCandidate.destination),
                func.ST_Y(RouteCandidate.origin),
                func.ST_X(RouteCandidate.origin),
                func.ST_NumPoints(RouteCandidate.geometry),
            )
            .where(RouteCandidate.mission_id == mission_id)
            .order_by(RouteCandidate.route_rank, RouteCandidate.created_at, RouteCandidate.id)
        )
        if blocked:
            statement = statement.where(RouteCandidate.route_id.not_in(blocked))
        profiles: list[RouteProfile] = []
        for row in (await db.execute(statement)).all():
            candidate = row.RouteCandidate
            profiles.append(
                RouteProfile(
                    candidate_id=candidate.id,
                    route_id=candidate.route_id,
                    vehicle_id=candidate.vehicle_id,
                    planning_cycle_id=candidate.planning_cycle_id,
                    status=candidate.status.value,
                    route_rank=candidate.route_rank,
                    estimated_duration_seconds=candidate.estimated_duration_seconds,
                    distance_meters=candidate.distance_meters,
                    # ``backup_viable`` is the Phase 5 scorer's own verdict,
                    # already accounting for failure and hazard thresholds.
                    route_score_viable=bool(candidate.backup_viable),
                    resilience_role=(
                        candidate.resilience_role.value if candidate.resilience_role else None
                    ),
                    destination_latitude=row[1],
                    destination_longitude=row[2],
                    origin_latitude=row[3],
                    origin_longitude=row[4],
                    geometry_points=int(row[5] or 0),
                    risk_score=candidate.risk_score,
                    predicted_failure_probability=candidate.predicted_failure_probability,
                    road_segment_ids=tuple(candidate.road_segment_ids or ()),
                )
            )
        return profiles

    # -- explanation ---------------------------------------------------

    def _payload(
        self,
        *,
        mission: Mission,
        incident: "_IncidentPosition | None",
        request: OptimizationRequest,
        ranking: HospitalRanking,
        allocation: ResourceAllocation,
        best: PlanOption | None,
        candidates: Sequence[RouteProfile],
        feasible: bool,
        blocking: list[str],
    ) -> dict[str, Any]:
        """The persisted explanation. Every absent factor says so explicitly."""
        selected = (
            next(
                (
                    entry
                    for entry in ranking.accepted
                    if best is not None and entry.hospital_id == best.hospital_id
                ),
                None,
            )
        )
        return {
            "optimizer_version": OPTIMIZER_VERSION,
            "objective": OBJECTIVE,
            "objective_weights": {
                "eta": self.settings.optimization_weight_eta,
                "route_risk": self.settings.optimization_weight_route_risk,
                "capability": self.settings.optimization_weight_capability,
                "capacity": self.settings.optimization_weight_capacity,
                "reliability": self.settings.optimization_weight_reliability,
                "resource_proximity": self.settings.optimization_weight_resource_proximity,
            },
            "search_bounds": {
                "max_hospitals": self.settings.optimization_max_hospitals,
                "max_route_candidates_per_hospital": self.settings.optimization_max_route_candidates,
                "hospital_match_meters": self.settings.optimization_hospital_match_meters,
            },
            "requirements": {
                "required_capabilities": list(request.required_capabilities),
                "required_capabilities_scope": "destination hospital only",
                "required_vehicle_types": list(request.required_vehicle_types),
                "missing": list(allocation.missing),
                "source": "caller_supplied",
                "note": (
                    "SENTINEL's mission model carries no clinical requirement "
                    "field. An empty list means none was stated, not that the "
                    "requirement is waived. required_capabilities constrains the "
                    "hospital; resource requirements are stated separately as "
                    "required_vehicle_types."
                ),
            },
            "context": {
                "mission_id": str(mission.id),
                "mission_priority": mission.priority,
                "mission_objective": mission.objective,
                "incident_id": str(incident.incident_id) if incident else UNAVAILABLE,
                "incident_type": incident.incident_type if incident else UNAVAILABLE,
                "incident_severity": incident.severity if incident else UNAVAILABLE,
            },
            "selected": {
                "hospital_id": str(best.hospital_id) if best else None,
                "route_id": str(best.route_id) if best else None,
                "resource_ids": [str(item) for item in allocation.selected_ids],
            },
            "hospital": selected.explanation() if selected is not None else None,
            "route": self._route_explanation(best, candidates),
            "resources": self._resource_explanation(allocation),
            "hospital_candidates": [entry.explanation() for entry in ranking.accepted],
            "hospital_rejected": ranking.rejected_explanations(),
            "route_rejected": [
                {
                    "candidate_id": str(item.candidate_id),
                    "route_id": str(item.route_id) if item.route_id else UNAVAILABLE,
                    "rejections": list(item.reasons),
                }
                for item in ranking.route_rejections
            ],
            "options_considered": len(ranking.options),
            "score": self._score_explanation(best),
            "feasible": feasible,
            "infeasible_reasons": blocking,
            "hard_constraints_applied": [
                "hospital_capability_match",
                "hospital_capacity_available",
                "hospital_operational_status",
                "route_status_viability",
                "route_scorer_viability",
                "resource_available",
                "resource_single_mission_commitment",
                "resource_type_match",
                "resource_capability_match",
            ],
        }

    def _route_explanation(
        self,
        best: PlanOption | None,
        candidates: Sequence[RouteProfile] = (),
    ) -> dict[str, Any] | None:
        if best is None:
            return None
        route = best.route
        diversity = _diversity_from_primary(route, candidates)
        return {
            "route_id": str(route.route_id),
            "candidate_id": str(route.candidate_id),
            "vehicle_id": str(route.vehicle_id),
            "resilience_role": route.resilience_role or UNAVAILABLE,
            "status": route.status,
            "eta_seconds": route.estimated_duration_seconds,
            "distance_meters": round(route.distance_meters, 2),
            "failure_risk": (
                round(best.factors.route_risk, 4)
                if best.factors.route_risk is not None
                else UNAVAILABLE
            ),
            # How distinct this route is from the Phase 5 primary, using the
            # resilience engine's own overlap definition. The primary itself has
            # no comparison route, and a route with no canonical edge ids cannot
            # be compared at all -- both report "unavailable" rather than 0.0.
            "diversity_from_primary": (
                round(diversity, 4) if diversity is not None else UNAVAILABLE
            ),
            "route_rank": route.route_rank,
            # Canonical RoadEdge.id values, copied verbatim from the Phase 5
            # candidate. A SUMO external_id never appears here.
            "canonical_road_edge_count": len(route.road_segment_ids),
            "canonical_road_edge_ids": list(route.road_segment_ids),
        }

    def _resource_explanation(self, allocation: ResourceAllocation) -> dict[str, Any]:
        return {
            "requirements_declared": allocation.requirements_declared,
            "feasible": allocation.feasible,
            "selected": [
                {
                    "requirement": item.requirement,
                    "vehicle_id": str(item.vehicle_id),
                    "call_sign": item.resource.call_sign,
                    "vehicle_type": item.resource.vehicle_type,
                    "status": item.resource.status,
                    "straight_line_response_meters": (
                        round(item.response_meters, 1)
                        if item.response_meters is not None
                        else UNAVAILABLE
                    ),
                    "reasons": list(item.reasons),
                }
                for item in allocation.selections
            ],
            "rejected": [
                {
                    "vehicle_id": str(item.resource.vehicle_id),
                    "call_sign": item.resource.call_sign,
                    "vehicle_type": item.resource.vehicle_type,
                    "status": item.resource.status,
                    "reasons": list(item.reasons),
                }
                for item in allocation.rejections
            ],
            "unmet": [
                {"requirement": item.requirement, "reason": item.reason, "considered": list(item.considered)}
                for item in allocation.unmet
            ],
            "worst_response_meters": (
                round(allocation.response_meters, 1)
                if allocation.response_meters is not None
                else UNAVAILABLE
            ),
        }

    def _score_explanation(self, best: PlanOption | None) -> dict[str, Any]:
        if best is None:
            return {
                "total": UNAVAILABLE,
                "coverage": 0.0,
                "contributions": {},
                "unavailable_factors": [],
                "costs": {},
                "note": "No feasible hospital and route combination was available to score.",
            }
        costs = best.score.costs
        return {
            "total": round(best.score.total, 6) if best.score.total is not None else UNAVAILABLE,
            "coverage": round(best.score.coverage, 4),
            "contributions": {
                name: round(value, 6) for name, value in sorted(best.score.contributions.items())
            },
            "unavailable_factors": list(best.score.unavailable_factors),
            "costs": {
                name: (round(value, 6) if value is not None else UNAVAILABLE)
                for name, value in costs.items()
            },
            "note": (
                "Costs are normalised within this option set; 1.0 is worst. "
                "Factors with no data are listed in unavailable_factors and are "
                "excluded from the weighted mean."
            ),
        }

    def _rationale(
        self,
        *,
        feasible: bool,
        blocking: list[str],
        best: PlanOption | None,
        ranking: HospitalRanking,
        allocation: ResourceAllocation,
    ) -> str:
        """A human-readable answer to 'why this plan?'."""
        lines: list[str] = []
        if not feasible:
            lines.append(
                "Mission plan is INFEASIBLE: "
                + ("; ".join(blocking) if blocking else "no feasible combination")
                + "."
            )
            if allocation.unmet:
                lines.append(
                    "Unmet resource requirements: "
                    + ", ".join(item.requirement for item in allocation.unmet)
                    + ". No resource was downgraded to fit."
                )
            if ranking.rejected:
                lines.append(
                    f"{len(ranking.rejected)} hospital(s) rejected by hard constraints: "
                    + ", ".join(
                        f"{entry.hospital.name} [{','.join(entry.rejections)}]"
                        for entry in ranking.rejected[:5]
                    )
                    + "."
                )
            return "\n".join(lines)

        assert best is not None
        hospital = best.hospital
        route = best.route
        lines.append(
            f"Selected hospital {hospital.name} via {route.resilience_role or 'UNASSIGNED'} "
            f"route {route.route_id} ({route.estimated_duration_seconds} s, "
            f"{route.distance_meters:.0f} m)."
        )
        reasons: list[str] = []
        reasons.append(
            f"capability_match={not best.capability.missing} "
            f"(required {list(best.capability.required) or 'none stated'}, "
            f"matched {list(best.capability.matched) or 'none'})"
        )
        reasons.append(
            f"capacity_available={hospital.capacity_available}/{hospital.capacity_total}"
        )
        reasons.append(
            "reliability="
            + (
                f"{hospital.reliability:.4f}"
                if hospital.reliability is not None
                else UNAVAILABLE
            )
        )
        reasons.append(
            f"route_failure_risk="
            + (
                f"{best.factors.route_risk:.4f}"
                if best.factors.route_risk is not None
                else UNAVAILABLE
            )
        )
        reasons.append(
            "plan_cost="
            + (
                f"{best.score.total:.6f}"
                if best.score.total is not None
                else UNAVAILABLE
            )
            + f" (coverage {best.score.coverage:.2f})"
        )
        if best.score.unavailable_factors:
            reasons.append(
                "unavailable_factors=" + ",".join(sorted(best.score.unavailable_factors))
            )
        lines.append("Why this hospital: " + "; ".join(reasons) + ".")

        others = [
            entry
            for entry in ranking.accepted
            if entry.hospital.hospital_id != hospital.hospital_id
        ]
        if others:
            lines.append(
                "Alternatives rejected by score, not by constraint: "
                + ", ".join(
                    f"{entry.hospital.name} (best cost "
                    + (
                        f"{entry.options[0].score.total:.6f}"
                        if entry.options and entry.options[0].score.total is not None
                        else UNAVAILABLE
                    )
                    + ")"
                    for entry in others
                )
                + "."
            )
        blocked = [
            entry
            for entry in ranking.rejected
            if entry.rejections != ("hospital_no_viable_route",)
        ]
        if blocked:
            lines.append(
                "Rejected by hard constraints: "
                + ", ".join(
                    f"{entry.hospital.name} [{','.join(entry.rejections)}]" for entry in blocked
                )
                + "."
            )
        if allocation.selections:
            lines.append(
                "Resources: "
                + ", ".join(
                    f"{item.resource.call_sign} -> {item.requirement}"
                    for item in allocation.selections
                )
                + "."
            )
        else:
            lines.append(
                "Resources: none allocated; "
                + (
                    "no resource requirement was stated for this mission."
                    if not allocation.requirements_declared
                    else "every candidate was rejected."
                )
            )
        if allocation.rejections:
            lines.append(
                "Rejected resources: "
                + ", ".join(
                    f"{item.resource.call_sign} ({','.join(item.reasons)})"
                    for item in allocation.rejections[:6]
                )
                + "."
            )
        return "\n".join(lines)


@dataclass(frozen=True)
class _IncidentPosition:
    incident_id: UUID
    incident_type: str
    severity: int
    latitude: float
    longitude: float


def _diversity_from_primary(
    route: RouteProfile, candidates: Sequence[RouteProfile]
) -> float | None:
    """How distinct a route is from the Phase 5 primary, or None.

    Uses exactly the id-based branch of the Phase 5 resilience engine --
    ``|A & B| / min(|A|, |B|)`` over canonical ``RoadEdge.id`` sets -- so the
    number reported in a plan and the number the resilience engine uses to
    appoint a backup can never drift apart.

    Two cases return ``None`` rather than a number: the selected route *is* the
    primary (it has nothing to be distinct from), and either route has no
    canonical edge ids to compare. The caller renders that as "unavailable";
    reporting ``0.0`` would read as "no diversity", which is a claim the data
    does not support.
    """
    primary = next(
        (item for item in candidates if item.resilience_role == "PRIMARY"), None
    )
    if primary is None or primary.candidate_id == route.candidate_id:
        return None
    first, second = set(route.road_segment_ids), set(primary.road_segment_ids)
    if not first or not second:
        return None
    overlap = len(first & second) / min(len(first), len(second))
    return max(0.0, min(1.0, 1.0 - overlap))
