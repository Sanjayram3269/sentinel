"""Vehicle creation and lookup endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from geoalchemy2 import WKTElement
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import Vehicle
from app.schemas.domain import GeoPoint, VehicleCreate, VehicleRead

router = APIRouter(prefix="/vehicles", tags=["vehicles"])


def _vehicle_read(
    vehicle: Vehicle,
    latitude: float | None,
    longitude: float | None,
) -> VehicleRead:
    return VehicleRead(
        id=vehicle.id,
        mission_id=vehicle.mission_id,
        vehicle_type=vehicle.vehicle_type,
        status=vehicle.status,
        call_sign=vehicle.call_sign,
        capability=vehicle.capability,
        current_location=(
            GeoPoint(latitude=latitude, longitude=longitude)
            if latitude is not None and longitude is not None
            else None
        ),
        latitude=latitude,
        longitude=longitude,
        heading=vehicle.heading,
        speed=vehicle.speed,
        created_at=vehicle.created_at,
        updated_at=vehicle.updated_at,
    )


async def _read_coordinates(
    db: AsyncSession,
    vehicle_id: UUID,
) -> tuple[float | None, float | None]:
    return (
        await db.execute(
            select(
                func.ST_Y(Vehicle.current_location),
                func.ST_X(Vehicle.current_location),
            ).where(Vehicle.id == vehicle_id)
        )
    ).one()


@router.post("", response_model=VehicleRead, status_code=status.HTTP_201_CREATED)
async def create_vehicle(
    payload: VehicleCreate,
    db: AsyncSession = Depends(get_db),
) -> VehicleRead:
    values = payload.model_dump(exclude={"current_location"})
    location = payload.current_location
    async with db.begin():
        vehicle = Vehicle(
            **values,
            current_location=(
                WKTElement(
                    f"POINT({location.longitude} {location.latitude})", srid=4326
                )
                if location is not None
                else None
            ),
        )
        db.add(vehicle)
        await db.flush()
        await db.refresh(vehicle)
        latitude, longitude = await _read_coordinates(db, vehicle.id)
    return _vehicle_read(vehicle, latitude, longitude)


@router.get("/{vehicle_id}", response_model=VehicleRead)
async def get_vehicle(
    vehicle_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> VehicleRead:
    vehicle = await db.get(Vehicle, vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    latitude, longitude = await _read_coordinates(db, vehicle.id)
    return _vehicle_read(vehicle, latitude, longitude)