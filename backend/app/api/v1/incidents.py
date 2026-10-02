"""Incident creation and lookup endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from geoalchemy2 import WKTElement
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import Incident
from app.schemas.domain import GeoPoint, IncidentCreate, IncidentRead

router = APIRouter(prefix="/incidents", tags=["incidents"])


def _incident_read(incident: Incident, latitude: float, longitude: float) -> IncidentRead:
    return IncidentRead(
        id=incident.id,
        mission_id=incident.mission_id,
        type=incident.type,
        severity=incident.severity,
        description=incident.description,
        location=GeoPoint(latitude=latitude, longitude=longitude),
        occurred_at=incident.occurred_at,
        active=incident.active,
        created_at=incident.created_at,
        updated_at=incident.updated_at,
    )


async def _read_coordinates(db: AsyncSession, incident_id: UUID) -> tuple[float, float]:
    latitude, longitude = (
        await db.execute(
            select(func.ST_Y(Incident.location), func.ST_X(Incident.location)).where(
                Incident.id == incident_id
            )
        )
    ).one()
    return latitude, longitude


@router.post("", response_model=IncidentRead, status_code=status.HTTP_201_CREATED)
async def create_incident(
    payload: IncidentCreate,
    db: AsyncSession = Depends(get_db),
) -> IncidentRead:
    values = payload.model_dump(exclude={"location"})
    async with db.begin():
        incident = Incident(
            **values,
            location=WKTElement(
                f"POINT({payload.location.longitude} {payload.location.latitude})",
                srid=4326,
            ),
        )
        db.add(incident)
        await db.flush()
        await db.refresh(incident)
        latitude, longitude = await _read_coordinates(db, incident.id)
    return _incident_read(incident, latitude, longitude)


@router.get("/{incident_id}", response_model=IncidentRead)
async def get_incident(
    incident_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> IncidentRead:
    incident = await db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    latitude, longitude = await _read_coordinates(db, incident.id)
    return _incident_read(incident, latitude, longitude)