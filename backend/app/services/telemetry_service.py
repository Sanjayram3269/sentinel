"""Mission-scoped telemetry validation, persistence, and event publication."""

from uuid import UUID, uuid4

from fastapi import HTTPException
from geoalchemy2 import WKTElement
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event, Mission, Vehicle, VehicleTelemetry
from app.schemas.events import EventType
from app.schemas.telemetry import TelemetryCreate
from app.services.event_bus import EventPublisher


class TelemetryService:
    def __init__(self, publisher: EventPublisher) -> None:
        self.publisher = publisher

    async def ingest(
        self,
        db: AsyncSession,
        mission_id: UUID,
        payload: TelemetryCreate,
    ) -> tuple[VehicleTelemetry, Vehicle]:
        correlation_id = uuid4()
        async with db.begin():
            mission = await db.get(Mission, mission_id)
            if mission is None:
                raise HTTPException(status_code=404, detail="Mission not found")

            vehicle = await db.scalar(
                select(Vehicle)
                .where(Vehicle.id == payload.vehicle_id)
                .with_for_update()
            )
            if vehicle is None:
                raise HTTPException(status_code=404, detail="Vehicle not found")
            if vehicle.mission_id != mission_id:
                raise HTTPException(
                    status_code=409,
                    detail="Vehicle does not belong to this mission",
                )

            latest_observed_at = await db.scalar(
                select(func.max(VehicleTelemetry.observed_at)).where(
                    VehicleTelemetry.vehicle_id == vehicle.id
                )
            )
            position = WKTElement(
                f"POINT({payload.longitude} {payload.latitude})", srid=4326
            )
            telemetry = VehicleTelemetry(
                vehicle_id=vehicle.id,
                observed_at=payload.timestamp,
                position=position,
                speed=payload.speed,
                heading=payload.heading,
                status=payload.status,
                source="telemetry-api",
            )
            db.add(telemetry)
            if latest_observed_at is None or payload.timestamp >= latest_observed_at:
                vehicle.current_location = position
                vehicle.speed = payload.speed
                vehicle.heading = payload.heading
                if payload.status is not None:
                    vehicle.status = payload.status
            events = [
                Event(
                    mission_id=mission_id,
                    event_type=event_type.value,
                    source="telemetry-api",
                    occurred_at=payload.timestamp,
                    correlation_id=correlation_id,
                    payload={
                        "vehicle_id": str(vehicle.id),
                        "timestamp": payload.timestamp.isoformat(),
                        "latitude": payload.latitude,
                        "longitude": payload.longitude,
                        "speed": payload.speed,
                        "heading": payload.heading,
                        "status": payload.status.value if payload.status else None,
                    },
                )
                for event_type in (
                    EventType.TELEMETRY_RECEIVED,
                    EventType.VEHICLE_POSITION_UPDATE,
                )
            ]
            db.add_all(events)
            await db.flush()
            await db.refresh(telemetry)
            await db.refresh(vehicle)

        try:
            for event in events:
                await self.publisher.publish(event)
        except (RedisError, OSError) as error:
            raise HTTPException(
                status_code=503,
                detail="Telemetry was persisted but its events could not be published",
            ) from error
        return telemetry, vehicle