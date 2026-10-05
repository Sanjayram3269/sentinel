"""Mission plan computation, retrieval, and what-if analysis APIs.

These endpoints extend the existing mission surface. No Phase 3/4/5 contract is
changed, and no frontend-shaped endpoint is added: the response carries the
selection, the feasibility verdict, and the full rationale because all three are
part of what the API is for.
"""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import Mission, MissionPlan
from app.schemas.planning import (
    PlanOptimizationRead,
    PlanOptimizationRequest,
    PlanRead,
    WhatIfRead,
    WhatIfRequest,
)
from app.services.event_bus import EventPublisher
from app.services.event_service import EventService
from app.services.optimization.mission_optimizer import (
    MissionOptimizer,
    MissionPlanOutcome,
    OptimizationRequest,
)
from app.services.optimization.whatif import WhatIfChange, WhatIfService

router = APIRouter(prefix="/missions", tags=["plans"])


def _optimizer(request: Request) -> MissionOptimizer:
    return MissionOptimizer(EventService(EventPublisher(request.app.state.redis)))


def _payload_of(plan: MissionPlan) -> dict[str, Any]:
    return plan.plan_payload if isinstance(plan.plan_payload, dict) else {}


def _selected(payload: dict[str, Any]) -> dict[str, Any]:
    selected = payload.get("selected")
    return selected if isinstance(selected, dict) else {}


