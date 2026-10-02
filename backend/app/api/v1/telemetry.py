"""Mission vehicle telemetry ingestion endpoint."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import VehicleTelemetry
from app.schemas.domain import GeoPoint
from app.schemas.telemetry import TelemetryCreate, TelemetryRead
from app.services.event_bus import EventPublisher
from app.services.telemetry_service import TelemetryService

router = APIRouter(prefix="/missions", tags=["telemetry"])


@router.post(
    "/{mission_id}/telemetry",
    response_model=TelemetryRead,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest vehicle telemetry",
)
async def ingest_telemetry(
    mission_id: UUID,
    payload: TelemetryCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TelemetryRead:
    service = TelemetryService(EventPublisher(request.app.state.redis))
    telemetry, vehicle = await service.ingest(db, mission_id, payload)
    latitude, longitude = (
        await db.execute(
            select(
                func.ST_Y(VehicleTelemetry.position),
                func.ST_X(VehicleTelemetry.position),
            ).where(VehicleTelemetry.id == telemetry.id)
        )
    ).one()
    return TelemetryRead(
        id=telemetry.id,
        mission_id=mission_id,
        vehicle_id=vehicle.id,
        timestamp=telemetry.observed_at,
        position=GeoPoint(latitude=latitude, longitude=longitude),
        speed=telemetry.speed,
        heading=telemetry.heading,
        status=telemetry.status or vehicle.status,
    )