"""Vehicle telemetry API contracts."""

from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.models.enums import VehicleStatus
from app.schemas.domain import GeoPoint


class TelemetryCreate(BaseModel):
    vehicle_id: UUID
    timestamp: datetime
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    speed: float = Field(ge=0, allow_inf_nan=False)
    heading: float | None = Field(default=None, ge=0, lt=360, allow_inf_nan=False)
    status: VehicleStatus | None = None

    @field_validator("timestamp")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(timezone.utc)


class TelemetryRead(BaseModel):
    id: UUID
    mission_id: UUID
    vehicle_id: UUID
    timestamp: datetime
    position: GeoPoint
    speed: float
    heading: float | None
    status: VehicleStatus