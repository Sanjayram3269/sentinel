"""Human approval, the authorization gate, and prototype execution (Phase 8).

The single rule this module exists to enforce:

    AI proposes. The optimizer selects. CLEARPATH measures. A **human**
    authorizes. Only then may a plan become an executable state.

Everything else here follows from that. In particular:

* An approval binds to one exact ``(plan_id, plan_version)``. It never
  authorizes a later version of the same plan.
* There is no implicit approval. No code path marks a plan APPROVED without a
  ``PlanApproval`` row carrying a named reviewer and a decision.
* Simulation evidence is *shown* to the reviewer. It is never treated as
  approval, and a successful simulation cannot authorize anything by itself.
* Replanning produces a new version in ``READY_FOR_REVIEW``. It never
  auto-approves, and never touches the version it superseded.
* Execution is ``PROTOTYPE``. No real actuator is contacted, and none exists.

The gate (:func:`AuthorizationGate.evaluate`) is the only place a plan may
become ``EXECUTION_AUTHORIZED``. It is not a helper callers may skip: the
execute endpoint is its only consumer, and every failure is an explicit reason
rather than a generic 500.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AuditLog,
    Mission,
    MissionPlan,
    RoadNetwork,
    Route,
    SimulationRun,
    Vehicle,
)
from app.models.enums import ApprovalStatus, MissionStatus, PlanStatus
from app.schemas.events import EventCreate, EventType
from app.services.event_service import EventService

logger = logging.getLogger(__name__)

APPROVAL_SOURCE = "sentinel_approval_service"
EXECUTION_MODE = "PROTOTYPE"

# The prototype has no authentication system. Rather than invent one, reviewer
# identity is an explicit string supplied by the caller and recorded verbatim,
# and this constant is stored alongside it so no reader can mistake it for an
# authenticated principal.
REVIEWER_IDENTITY_KIND = "development_prototype_identity"


class ApprovalCode(str, Enum):
    """Machine-readable authorization failures. Never collapsed into a 500."""

    MISSION_NOT_FOUND = "MISSION_NOT_FOUND"
    PLAN_NOT_FOUND = "PLAN_NOT_FOUND"
    PLAN_MISSION_MISMATCH = "PLAN_MISSION_MISMATCH"
    PLAN_VERSION_MISMATCH = "PLAN_VERSION_MISMATCH"
    PLAN_NOT_REVIEWABLE = "PLAN_NOT_REVIEWABLE"
    PLAN_INFEASIBLE = "PLAN_INFEASIBLE"
    PLAN_SUPERSEDED = "PLAN_SUPERSEDED"
    ALREADY_APPROVED = "ALREADY_APPROVED"
    ALREADY_REJECTED = "ALREADY_REJECTED"
    CONFLICTING_DECISION = "CONFLICTING_DECISION"
    APPROVAL_NOT_FOUND = "APPROVAL_NOT_FOUND"
    STALE_NETWORK = "STALE_NETWORK"
    SAFETY_REJECTED = "SAFETY_REJECTED"
    ROUTE_UNAVAILABLE = "ROUTE_UNAVAILABLE"
    VEHICLE_UNAVAILABLE = "VEHICLE_UNAVAILABLE"
    RESOURCE_UNAVAILABLE = "RESOURCE_UNAVAILABLE"
    HOSPITAL_UNAVAILABLE = "HOSPITAL_UNAVAILABLE"
    EXECUTION_NOT_AUTHORIZED = "EXECUTION_NOT_AUTHORIZED"
    EXECUTION_ALREADY_STARTED = "EXECUTION_ALREADY_STARTED"
    REPLAN_NOT_REQUIRED = "REPLAN_NOT_REQUIRED"
    REPLAN_FAILED = "REPLAN_FAILED"
    MODIFICATION_NOT_ALLOWED = "MODIFICATION_NOT_ALLOWED"


_FAILURE_STATUS: dict[ApprovalCode, int] = {
    ApprovalCode.MISSION_NOT_FOUND: 404,
    ApprovalCode.PLAN_NOT_FOUND: 404,
    ApprovalCode.PLAN_MISSION_MISMATCH: 404,
    ApprovalCode.PLAN_VERSION_MISMATCH: 409,
    ApprovalCode.PLAN_NOT_REVIEWABLE: 409,
    ApprovalCode.PLAN_INFEASIBLE: 409,
    ApprovalCode.PLAN_SUPERSEDED: 409,
    ApprovalCode.ALREADY_APPROVED: 409,
    ApprovalCode.ALREADY_REJECTED: 409,
    ApprovalCode.CONFLICTING_DECISION: 409,
    ApprovalCode.APPROVAL_NOT_FOUND: 404,
    ApprovalCode.STALE_NETWORK: 409,
    ApprovalCode.SAFETY_REJECTED: 409,
    ApprovalCode.ROUTE_UNAVAILABLE: 409,
    ApprovalCode.VEHICLE_UNAVAILABLE: 409,
    ApprovalCode.RESOURCE_UNAVAILABLE: 409,
    ApprovalCode.HOSPITAL_UNAVAILABLE: 409,
    ApprovalCode.EXECUTION_NOT_AUTHORIZED: 409,
    ApprovalCode.EXECUTION_ALREADY_STARTED: 409,
    ApprovalCode.REPLAN_NOT_REQUIRED: 409,
    ApprovalCode.REPLAN_FAILED: 409,
    ApprovalCode.MODIFICATION_NOT_ALLOWED: 422,
}

# Plan states a human may be asked to decide on. DRAFT is deliberately absent:
# a plan the optimizer is still producing is not offered for review, and
# SUPERSEDED/EXECUTING/COMPLETED are decisions that have already been made.
REVIEWABLE_STATES = frozenset({PlanStatus.READY_FOR_REVIEW, PlanStatus.SUBMITTED})

# Terminal states for a decision. Once a plan reaches one, a second decision
# is either an idempotent no-op (same decision) or a conflict (different one).
DECIDED_STATES = frozenset({PlanStatus.APPROVED, PlanStatus.REJECTED})


class ExecutionDecision(str, Enum):
    AUTHORIZED = "AUTHORIZED"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"


@dataclass(frozen=True)
class GateReason:
    """One reason for an authorization outcome, with the evidence behind it."""

    code: ApprovalCode
    detail: str

    def as_payload(self) -> dict[str, str]:
        return {"code": self.code.value, "detail": self.detail}


@dataclass(frozen=True)
class GateDecision:
    """The gate's verdict. Callers may read this; only the gate may set it."""

    decision: ExecutionDecision
    reasons: tuple[GateReason, ...] = ()
    plan_id: UUID | None = None
    plan_version: int | None = None
    approval_id: UUID | None = None
    reviewer_id: str | None = None

    @property
    def authorized(self) -> bool:
        return self.decision is ExecutionDecision.AUTHORIZED

    def as_payload(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reasons": [item.as_payload() for item in self.reasons],
            "plan_id": str(self.plan_id) if self.plan_id else None,
            "plan_version": self.plan_version,
            "approval_id": str(self.approval_id) if self.approval_id else None,
            "reviewer_id": self.reviewer_id,
        }


