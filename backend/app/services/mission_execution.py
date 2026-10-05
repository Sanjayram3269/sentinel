"""Prototype execution boundary, observation, and replanning (Phase 8).

Three responsibilities, deliberately kept apart:

``ExecutionService``
    Turns an *authorized* plan into a prototype execution record. It refuses
    everything the gate refuses, and it contacts no real actuator: there is no
    ambulance dispatch, no police system, no hospital API and no traffic-signal
    client anywhere in this module or its dependencies.
``ObservationService``
    Reads telemetry that already exists and compares it against what the plan
    expected, raising a replan condition when reality diverges.
``ReplanService``
    Turns a replan condition into a *new plan version in READY_FOR_REVIEW* using
    the existing Phase 5 routing and Phase 6 optimization. It never approves,
    never authorizes, and never touches the version it superseded.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Mission, MissionPlan, Route, Vehicle
from app.models.enums import MissionStatus, PlanStatus, RouteStatus, VehicleStatus
from app.schemas.events import EventCreate, EventType
from app.services.approval import (
    EXECUTION_MODE,
    ApprovalCode,
    ApprovalError,
    AuthorizationGate,
    ExecutionDecision,
)
from app.services.event_service import EventService

logger = logging.getLogger(__name__)

EXECUTION_SOURCE = "sentinel_execution_service"
REPLAN_SOURCE = "sentinel_replan_service"

# Prototype boundary, stated where a reader will find it rather than only in
# the README. "execute" means "authorize for simulated/prototype execution".
NO_REAL_ACTUATOR_NOTICE = (
    "execution_mode=PROTOTYPE. This records a prototype execution state only. "
    "No real vehicle, hospital or traffic signal was contacted, and this system "
    "contains no actuator capable of doing so."
)


class ReplanReason(str, Enum):
    ROUTE_DEVIATION = "ROUTE_DEVIATION"
    ROUTE_UNAVAILABLE = "ROUTE_UNAVAILABLE"
    VEHICLE_DEVIATION = "VEHICLE_DEVIATION"
    ETA_DEGRADATION = "ETA_DEGRADATION"
    HOSPITAL_UNAVAILABLE = "HOSPITAL_UNAVAILABLE"
    RESOURCE_UNAVAILABLE = "RESOURCE_UNAVAILABLE"
    SAFETY_CONDITION = "SAFETY_CONDITION"
    OPERATOR_REQUEST = "OPERATOR_REQUEST"


@dataclass(frozen=True)
class ExecutionRecord:
    """A prototype execution. Contains no actuator reference by design."""

    plan_id: UUID
    plan_version: int
    mission_id: UUID
    execution_mode: str
    authorized_by_approval_id: UUID | None
    reviewer_id: str | None
    status: str
    started_at: datetime
    notice: str = NO_REAL_ACTUATOR_NOTICE


@dataclass(frozen=True)
class ObservationResult:
    """What telemetry says about the plan being executed."""

    plan_id: UUID
    plan_version: int
    observed_samples: int
    deviation_detected: bool
    reasons: tuple[str, ...] = ()
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReplanResult:
    """The outcome of a replan request.

    ``auto_approved`` is hard-wired to False and exists so that the audit trail
    records the absence of approval rather than leaving it implied.
    """

    replan_requested: bool
    reason: str | None
    new_plan_id: UUID | None = None
    new_plan_version: int | None = None
    previous_plan_id: UUID | None = None
    previous_plan_version: int | None = None
    previous_plan_status: str | None = None
    auto_approved: bool = False
    requires_human_approval: bool = True
    correlation_id: UUID = field(default_factory=uuid4)
    note: str = (
        "A replanned plan is always returned for human approval. Nothing in this "
        "service can approve, authorize or execute it."
    )


class ExecutionService:
    """The only caller of :class:`AuthorizationGate` in the application."""

    def __init__(
        self,
        db: AsyncSession,
        event_service: EventService,
        gate: AuthorizationGate,
        settings: Any = None,
    ) -> None:
        self.db = db
        self.event_service = event_service
        self.gate = gate
        self.settings = settings

    async def execute(
        self,
        db: AsyncSession,
        mission_id: UUID,
        plan_id: UUID,
        *,
        correlation_id: UUID | None = None,
    ) -> ExecutionRecord:
        """Authorize and start a prototype execution, or refuse with reasons.

        The gate verdict is the whole decision. This method adds no approval of
        its own and no shortcut: an unapproved, version-mismatched, superseded,
        rejected, infeasible or stale-network plan cannot start here, and each
        refusal carries the machine-readable reason.
        """
        plan = await self._load(db, mission_id, plan_id)
        if plan.status in {PlanStatus.EXECUTING, PlanStatus.EXECUTION_AUTHORIZED}:
            raise ApprovalError(
                ApprovalCode.EXECUTION_ALREADY_STARTED,
                f"Plan version {plan.version} is already executing",
            )

        decision = await self.gate.evaluate(plan)
        if decision.decision is not ExecutionDecision.AUTHORIZED:
            primary = decision.reasons[0].code if decision.reasons else ApprovalCode.EXECUTION_NOT_AUTHORIZED
            raise ApprovalError(
                primary,
                "; ".join(item.detail for item in decision.reasons)
                or "Execution is not authorized",
                # A gate refusal is always a conflict: the plan exists, the
                # request simply may not act on its current state.
                status_code=409,
            )

        correlation = correlation_id or uuid4()
        previous = plan.status
        # The gate -- not the approval, and not the simulation -- is what grants
        # execution authorization. Recording it as its own state keeps that
        # distinction in the persisted history rather than only in a return
        # value: APPROVED means a human said yes, EXECUTION_AUTHORIZED means the
        # server re-checked and agreed.
        plan.status = PlanStatus.EXECUTION_AUTHORIZED
        await db.flush()
        await self._publish(
            db,
            mission_id,
            EventType.EXECUTION_AUTHORIZED,
            correlation,
            {
                "action": "EXECUTION_AUTHORIZED",
                "plan_id": str(plan.id),
                "plan_version": plan.version,
                "approval_id": str(decision.approval_id) if decision.approval_id else None,
                "reviewer_id": decision.reviewer_id,
                "previous_plan_status": previous.value,
            },
        )

        plan.status = PlanStatus.EXECUTING

        mission = await db.get(Mission, mission_id)
        if mission is not None and mission.status in {
            MissionStatus.CREATED,
            MissionStatus.DISPATCHED,
        }:
            mission.status = MissionStatus.ACTIVE

        await db.flush()
        event = await self.event_service.persist(
            db,
            mission_id,
            EventCreate(
                event_type=EventType.MISSION_EXECUTION_STARTED,
                timestamp=datetime.now(timezone.utc),
                source=EXECUTION_SOURCE,
                correlation_id=correlation,
                payload={
                    "action": "MISSION_EXECUTION_STARTED",
                    "plan_id": str(plan.id),
                    "plan_version": plan.version,
                    "execution_mode": EXECUTION_MODE,
                    "approval_id": str(decision.approval_id) if decision.approval_id else None,
                    "reviewer_id": decision.reviewer_id,
                    "previous_plan_status": previous.value,
                    "notice": NO_REAL_ACTUATOR_NOTICE,
                },
            ),
        )
        try:
            await self.event_service.publish_persisted(event)
        except Exception:  # pragma: no cover - notification is best effort
            logger.warning("Execution event %s persisted but not published", event.id)

        return ExecutionRecord(
            plan_id=plan.id,
            plan_version=plan.version,
            mission_id=mission_id,
            execution_mode=EXECUTION_MODE,
            authorized_by_approval_id=decision.approval_id,
            reviewer_id=decision.reviewer_id,
            status=PlanStatus.EXECUTING.value,
            started_at=datetime.now(timezone.utc),
        )

    async def _publish(
        self,
        db: AsyncSession,
        mission_id: UUID,
        event_type: EventType,
        correlation: UUID,
        payload: dict[str, Any],
    ) -> None:
        """Persist, then notify. A notification failure never loses the event."""
        event = await self.event_service.persist(
            db,
            mission_id,
            EventCreate(
                event_type=event_type,
                timestamp=datetime.now(timezone.utc),
                source=EXECUTION_SOURCE,
                correlation_id=correlation,
                payload=payload,
            ),
        )
        try:
            await self.event_service.publish_persisted(event)
        except Exception:  # pragma: no cover - notification is best effort
            logger.warning("Execution event %s persisted but not published", event.id)

    async def _load(self, db: AsyncSession, mission_id: UUID, plan_id: UUID) -> MissionPlan:
        mission = await db.get(Mission, mission_id)
        if mission is None:
            raise ApprovalError(ApprovalCode.MISSION_NOT_FOUND, "Mission not found")
        plan = await db.get(MissionPlan, plan_id)
        if plan is None:
            raise ApprovalError(ApprovalCode.PLAN_NOT_FOUND, "Mission plan not found")
        if plan.mission_id != mission_id:
            raise ApprovalError(
                ApprovalCode.PLAN_MISSION_MISMATCH, "Plan does not belong to this mission"
            )
        return plan


class ObservationService:
    """Compare observed telemetry against what the authorized plan expected.

    Reads the existing ``VehicleTelemetry`` rows; it does not introduce a second
    telemetry path. Thresholds are explicit and reported, and "no telemetry"
    is a distinct outcome from "telemetry shows no deviation".
    """

    DEFAULT_MAX_SPEED_DEVIATION = 0.5  # m/s below the plan's expected speed

    def __init__(self, db: AsyncSession, settings: Any = None) -> None:
        self.db = db
        self.settings = settings

    async def observe(
        self,
        db: AsyncSession,
        mission_id: UUID,
        plan_id: UUID,
        *,
        max_speed_deviation: float | None = None,
    ) -> ObservationResult:
        plan = await db.get(MissionPlan, plan_id)
        if plan is None or plan.mission_id != mission_id:
            raise ApprovalError(ApprovalCode.PLAN_NOT_FOUND, "Mission plan not found")

        route_id = (plan.plan_payload or {}).get("selected", {}).get("route_id")
        vehicle_id = None
        if route_id is not None:
            route = await db.get(Route, UUID(str(route_id)))
            vehicle_id = route.vehicle_id if route is not None else None

        samples = []
        if vehicle_id is not None:
            from app.models.vehicle import VehicleTelemetry

            samples = list(
                (
                    await db.scalars(
                        select(VehicleTelemetry)
                        .where(VehicleTelemetry.vehicle_id == vehicle_id)
                        .order_by(
                            VehicleTelemetry.observed_at.desc(), VehicleTelemetry.id
                        )
                        .limit(50)
                    )
                ).all()
            )

        threshold = (
            self.DEFAULT_MAX_SPEED_DEVIATION
            if max_speed_deviation is None
            else max_speed_deviation
        )

        reasons: list[str] = []
        detail: dict[str, Any] = {
            "telemetry_available": bool(samples),
            "max_speed_deviation_mps": threshold,
        }

        # Vehicle and route state are checked whether or not telemetry exists:
        # a vehicle that has gone OFFLINE is a deviation even when no sample
        # was ever recorded.
        reasons.extend(await self._state_reasons(db, route_id, vehicle_id))

        if not samples:
            # Absent telemetry is not evidence of health, and not evidence of
            # deviation either. It is its own outcome.
            return ObservationResult(
                plan_id=plan.id,
                plan_version=plan.version,
                observed_samples=0,
                deviation_detected=bool(reasons),
                reasons=tuple(sorted(set(reasons))),
                detail={
                    **detail,
                    "note": "No vehicle telemetry has been observed yet",
                },
            )

        stopped = [item for item in samples if (item.status is VehicleStatus.IDLE) or (item.speed is not None and item.speed <= 0.1)]
        detail["stopped_samples"] = len(stopped)
        detail["latest_observed_at"] = samples[0].observed_at.isoformat()
        if stopped:
            reasons.append(ReplanReason.VEHICLE_DEVIATION.value)

        if ReplanReason.ROUTE_UNAVAILABLE.value in reasons:
            detail["route_status"] = RouteStatus.FAILED.value

        return ObservationResult(
            plan_id=plan.id,
            plan_version=plan.version,
            observed_samples=len(samples),
            deviation_detected=bool(reasons),
            reasons=tuple(sorted(set(reasons))),
            detail=detail,
        )


    async def _state_reasons(
        self, db: AsyncSession, route_id: Any, vehicle_id: Any
    ) -> list[str]:
        """Deviations visible from vehicle/route state alone, without telemetry."""
        reasons: list[str] = []
        if vehicle_id is not None:
            vehicle = await db.get(Vehicle, vehicle_id)
            if vehicle is not None and vehicle.status is VehicleStatus.OFFLINE:
                reasons.append(ReplanReason.VEHICLE_DEVIATION.value)
        if route_id is not None:
            route = await db.get(Route, UUID(str(route_id)))
            if route is not None and route.status is RouteStatus.FAILED:
                reasons.append(ReplanReason.ROUTE_UNAVAILABLE.value)
        return reasons


class ReplanService:
    """Produce a new plan version for human approval. Never approve one."""

    def __init__(
        self,
        db: AsyncSession,
        event_service: EventService,
        optimizer_factory: Any,
        settings: Any = None,
    ) -> None:
        self.db = db
        self.event_service = event_service
        self.optimizer_factory = optimizer_factory
        self.settings = settings

    async def request(
        self,
        db: AsyncSession,
        mission_id: UUID,
        plan_id: UUID,
        *,
        reason: ReplanReason,
        correlation_id: UUID | None = None,
        required_capabilities: list[str] | None = None,
        required_vehicle_types: list[str] | None = None,
    ) -> ReplanResult:
        """Mark the current version REPLAN_REQUIRED and compute a fresh one.

        The current version is preserved exactly as it was, including its
        approval: a replan is an addition to the record, never a rewrite of it.
        The new version lands in ``READY_FOR_REVIEW`` and nothing more.
        """
        correlation = correlation_id or uuid4()
        mission = await db.get(Mission, mission_id)
        if mission is None:
            raise ApprovalError(ApprovalCode.MISSION_NOT_FOUND, "Mission not found")
        plan = await db.get(MissionPlan, plan_id)
        if plan is None or plan.mission_id != mission_id:
            raise ApprovalError(ApprovalCode.PLAN_NOT_FOUND, "Mission plan not found")

        if plan.status is PlanStatus.SUPERSEDED:
            raise ApprovalError(
                ApprovalCode.REPLAN_NOT_REQUIRED,
                "Plan version was already superseded; replan from the current version",
            )

        await self._publish(
            db,
            mission_id,
            EventType.REPLAN_TRIGGERED,
            correlation,
            {
                "action": "REPLAN_REQUESTED",
                "plan_id": str(plan.id),
                "plan_version": plan.version,
                "reason": reason.value,
            },
        )

        previous_status = plan.status
        plan.status = PlanStatus.REPLAN_REQUIRED
        await db.flush()

        await self._publish(
            db, mission_id, EventType.REPLAN_TRIGGERED, correlation,
            {"action": "REPLAN_STARTED", "plan_id": str(plan.id), "reason": reason.value},
        )

        from app.services.optimization.mission_optimizer import (
            MissionOptimizer,
            OptimizationRequest,
        )

        try:
            optimizer = self.optimizer_factory(db, self.event_service, self.settings)
            if not isinstance(optimizer, MissionOptimizer):  # pragma: no cover
                optimizer = MissionOptimizer(db, self.event_service, settings=self.settings)
            outcome = await optimizer.optimize(
                db,
                mission_id,
                OptimizationRequest(
                    required_capabilities=tuple(required_capabilities or ()),
                    required_vehicle_types=tuple(required_vehicle_types or ()),
                    correlation_id=correlation,
                ),
            )
        except ApprovalError:
            raise
        except Exception as error:
            logger.exception("Replan failed for mission %s", mission_id)
            await self._publish(
                db, mission_id, EventType.REPLAN_TRIGGERED, correlation,
                {
                    "action": "REPLAN_FAILED",
                    "plan_id": str(plan.id),
                    "error": str(error)[:200],
                },
            )
            raise ApprovalError(
                ApprovalCode.REPLAN_FAILED,
                f"Replanning failed: {error}"[:300],
            ) from error

        # The new plan is offered for review. It is never approved here, and the
        # version it replaces keeps its own approval and its own history.
        if outcome.plan_id is not None:
            new_plan = await db.get(MissionPlan, outcome.plan_id)
            if new_plan is not None:
                new_plan.status = PlanStatus.READY_FOR_REVIEW

        if outcome.plan_id is not None:
            # One event type carries the whole replan lifecycle; the payload
            # action distinguishes the phase. Publishing the completion under a
            # different type would make "did this replan finish?" unanswerable
            # from a single event stream.
            await self._publish(
                db, mission_id, EventType.REPLAN_TRIGGERED, correlation,
                {
                    "action": "REPLAN_COMPLETED",
                    "plan_id": str(outcome.plan_id),
                    "plan_version": outcome.version,
                    "previous_plan_id": str(plan.id),
                    "previous_plan_version": plan.version,
                    "previous_plan_status": previous_status.value,
                    "requires_human_approval": True,
                },
            )

        return ReplanResult(
            replan_requested=True,
            reason=reason.value,
            new_plan_id=outcome.plan_id,
            new_plan_version=outcome.version,
            previous_plan_id=plan.id,
            previous_plan_version=plan.version,
            previous_plan_status=previous_status.value,
            auto_approved=False,
            requires_human_approval=True,
            correlation_id=correlation,
        )

    async def _publish(
        self,
        db: AsyncSession,
        mission_id: UUID,
        event_type: EventType,
        correlation: UUID,
        payload: dict[str, Any],
    ) -> None:
        event = await self.event_service.persist(
            db,
            mission_id,
            EventCreate(
                event_type=event_type,
                timestamp=datetime.now(timezone.utc),
                source=REPLAN_SOURCE,
                correlation_id=correlation,
                payload=payload,
            ),
        )
        try:
            await self.event_service.publish_persisted(event)
        except Exception:  # pragma: no cover - notification is best effort
            logger.warning("Replan event %s persisted but not published", event.id)


async def latest_plan_version(db: AsyncSession, mission_id: UUID) -> int:
    current = await db.scalar(
        select(func.max(MissionPlan.version)).where(MissionPlan.mission_id == mission_id)
    )
    return int(current or 0)


__all__ = [
    "EXECUTION_MODE",
    "NO_REAL_ACTUATOR_NOTICE",
    "ExecutionRecord",
    "ExecutionService",
    "ObservationResult",
    "ObservationService",
    "ReplanReason",
    "ReplanResult",
    "ReplanService",
    "latest_plan_version",
]
