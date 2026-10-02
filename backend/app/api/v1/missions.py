"""Mission creation, lookup, and aggregate state endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import Event, Incident, Mission, Route, Vehicle
from app.models.enums import RouteStatus
from app.schemas.domain import (
    EventState,
    GeoPoint,
    IncidentRead,
    MissionCreate,
    MissionRead,
    MissionStateRead,
    RouteState,
    VehicleRead,
)

router = APIRouter(prefix="/missions", tags=["missions"])


@router.post("", response_model=MissionRead, status_code=status.HTTP_201_CREATED)
async def create_mission(
    payload: MissionCreate,
    db: AsyncSession = Depends(get_db),
) -> MissionRead:
    async with db.begin():
        mission = Mission(**payload.model_dump())
        db.add(mission)
        await db.flush()
        await db.refresh(mission)
    return MissionRead.model_validate(mission)


@router.get("/{mission_id}", response_model=MissionRead)
async def get_mission(
    mission_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> MissionRead:
    mission = await db.get(Mission, mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    return MissionRead.model_validate(mission)


@router.get("/{mission_id}/state", response_model=MissionStateRead)
async def get_mission_state(
    mission_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> MissionStateRead:
    mission = await db.get(Mission, mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission not found")

    incident_rows = (
        await db.execute(
            select(
                Incident,
                func.ST_Y(Incident.location),
                func.ST_X(Incident.location),
            )
            .where(Incident.mission_id == mission_id)
            .order_by(Incident.occurred_at.desc())
        )
    ).all()
    incidents = [
        IncidentRead(
            id=row.Incident.id,
            mission_id=row.Incident.mission_id,
            type=row.Incident.type,
            severity=row.Incident.severity,
            description=row.Incident.description,
            location=GeoPoint(latitude=row[1], longitude=row[2]),
            occurred_at=row.Incident.occurred_at,
            active=row.Incident.active,
            created_at=row.Incident.created_at,
            updated_at=row.Incident.updated_at,
        )
        for row in incident_rows
    ]

    vehicle_rows = (
        await db.execute(
            select(
                Vehicle,
                func.ST_Y(Vehicle.current_location),
                func.ST_X(Vehicle.current_location),
            ).where(Vehicle.mission_id == mission_id)
        )
    ).all()
    vehicles = [
        VehicleRead(
            id=row.Vehicle.id,
            mission_id=row.Vehicle.mission_id,
            vehicle_type=row.Vehicle.vehicle_type,
            status=row.Vehicle.status,
            call_sign=row.Vehicle.call_sign,
            capability=row.Vehicle.capability,
            current_location=(
                GeoPoint(latitude=row[1], longitude=row[2])
                if row[1] is not None and row[2] is not None
                else None
            ),
            heading=row.Vehicle.heading,
            speed=row.Vehicle.speed,
            created_at=row.Vehicle.created_at,
            updated_at=row.Vehicle.updated_at,
        )
        for row in vehicle_rows
    ]

    routes = (
        await db.scalars(
            select(Route)
            .where(Route.mission_id == mission_id, Route.status == RouteStatus.ACTIVE)
            .order_by(Route.created_at.desc())
        )
    ).all()
    events = (
        await db.scalars(
            select(Event)
            .where(Event.mission_id == mission_id)
            .order_by(Event.occurred_at.desc())
            .limit(20)
        )
    ).all()

    return MissionStateRead(
        mission=MissionRead.model_validate(mission),
        incidents=incidents,
        vehicles=vehicles,
        active_routes=[
            RouteState(
                id=route.id,
                vehicle_id=route.vehicle_id,
                status=route.status,
                name=route.name,
                distance_meters=route.distance_meters,
                estimated_duration_seconds=route.estimated_duration_seconds,
                risk_score=route.risk_score,
            )
            for route in routes
        ],
        latest_events=[EventState.model_validate(event) for event in events],
    )