@dataclass(frozen=True)
class ApprovalRecord:
    """A persisted decision, shaped for the API without leaking ORM state."""

    approval_id: UUID
    plan_id: UUID
    plan_version: int
    decision: ApprovalStatus
    reviewer_id: str
    reviewer_role: str | None
    comment: str | None
    previous_plan_status: str | None
    new_plan_status: str
    decided_at: datetime
    created_at: datetime
    evidence: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    created: bool = True


class ApprovalError(HTTPException):
    """An authorization failure carrying its own machine-readable code.

    ``status_code`` may override the code's default. The default answers "which
    HTTP status does this failure usually mean?"; execution needs a different
    answer, because there a missing approval is not a missing resource -- it is
    a state the request may not act from. That is 409, not 404.
    """

    def __init__(self, code: ApprovalCode, detail: str, status_code: int | None = None) -> None:
        super().__init__(
            status_code=status_code or _FAILURE_STATUS.get(code, 409),
            detail={"code": code.value, "detail": detail},
        )
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_plan_status(value: object) -> str:
    return value.value if isinstance(value, PlanStatus) else str(value)


class AuthorizationGate:
    """The one server-side gate between an approved plan and execution.

    Kept separate from the approval service so that "was this authorized?" has
    exactly one definition, independent of who is asking. The execute endpoint
    calls :meth:`evaluate` and acts on the verdict; it never re-implements a
    subset of the checks.
    """

    def __init__(self, db: AsyncSession, settings=None) -> None:
        self.db = db
        self.settings = settings

    async def evaluate(
        self, plan: MissionPlan, *, approval: Any = None
    ) -> GateDecision:
        """Decide whether ``plan`` may enter an executable state.

        ``approval`` may be passed by tests that want to exercise a specific
        decision, but the gate always re-reads the persisted approval so a
        caller cannot supply a synthetic one.
        """
        reasons: list[GateReason] = []

        current = _as_plan_status(plan.status)
        record = await self._current_approval(plan)

        if record is None:
            reasons.append(
                GateReason(
                    ApprovalCode.APPROVAL_NOT_FOUND,
                    f"Plan version {plan.version} has no human approval record",
                )
            )
        else:
            # The version binding. An approval for version 4 must not authorize
            # version 5, even though both share a plan_id.
            if record.plan_version != plan.version:
                reasons.append(
                    GateReason(
                        ApprovalCode.PLAN_VERSION_MISMATCH,
                        (
                            f"Approval is bound to version {record.plan_version} but the "
                            f"current plan is version {plan.version}"
                        ),
                    )
                )
            if record.decision is not ApprovalStatus.APPROVED:
                reasons.append(
                    GateReason(
                        ApprovalCode.SAFETY_REJECTED,
                        f"Latest human decision was {record.decision.value}, not APPROVED",
                    )
                )

        if plan.status is PlanStatus.SUPERSEDED:
            reasons.append(
                GateReason(
                    ApprovalCode.PLAN_SUPERSEDED,
                    "Plan version was superseded by a later version",
                )
            )
        elif plan.status is PlanStatus.REJECTED:
            reasons.append(
                GateReason(
                    ApprovalCode.SAFETY_REJECTED,
                    "Plan version was rejected by a reviewer",
                )
            )
        elif current not in {
            PlanStatus.APPROVED.value,
            PlanStatus.EXECUTION_AUTHORIZED.value,
        }:
            reasons.append(
                GateReason(
                    ApprovalCode.EXECUTION_NOT_AUTHORIZED,
                    f"Plan status is {current}; execution requires APPROVED",
                )
            )

        if plan.feasible is not True:
            reasons.append(
                GateReason(
                    ApprovalCode.PLAN_INFEASIBLE,
                    "Plan feasibility is not True; an infeasible plan cannot execute",
                )
            )

        reasons.extend(await self._route_reasons(plan))
        reasons.extend(await self._vehicle_reasons(plan))
        reasons.extend(await self._resource_reasons(plan))
        reasons.extend(await self._hospital_reasons(plan))
        stale = await self._network_reason(plan)
        if stale is not None:
            reasons.append(stale)

        if reasons:
            return GateDecision(
                decision=ExecutionDecision.NOT_AUTHORIZED,
                reasons=tuple(reasons),
                plan_id=plan.id,
                plan_version=plan.version,
                approval_id=record.approval_id if record else None,
                reviewer_id=record.reviewer_id if record else None,
            )

        return GateDecision(
            decision=ExecutionDecision.AUTHORIZED,
            reasons=(),
            plan_id=plan.id,
            plan_version=plan.version,
            approval_id=record.approval_id if record else None,
            reviewer_id=record.reviewer_id if record else None,
        )

    async def _current_approval(self, plan: MissionPlan) -> ApprovalRecord | None:
        """The latest human *decision* for this exact version.

        A MODIFIED row is provenance -- it records that a reviewer changed this
        version -- not a decision about whether it may run. Treating it as a
        decision would report a freshly modified plan as "safety rejected",
        which is both wrong and alarming. Only APPROVED/REJECTED count here; a
        version carrying only MODIFIED has, correctly, no approval.
        """
        row = await self.db.scalar(
            select(_approval_model())
            .where(
                _approval_model().plan_id == plan.id,
                _approval_model().plan_version == plan.version,
                _approval_model().status.in_(
                    [ApprovalStatus.APPROVED, ApprovalStatus.REJECTED]
                ),
            )
            .order_by(_approval_model().created_at.desc())
            .limit(1)
        )
        return _to_record(row) if row is not None else None

    async def _route_reasons(self, plan: MissionPlan) -> list[GateReason]:
        route_id = _selected(plan, "route_id")
        if route_id is None:
            return [
                GateReason(
                    ApprovalCode.ROUTE_UNAVAILABLE,
                    "Plan payload names no selected route",
                )
            ]
        route = await self.db.get(Route, route_id)
        if route is None:
            return [
                GateReason(
                    ApprovalCode.ROUTE_UNAVAILABLE,
                    f"Selected route {route_id} does not exist",
                )
            ]
        from app.models.route import RouteStatus

        if route.status not in {RouteStatus.ACTIVE, RouteStatus.COMPLETED}:
            return [
                GateReason(
                    ApprovalCode.ROUTE_UNAVAILABLE,
                    f"Selected route status is {route.status.value}",
                )
            ]
        return []

    async def _vehicle_reasons(self, plan: MissionPlan) -> list[GateReason]:
        route_id = _selected(plan, "route_id")
        if route_id is None:
            return []
        route = await self.db.get(Route, route_id)
        if route is None or route.vehicle_id is None:
            return []
        vehicle = await self.db.get(Vehicle, route.vehicle_id)
        if vehicle is None:
            return [
                GateReason(
                    ApprovalCode.VEHICLE_UNAVAILABLE,
                    f"Route vehicle {route.vehicle_id} does not exist",
                )
            ]
        return []

    async def _resource_reasons(self, plan: MissionPlan) -> list[GateReason]:
        selected = _selected(plan, "resource_ids") or []
        if not selected:
            return []
        from app.models.resource import Vehicle as ResourceVehicle

        found = await self.db.scalar(
            select(func.count())
            .select_from(ResourceVehicle)
            .where(ResourceVehicle.id.in_([UUID(str(item)) for item in selected]))
        )
        if int(found or 0) != len({str(item) for item in selected}):
            return [
                GateReason(
                    ApprovalCode.RESOURCE_UNAVAILABLE,
                    "One or more assigned resources no longer exist",
                )
            ]
        return []

    async def _hospital_reasons(self, plan: MissionPlan) -> list[GateReason]:
        hospital_id = _selected(plan, "hospital_id")
        if hospital_id is None:
            return []
        from app.models.resource import Hospital

        hospital = await self.db.get(Hospital, UUID(str(hospital_id)))
        if hospital is None:
            return [
                GateReason(
                    ApprovalCode.HOSPITAL_UNAVAILABLE,
                    f"Selected hospital {hospital_id} does not exist",
                )
            ]
        if hospital.operational_status is not None and not hospital.operational_status:
            return [
                GateReason(
                    ApprovalCode.HOSPITAL_UNAVAILABLE,
                    "Selected hospital is not operational",
                )
            ]
        return []

    async def _network_reason(self, plan: MissionPlan) -> GateReason | None:
        """Fail closed when the plan's network has been re-imported.

        A plan describes a journey through a specific road graph. If that graph
        was replaced after the plan was computed, approving it would authorize
        a route through a world that no longer exists.
        """
        if plan.network_checksum is None:
            return GateReason(
                ApprovalCode.STALE_NETWORK,
                "Plan records no road-network checksum, so its world cannot be verified",
            )
        key = plan.network_key or (self.settings.road_network_key if self.settings else None)
        if key is None:
            return GateReason(
                ApprovalCode.STALE_NETWORK,
                "No road network key is configured to verify the plan against",
            )
        current = await self.db.scalar(
            select(RoadNetwork.source_checksum).where(RoadNetwork.network_key == key)
        )
        if current is None:
            return GateReason(
                ApprovalCode.STALE_NETWORK,
                f"Road network {key} is not present in the database",
            )
        if current != plan.network_checksum:
            return GateReason(
                ApprovalCode.STALE_NETWORK,
                (
                    f"Road network {key} was re-imported: plan was decided against "
                    f"checksum {plan.network_checksum} but the network is now {current}"
                ),
            )
        return None