def _as_uuid(value: Any) -> UUID | None:
    try:
        return UUID(str(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_uuid_list(value: Any) -> list[UUID]:
    if not isinstance(value, list):
        return []
    parsed = [_as_uuid(item) for item in value]
    return [item for item in parsed if item is not None]


def _coverage(payload: dict[str, Any]) -> float:
    score = payload.get("score")
    value = score.get("coverage") if isinstance(score, dict) else None
    # ``coverage`` is stored as a number, but the explanation renders unknown
    # values as the string "unavailable"; neither should ever fail validation.
    return float(value) if isinstance(value, (int, float)) else 0.0


def _to_read(plan: MissionPlan) -> PlanRead:
    payload = _payload_of(plan)
    selected = _selected(payload)
    return PlanRead(
        id=plan.id,
        mission_id=plan.mission_id,
        version=plan.version,
        status=plan.status,
        objective=plan.objective,
        feasible=plan.feasible,
        score=plan.score,
        confidence=plan.confidence,
        rationale=plan.rationale,
        plan_payload=payload,
        score_coverage=_coverage(payload),
        selected_hospital_id=_as_uuid(selected.get("hospital_id")),
        selected_route_id=_as_uuid(selected.get("route_id")),
        selected_resource_ids=_as_uuid_list(selected.get("resource_ids")),
        infeasible_reasons=list(payload.get("infeasible_reasons") or []),
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )


def _to_optimization_read(outcome: MissionPlanOutcome) -> PlanOptimizationRead:
    payload = outcome.payload
    selected = _selected(payload)
    return PlanOptimizationRead(
        plan_id=outcome.plan_id,
        mission_id=outcome.mission_id,
        version=outcome.version or 0,
        status=outcome.status,
        objective=outcome.objective,
        feasible=outcome.feasible,
        score=outcome.score,
        score_coverage=_coverage(payload),
        selected_hospital_id=_as_uuid(selected.get("hospital_id")),
        selected_route_id=_as_uuid(selected.get("route_id")),
        selected_resource_ids=_as_uuid_list(selected.get("resource_ids")),
        rationale=outcome.rationale,
        plan_payload=payload,
        infeasible_reasons=list(payload.get("infeasible_reasons") or []),
        event_id=outcome.event_id,
        correlation_id=outcome.correlation_id,
        # Non-null whenever the outcome came from a persisted plan. A
        # non-persisted outcome has no creation time, and saying so is better
        # than stamping the response with the moment it was serialised.
        created_at=outcome.created_at or datetime.now(timezone.utc),
    )


@router.post(
    "/{mission_id}/plans/optimize",
    response_model=PlanOptimizationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Compute the best hospital, route, and resource plan for a mission",
    description=(
        "Reads the route candidates Phase 5 already generated; it never "
        "generates a route. A mission with no feasible combination returns "
        "201 with feasible=false and the reasons, rather than an error or a "
        "silently degraded plan."
    ),
)
async def optimize_mission_plan(
    mission_id: UUID,
    payload: PlanOptimizationRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> PlanOptimizationRead:
    await _require_mission(db, mission_id)
    outcome = await _optimizer(request).optimize(
        db,
        mission_id,
        OptimizationRequest(
            required_capabilities=tuple(payload.required_capabilities),
            required_vehicle_types=tuple(item.value for item in payload.required_vehicle_types),
            hospital_ids=tuple(payload.hospital_ids),
            vehicle_ids=tuple(payload.vehicle_ids),
            correlation_id=payload.correlation_id,
        ),
    )
    return _to_optimization_read(outcome)


@router.get(
    "/{mission_id}/plans/latest",
    response_model=PlanRead,
    summary="Retrieve the most recent plan for a mission",
)
async def get_latest_plan(
    mission_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> PlanRead:
    await _require_mission(db, mission_id)
    plan = await db.scalar(
        select(MissionPlan)
        .where(MissionPlan.mission_id == mission_id)
        .order_by(MissionPlan.version.desc(), MissionPlan.id.desc())
        .limit(1)
    )
    if plan is None:
        raise HTTPException(status_code=404, detail="Mission has no plan yet")
    return _to_read(plan)


@router.get(
    "/{mission_id}/plans/{plan_id}",
    response_model=PlanRead,
    summary="Retrieve one persisted plan",
)
async def get_plan(
    mission_id: UUID,
    plan_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> PlanRead:
    plan = await db.get(MissionPlan, plan_id)
    if plan is None or plan.mission_id != mission_id:
        raise HTTPException(status_code=404, detail="Mission plan not found")
    return _to_read(plan)


@router.post(
    "/{mission_id}/plans/whatif",
    response_model=WhatIfRead,
    summary="Recompute the plan with one selected component removed",
    description=(
        "Deterministic: the same optimizer runs again with one hospital, route, "
        "or resource excluded, and the difference is reported. The hypothetical "
        "is not persisted -- it is not a plan version."
    ),
)
async def plan_what_if(
    mission_id: UUID,
    payload: WhatIfRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> WhatIfRead:
    await _require_mission(db, mission_id)
    service = WhatIfService(_optimizer(request))
    result = await service.replan_without(
        db,
        mission_id,
        WhatIfChange(component=payload.component, component_id=payload.component_id),
        baseline_plan_id=payload.baseline_plan_id,
        request=OptimizationRequest(
            required_capabilities=tuple(payload.required_capabilities),
            required_vehicle_types=tuple(item.value for item in payload.required_vehicle_types),
            correlation_id=None,
        ),
    )
    return WhatIfRead(
        mission_id=mission_id,
        component=result.component,
        component_id=result.component_id,
        baseline_feasible=result.baseline_feasible,
        recomputed_feasible=result.recomputed_feasible,
        plan_changed=result.plan_changed,
        hospital_changed=result.hospital_changed,
        route_changed=result.route_changed,
        resources_changed=result.resources_changed,
        baseline_hospital_id=_as_uuid(result.baseline_hospital_id),
        recomputed_hospital_id=_as_uuid(result.recomputed_hospital_id),
        baseline_route_id=_as_uuid(result.baseline_route_id),
        recomputed_route_id=_as_uuid(result.recomputed_route_id),
        baseline_resource_ids=_as_uuid_list(list(result.baseline_resource_ids)),
        recomputed_resource_ids=_as_uuid_list(list(result.recomputed_resource_ids)),
        baseline_eta_seconds=result.baseline_eta_seconds,
        recomputed_eta_seconds=result.recomputed_eta_seconds,
        delta_eta_seconds=result.delta_eta_seconds,
        explanation=result.explanation,
        recomputed_infeasible_reasons=result.to_payload()["recomputed_infeasible_reasons"],
        recomputed_rationale=result.recomputed.rationale,
    )


async def _require_mission(db: AsyncSession, mission_id: UUID) -> None:
    exists = await db.scalar(select(func.count()).select_from(Mission).where(Mission.id == mission_id))
    if not exists:
        raise HTTPException(status_code=404, detail="Mission not found")
