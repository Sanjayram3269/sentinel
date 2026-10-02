"""Route candidate, route retrieval, resilience, and activation APIs."""

from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.enums import ResilienceRole, RouteStatus
from app.schemas.routing import (
    ResilienceRead,
    RouteActivationRead,
    RouteActivationRequest,
    RouteCandidateCollection,
    RouteCandidateRequest,
    RouteCollection,
    RouteRead,
)
from app.services.event_bus import EventPublisher
from app.services.event_service import EventService
from app.services.route_service import RouteService
from app.services.routing.baseline import BaselineDevelopmentProvider

router = APIRouter(prefix="/missions", tags=["routes"])


def _route_service(request: Request) -> RouteService:
    event_service = EventService(EventPublisher(request.app.state.redis))
    return RouteService(
        event_service,
        BaselineDevelopmentProvider(),
    )


@router.post(
    "/{mission_id}/routes/candidates",
    response_model=RouteCandidateCollection,
    status_code=status.HTTP_201_CREATED,
    summary="Score and classify supplied route candidates",
    description=(
        "Persists caller/provider-supplied route geometry. The development provider "
        "does not fabricate roads or contact an external routing service."
    ),
)
async def generate_route_candidates(
    mission_id: UUID,
    payload: RouteCandidateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RouteCandidateCollection:
    return await _route_service(request).generate_candidates(db, mission_id, payload)


@router.get(
    "/{mission_id}/routes/resilience",
    response_model=ResilienceRead,
    summary="Evaluate the latest or selected route planning cycle",
)
async def get_route_resilience(
    mission_id: UUID,
    request: Request,
    vehicle_id: UUID | None = None,
    planning_cycle_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
) -> ResilienceRead:
    return await _route_service(request).resilience(
        db,
        mission_id,
        vehicle_id=vehicle_id,
        planning_cycle_id=planning_cycle_id,
    )


@router.post(
    "/{mission_id}/routes/{route_id}/activate",
    response_model=RouteActivationRead,
    summary="Activate a viable route for its mission vehicle",
)
async def activate_route(
    mission_id: UUID,
    route_id: UUID,
    request: Request,
    payload: RouteActivationRequest | None = Body(default=None),
    db: AsyncSession = Depends(get_db),
) -> RouteActivationRead:
    return await _route_service(request).activate(
        db,
        mission_id,
        route_id,
        correlation_id=payload.correlation_id if payload else None,
    )


@router.get(
    "/{mission_id}/routes",
    response_model=RouteCollection,
    summary="List bounded mission route history",
)
async def list_mission_routes(
    mission_id: UUID,
    request: Request,
    vehicle_id: UUID | None = None,
    role: ResilienceRole | None = None,
    route_status: RouteStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> RouteCollection:
    return await _route_service(request).list_routes(
        db,
        mission_id,
        vehicle_id=vehicle_id,
        role=role,
        route_status=route_status,
        limit=limit,
    )


@router.get(
    "/{mission_id}/routes/{route_id}",
    response_model=RouteRead,
    summary="Get a mission route",
)
async def get_mission_route(
    mission_id: UUID,
    route_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RouteRead:
    return await _route_service(request).get_route(db, mission_id, route_id)