def _approval_model():
    from app.models.plan import PlanApproval

    return PlanApproval


def _to_record(row: Any) -> ApprovalRecord:
    return ApprovalRecord(
        approval_id=row.id,
        plan_id=row.plan_id,
        plan_version=int(row.plan_version),
        decision=ApprovalStatus(row.status),
        reviewer_id=row.reviewer_id or "unknown",
        reviewer_role=row.reviewer_role,
        comment=row.comment,
        previous_plan_status=row.previous_plan_status,
        new_plan_status=row.new_plan_status or _as_plan_status(row.status),
        decided_at=row.decided_at or row.created_at,
        created_at=row.created_at,
        evidence=dict(row.evidence or {}),
        context=dict(row.context or {}),
    )


def _selected(plan: MissionPlan, key: str) -> Any:
    payload = plan.plan_payload or {}
    selected = payload.get("selected") or {}
    value = selected.get(key)
    if value in (None, "", []):
        return None
    return value


def _plan_edge_ids(plan: MissionPlan) -> list[str]:
    route = (plan.plan_payload or {}).get("route") or {}
    ids = route.get("canonical_road_edge_ids") or []
    return [str(item) for item in ids]


class ApprovalService:
    """Persist human decisions and move a plan between review states."""

    def __init__(self, db: AsyncSession, event_service: EventService, settings=None) -> None:
        self.db = db
        self.event_service = event_service
        self.settings = settings

    # ------------------------------------------------------------------
    # review
    # ------------------------------------------------------------------

    async def review_package(self, db: AsyncSession, mission_id: UUID, plan_id: UUID) -> dict[str, Any]:
        """Everything a reviewer needs before deciding.

        Deliberately assembles the Phase 6 plan and the Phase 7 simulation
        evidence into one payload rather than making the caller join them: the
        reviewer should not have to decide whether to trust an optimizer score
        they cannot see the supporting measurements for.
        """
        from app.services.approval_review import build_review_package

        plan = await self._load(db, mission_id, plan_id)
        return await build_review_package(db, plan, settings=self.settings)

    # ------------------------------------------------------------------
    # decisions
    # ------------------------------------------------------------------

    async def approve(
        self,
        db: AsyncSession,
        mission_id: UUID,
        plan_id: UUID,
        *,
        plan_version: int,
        reviewer_id: str,
        comment: str | None = None,
        reviewer_role: str | None = None,
        correlation_id: UUID | None = None,
    ) -> ApprovalRecord:
        """Record an APPROVED decision for one exact plan version."""
        return await self._decide(
            db,
            mission_id,
            plan_id,
            plan_version=plan_version,
            decision=ApprovalStatus.APPROVED,
            reviewer_id=reviewer_id,
            comment=comment,
            reviewer_role=reviewer_role,
            correlation_id=correlation_id,
        )

    async def reject(
        self,
        db: AsyncSession,
        mission_id: UUID,
        plan_id: UUID,
        *,
        plan_version: int,
        reviewer_id: str,
        reason: str,
        reviewer_role: str | None = None,
        correlation_id: UUID | None = None,
    ) -> ApprovalRecord:
        """Record a REJECTED decision. A rejected plan can never execute."""
        return await self._decide(
            db,
            mission_id,
            plan_id,
            plan_version=plan_version,
            decision=ApprovalStatus.REJECTED,
            reviewer_id=reviewer_id,
            comment=reason,
            reviewer_role=reviewer_role,
            correlation_id=correlation_id,
        )

    async def _decide(
        self,
        db: AsyncSession,
        mission_id: UUID,
        plan_id: UUID,
        *,
        plan_version: int,
        decision: ApprovalStatus,
        reviewer_id: str,
        comment: str | None,
        reviewer_role: str | None,
        correlation_id: UUID | None,
    ) -> ApprovalRecord:
        plan = await self._load(db, mission_id, plan_id)

        if plan.version != plan_version:
            raise ApprovalError(
                ApprovalCode.PLAN_VERSION_MISMATCH,
                f"Decision targets version {plan_version} but the current plan is version {plan.version}",
            )

        # Idempotency is settled BEFORE the reviewable-state guard. A repeated
        # approve of an already-approved version is a replay, not an attempt to
        # decide a decided plan, so it must return the existing record rather
        # than a "not open for review" conflict.
        existing = await db.scalar(
            select(_approval_model()).where(
                _approval_model().plan_id == plan.id,
                _approval_model().plan_version == plan_version,
            )
        )
        if existing is not None:
            current = ApprovalStatus(existing.status)
            if current is decision:
                return _to_record(existing)
            raise ApprovalError(
                ApprovalCode.CONFLICTING_DECISION,
                (
                    f"Version {plan_version} already has a {current.value} decision; "
                    f"a {decision.value} decision contradicts it"
                ),
            )

        if plan.status is PlanStatus.SUPERSEDED:
            raise ApprovalError(
                ApprovalCode.PLAN_SUPERSEDED,
                "Plan version was superseded and can no longer be decided",
            )
        if plan.status not in REVIEWABLE_STATES:
            raise ApprovalError(
                ApprovalCode.PLAN_NOT_REVIEWABLE,
                f"Plan status {plan.status.value} is not open for review",
            )
        if decision is ApprovalStatus.APPROVED and plan.feasible is not True:
            raise ApprovalError(
                ApprovalCode.PLAN_INFEASIBLE,
                "An infeasible plan cannot be approved",
            )

        # The unique constraint on (plan_id, plan_version) is the real
        # concurrency guard; the read above only produces a better message.
        stale = await AuthorizationGate(db, self.settings)._network_reason(plan)
        if stale is not None and decision is ApprovalStatus.APPROVED:
            raise ApprovalError(ApprovalCode.STALE_NETWORK, stale.detail)

        previous = _as_plan_status(plan.status)
        new_status = (
            PlanStatus.APPROVED if decision is ApprovalStatus.APPROVED else PlanStatus.REJECTED
        )
        correlation = correlation_id or uuid4()
        evidence = await self._evidence_snapshot(db, plan)

        record = _approval_model()(
            plan_id=plan.id,
            plan_version=plan_version,
            status=decision,
            decision=decision.value,
            reviewer_id=reviewer_id,
            reviewer_role=reviewer_role,
            comment=comment,
            previous_plan_status=previous,
            new_plan_status=new_status.value,
            correlation_id=correlation,
            evidence=evidence,
            context={
                "reviewer_identity_kind": REVIEWER_IDENTITY_KIND,
                "mission_id": str(mission_id),
                "plan_objective": plan.objective,
                "plan_score": plan.score,
                "mission_status_at_decision": None,
            },
            decided_at=_now(),
        )
        db.add(record)
        plan.status = new_status

        mission = await db.get(Mission, mission_id)
        if mission is not None:
            record.context["mission_status_at_decision"] = mission.status.value

        await self._audit(
            db,
            mission_id=mission_id,
            action=f"PLAN_{decision.value}",
            entity_id=plan.id,
            before={"status": previous, "version": plan_version},
            after={"status": new_status.value, "version": plan_version},
            reviewer_id=reviewer_id,
        )

        try:
            await db.flush()
        except IntegrityError as error:
            # The unique constraint is the real concurrency guard: two
            # reviewers deciding the same version simultaneously cannot both
            # persist. Roll the loser back rather than leaving a half-applied
            # state for the caller.
            await db.rollback()
            raise ApprovalError(
                ApprovalCode.CONFLICTING_DECISION,
                "Another decision for this plan version was recorded concurrently",
            ) from error

        event = await self.event_service.persist(
            db,
            mission_id,
            EventCreate(
                event_type=(
                    EventType.PLAN_APPROVED
                    if decision is ApprovalStatus.APPROVED
                    else EventType.PLAN_REJECTED
                ),
                timestamp=_now(),
                source=APPROVAL_SOURCE,
                correlation_id=correlation,
                payload={
                    "plan_id": str(plan.id),
                    "plan_version": plan_version,
                    "decision": decision.value,
                    "reviewer_id": reviewer_id,
                    "reviewer_identity_kind": REVIEWER_IDENTITY_KIND,
                    "previous_plan_status": previous,
                    "new_plan_status": new_status.value,
                    "comment": comment,
                    "simulation_evidence_reviewed": evidence.get("summary"),
                },
            ),
        )
        await self._publish(event)
        await db.refresh(record)
        return _to_record(record)

    async def _evidence_snapshot(self, db: AsyncSession, plan: MissionPlan) -> dict[str, Any]:
        """Summarize what was available to review at decision time.

        This records that evidence *existed*, not that it was good. A reviewer
        may legitimately approve without simulation, so the summary reports
        presence and status only, and never an improvement figure the reviewer
        was not shown.
        """
        from app.services.approval_review import latest_simulation_evidence

        evidence = await latest_simulation_evidence(db, plan)
        return {
            "summary": evidence.get("summary"),
            "evidence_status": evidence.get("evidence_status"),
            "safety_status": evidence.get("safety_status"),
            "baseline_simulation_id": evidence.get("baseline_simulation_id"),
            "clearpath_simulation_id": evidence.get("clearpath_simulation_id"),
            "travel_time_delta_seconds": evidence.get("travel_time_delta_seconds"),
            "travel_time_improvement_percent": evidence.get(
                "travel_time_improvement_percent"
            ),
            "corridor_status": evidence.get("corridor_status"),
        }

    async def _publish(self, event: Any) -> None:
        """Publish over Redis without letting notification failure lose state.

        Persistence is authoritative: a reviewer who approved a plan has
        approved it whether or not the websocket bus was reachable.
        """
        try:
            await self.event_service.publish_persisted(event)
        except Exception:  # pragma: no cover - notification is best effort
            logger.warning(
                "Approval event %s persisted but could not be published", event.id
            )

    async def _audit(
        self,
        db: AsyncSession,
        *,
        mission_id: UUID,
        action: str,
        entity_id: UUID,
        before: dict[str, Any],
        after: dict[str, Any],
        reviewer_id: str,
    ) -> None:
        db.add(
            AuditLog(
                mission_id=mission_id,
                actor_type=REVIEWER_IDENTITY_KIND,
                actor_id=None,
                action=action,
                entity_type="mission_plan",
                entity_id=entity_id,
                before_state=before,
                after_state={**after, "reviewer_id": reviewer_id},
            )
        )

    async def _load(
        self, db: AsyncSession, mission_id: UUID, plan_id: UUID
    ) -> MissionPlan:
        mission = await db.get(Mission, mission_id)
        if mission is None:
            raise ApprovalError(ApprovalCode.MISSION_NOT_FOUND, "Mission not found")
        plan = await db.get(MissionPlan, plan_id)
        if plan is None:
            raise ApprovalError(ApprovalCode.PLAN_NOT_FOUND, "Mission plan not found")
        if plan.mission_id != mission_id:
            raise ApprovalError(
                ApprovalCode.PLAN_MISSION_MISMATCH,
                "Plan does not belong to this mission",
            )
        return plan


def _plan_status_counts() -> Sequence[tuple[str, int]]:  # pragma: no cover
    return ()


__all__ = [
    "APPROVAL_SOURCE",
    "EXECUTION_MODE",
    "REVIEWER_IDENTITY_KIND",
    "ApprovalCode",
    "ApprovalError",
    "ApprovalRecord",
    "ApprovalService",
    "AuthorizationGate",
    "ExecutionDecision",
    "GateDecision",
    "GateReason",
    "MissionStatus",
    "SimulationRun",
]
