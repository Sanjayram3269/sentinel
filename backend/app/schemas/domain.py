"""Request and response schemas for initial domain APIs."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import (
    IncidentType,
    MissionStatus,
    RouteStatus,
    VehicleStatus,
    VehicleType,
)


class GeoPoint(BaseModel):
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)


class MissionCreate(BaseModel):
    status: MissionStatus = MissionStatus.CREATED
    priority: int = Field(default=3, ge=1, le=5)
    objective: str = Field(min_length=1, max_length=4000)


class MissionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: MissionStatus
    priority: int
    objective: str
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class IncidentCreate(BaseModel):
    mission_id: UUID | None = None
    type: IncidentType
    severity: int = Field(ge=1, le=5)
    description: str = Field(min_length=1, max_length=4000)
    location: GeoPoint
    occurred_at: datetime | None = None
    active: bool = True

    @field_validator("occurred_at")
    @classmethod
    def normalize_occurred_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value.astimezone(timezone.utc)


class IncidentRead(BaseModel):
    id: UUID
    mission_id: UUID | None
    type: IncidentType
    severity: int
    description: str
    location: GeoPoint
    occurred_at: datetime
    active: bool
    created_at: datetime
    updated_at: datetime


class VehicleCreate(BaseModel):
    mission_id: UUID | None = None
    vehicle_type: VehicleType
    status: VehicleStatus = VehicleStatus.AVAILABLE
    call_sign: str = Field(min_length=1, max_length=80)
    capability: dict[str, Any] = Field(default_factory=dict)
    current_location: GeoPoint | None = None
    heading: float | None = Field(default=None, ge=0, lt=360)
    speed: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
        description="Vehicle speed in meters per second.",
    )


class VehicleRead(BaseModel):
    id: UUID
    mission_id: UUID | None
    vehicle_type: VehicleType
    status: VehicleStatus
    call_sign: str
    capability: dict[str, Any]
    current_location: GeoPoint | None
    latitude: float | None = None
    longitude: float | None = None
    heading: float | None
    speed: float | None
    created_at: datetime
    updated_at: datetime


class RouteState(BaseModel):
    id: UUID
    vehicle_id: UUID
    status: RouteStatus
    name: str
    distance_meters: float
    estimated_duration_seconds: int
    risk_score: float | None


class EventState(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_type: str
    source: str
    occurred_at: datetime
    correlation_id: UUID
    payload: dict[str, Any]


class MissionStateRead(BaseModel):
    mission_id: UUID
    status: MissionStatus
    incident: IncidentRead | None = None
    mission: MissionRead
    incidents: list[IncidentRead]
    vehicles: list[VehicleRead]
    active_routes: list[RouteState]
    routes: list[RouteState]
    active_plan: dict[str, Any] | None = None
    latest_events: list[EventState]
    updated_at: datetime