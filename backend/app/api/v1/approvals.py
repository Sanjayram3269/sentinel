"""Human-in-the-loop approval and prototype execution APIs (Phase 8).

These endpoints are the governance boundary. ``approve`` records a human
decision; ``execute`` is the only path to a prototype execution state and it
consults the authorization gate unconditionally. There is no endpoint that
approves implicitly, and none that turns a simulation result into an action.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.plans import _optimizer
from app.db.session import get_db
from app.schemas.approval import (
    ApprovalRead,
    ApprovalRequest,
    ApprovalRecordRead,
    ExecutionRead,
    GateDecisionRead,
    ModifyPlanRead,
    ModifyPlanRequest,
    ObservationRead,
    ReplanRead,
    ReplanRequest,
    RejectionRequest,
)
from app.services.approval import ApprovalRecord, ApprovalService, AuthorizationGate
from app.services.event_bus import EventPublisher
from app.services.event_service import EventService
from app.services.mission_execution import (
    ExecutionService,
    ObservationService,
    ReplanReason,
    ReplanService,
)
from app.services.plan_modification import PlanModificationService

router = APIRouter(prefix="/missions", tags=["approvals"])


def _events(request: Request) -> EventService:
    """The single existing event service. Phase 8 adds no second event bus."""
    return EventService(EventPublisher(request.app.state.redis))


def _approval(request: Request, db: AsyncSession) -> ApprovalService:
    return ApprovalService(db, _events(request), getattr(request.app.state, "settings", None))


def _execution(request: Request, db: AsyncSession) -> ExecutionService:
    return ExecutionService(
        db,
        _events(request),
        AuthorizationGate(db, getattr(request.app.state, "settings", None)),
        getattr(request.app.state, "settings", None),
    )


def _record_read(record: ApprovalRecord) -> ApprovalRecordRead:
    return ApprovalRecordRead(
        approval_id=record.approval_id,
        plan_id=record.plan_id,
        plan_version=record.plan_version,
        decision=record.decision.value,
        reviewer_id=record.reviewer_id,
        reviewer_role=record.reviewer_role,
        comment=record.comment,
        previous_plan_status=record.previous_plan_status,
        new_plan_status=record.new_plan_status,
        decided_at=record.decided_at,
        created_at=record.created_at,
        evidence=record.evidence,
        context=record.context,
    )


@router.get(
    "/{mission_id}/plans/{plan_id}/review",
    summary="Everything a reviewer needs before deciding",
    description=(
        "Returns the Phase 6 plan context and the Phase 7 CLEARPATH simulation "
        "evidence for one plan version. Nothing here authorizes anything: the "
        "plan is not executable until a named human approves this exact version."
    ),
)
async def review_plan(
    mission_id: UUID,
    plan_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await _approval(request, db).review_package(db, mission_id, plan_id)


@router.post(
    "/{mission_id}/plans/{plan_id}/approve",
    response_model=ApprovalRead,
    status_code=status.HTTP_201_CREATED,
    summary="Record a human approval of one exact plan version",
    description=(
        "Persists a named human decision against the supplied plan version. "
        "Approval does not authorize execution: that is a separate, server-side "
        "gate which re-checks feasibility, resource validity and network "
        "freshness. Approving the same version twice is idempotent."
    ),
)
async def approve_plan(
    mission_id: UUID,
    plan_id: UUID,
    payload: ApprovalRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ApprovalRead:
    service = _approval(request, db)
    existing = await service.db.scalar(
        _existing_approval(plan_id, payload.plan_version)
    )
    record = await service.approve(
        db,
        mission_id,
        plan_id,
        plan_version=payload.plan_version,
        reviewer_id=payload.reviewer_id,
        comment=payload.comment,
        reviewer_role=payload.reviewer_role,
    )
    await db.commit()
    plan = await service._load(db, mission_id, plan_id)
    return ApprovalRead(
        approval=_record_read(record),
        plan_id=record.plan_id,
        plan_version=record.plan_version,
        plan_status=plan.status.value,
        execution_authorized=False,
        requires_human_approval=False,
        idempotent_replay=existing is not None,
    )


@router.post(
    "/{mission_id}/plans/{plan_id}/reject",
    response_model=ApprovalRead,
    status_code=status.HTTP_201_CREATED,
    summary="Record a human rejection of one exact plan version",
    description=(
        "A rejected plan can never execute. Rejection is persisted with its "
        "reason and does not trigger any other plan."
    ),
)
async def reject_plan(
    mission_id: UUID,
    plan_id: UUID,
    payload: RejectionRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ApprovalRead:
    service = _approval(request, db)
    record = await service.reject(
        db,
        mission_id,
        plan_id,
        plan_version=payload.plan_version,
        reviewer_id=payload.reviewer_id,
        reason=payload.comment,
        reviewer_role=payload.reviewer_role,
    )
    await db.commit()
    plan = await service._load(db, mission_id, plan_id)
    return ApprovalRead(
        approval=_record_read(record),
        plan_id=record.plan_id,
        plan_version=record.plan_version,
        plan_status=plan.status.value,
        execution_authorized=False,
        requires_human_approval=False,
    )


@router.post(
    "/{mission_id}/plans/{plan_id}/modify",
    response_model=ModifyPlanRead,
    status_code=status.HTTP_201_CREATED,
    summary="Supersede a plan version with a reviewer-modified one",
    description=(
        "Never edits a plan in place. The current version becomes SUPERSEDED and "
        "a new version is created in READY_FOR_REVIEW. The previous approval "
        "does not carry over: the new version requires its own human approval."
    ),
)
async def modify_plan(
    mission_id: UUID,
    plan_id: UUID,
    payload: ModifyPlanRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ModifyPlanRead:
    service = PlanModificationService(db, _events(request))
    result = await service.modify(
        db,
        mission_id,
        plan_id,
        plan_version=payload.plan_version,
        reviewer_id=payload.reviewer_id,
        modifications=payload.modifications,
        comment=payload.comment,
    )
    await db.commit()
    return ModifyPlanRead(
        previous_plan_id=result.previous_plan_id,
        previous_plan_version=result.previous_plan_version,
        previous_plan_status=result.previous_plan_status,
        new_plan_id=result.new_plan_id,
        new_plan_version=result.new_plan_version,
        new_plan_status=result.new_plan_status,
        applied_modifications=result.applied,
        requires_human_approval=result.requires_human_approval,
        previous_approval_invalidated=result.previous_approval_invalidated,
    )


@router.get(
    "/{mission_id}/plans/{plan_id}/authorization",
    response_model=GateDecisionRead,
    summary="Explain whether this plan version is authorized for execution",
    description=(
        "Runs the same server-side gate the execute endpoint uses and returns "
        "its verdict with every reason. Read-only: it never changes state."
    ),
)
async def plan_authorization(
    mission_id: UUID,
    plan_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> GateDecisionRead:
    from app.models import MissionPlan

    plan = await db.get(MissionPlan, plan_id)
    if plan is None or plan.mission_id != mission_id:
        from app.services.approval import ApprovalCode, ApprovalError

        raise ApprovalError(ApprovalCode.PLAN_NOT_FOUND, "Mission plan not found")
    decision = await AuthorizationGate(
        db, getattr(request.app.state, "settings", None)
    ).evaluate(plan)
    return GateDecisionRead(**decision.as_payload())


@router.post(
    "/{mission_id}/plans/{plan_id}/execute",
    response_model=ExecutionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Authorize a plan for PROTOTYPE execution",
    description=(
        "Records a prototype execution state for an approved plan version. This "
        "does NOT dispatch a vehicle, contact a hospital or control a traffic "
        "signal: the system contains no real-world actuator. Execution fails "
        "closed unless a human approval exists for this exact version and every "
        "gate check passes."
    ),
)
async def execute_plan(
    mission_id: UUID,
    plan_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ExecutionRead:
    record = await _execution(request, db).execute(db, mission_id, plan_id)
    await db.commit()
    return ExecutionRead(
        plan_id=record.plan_id,
        plan_version=record.plan_version,
        mission_id=record.mission_id,
        execution_mode=record.execution_mode,  # type: ignore[arg-type]
        authorized_by_approval_id=record.authorized_by_approval_id,
        reviewer_id=record.reviewer_id,
        status=record.status,
        started_at=record.started_at,
        notice=record.notice,
    )


@router.post(
    "/{mission_id}/plans/{plan_id}/observe",
    response_model=ObservationRead,
    summary="Compare observed telemetry against the authorized plan",
    description=(
        "Reads existing vehicle telemetry and reports deviation. Absence of "
        "telemetry is reported as its own outcome, never as 'no deviation'."
    ),
)
async def observe_plan(
    mission_id: UUID,
    plan_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ObservationRead:
    body = await request.json() if await request.body() else {}
    observation = await ObservationService(
        db, getattr(request.app.state, "settings", None)
    ).observe(
        db,
        mission_id,
        plan_id,
        max_speed_deviation=body.get("max_speed_deviation_mps"),
    )
    return ObservationRead(
        plan_id=observation.plan_id,
        plan_version=observation.plan_version,
        observed_samples=observation.observed_samples,
        deviation_detected=observation.deviation_detected,
        reasons=list(observation.reasons),
        detail=observation.detail,
        replan_required=observation.deviation_detected,
        recommended_replan_reason=observation.reasons[0] if observation.reasons else None,
    )


@router.post(
    "/{mission_id}/plans/{plan_id}/replan",
    response_model=ReplanRead,
    status_code=status.HTTP_201_CREATED,
    summary="Replan from an observed deviation, returning a plan for review",
    description=(
        "Reuses the existing Phase 5 routing and Phase 6 optimization. The "
        "superseded version is preserved with its own approval, and the new plan "
        "is always returned in READY_FOR_REVIEW. This endpoint cannot approve, "
        "authorize or execute the plan it produces."
    ),
)
async def replan_mission(
    mission_id: UUID,
    plan_id: UUID,
    payload: ReplanRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ReplanRead:
    settings = getattr(request.app.state, "settings", None)
    service = ReplanService(db, _events(request), lambda *_: _optimizer(request), settings)
    try:
        reason = ReplanReason(payload.reason)
    except ValueError as error:
        from app.services.approval import ApprovalCode, ApprovalError

        raise ApprovalError(
            ApprovalCode.REPLAN_FAILED,
            f"Unknown replan reason {payload.reason!r}",
        ) from error

    result = await service.request(
        db,
        mission_id,
        plan_id,
        reason=reason,
        correlation_id=payload.correlation_id,
    )
    await db.commit()
    return ReplanRead(
        replan_requested=result.replan_requested,
        reason=result.reason,
        new_plan_id=result.new_plan_id,
        new_plan_version=result.new_plan_version,
        previous_plan_id=result.previous_plan_id,
        previous_plan_version=result.previous_plan_version,
        previous_plan_status=result.previous_plan_status,
        auto_approved=result.auto_approved,
        requires_human_approval=result.requires_human_approval,
        note=result.note,
    )


def _existing_approval(plan_id: UUID, plan_version: int):
    """The persisted decision for a version, if one exists.

    Used only to label a replay as idempotent in the response; the approval
    service re-reads and re-checks it, so this never decides anything.
    """
    from sqlalchemy import select

    from app.models.plan import PlanApproval

    return select(PlanApproval).where(
        PlanApproval.plan_id == plan_id,
        PlanApproval.plan_version == plan_version,
    )


__all__ = ["router"]
