"""Typed event API contracts."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EventType(str, Enum):
    MISSION_CREATED = "MISSION_CREATED"
    MISSION_STATUS_CHANGED = "MISSION_STATUS_CHANGED"
    VEHICLE_POSITION_UPDATE = "VEHICLE_POSITION_UPDATE"
    VEHICLE_STATUS_CHANGED = "VEHICLE_STATUS_CHANGED"
    INCIDENT_CREATED = "INCIDENT_CREATED"
    INCIDENT_UPDATED = "INCIDENT_UPDATED"
    ROAD_CLOSURE = "ROAD_CLOSURE"
    ACCIDENT_DETECTED = "ACCIDENT_DETECTED"
    HAZARD_UPDATED = "HAZARD_UPDATED"
    CONGESTION_CHANGED = "CONGESTION_CHANGED"
    ROUTE_ASSIGNED = "ROUTE_ASSIGNED"
    ROUTE_UPDATED = "ROUTE_UPDATED"
    ROUTE_FAILED = "ROUTE_FAILED"
    ROUTE_DEVIATION = "ROUTE_DEVIATION"
    BACKUP_ROUTE_DEGRADED = "BACKUP_ROUTE_DEGRADED"
    REPLAN_TRIGGERED = "REPLAN_TRIGGERED"
    PREDICTION_UPDATED = "PREDICTION_UPDATED"
    HOSPITAL_CAPACITY_CHANGED = "HOSPITAL_CAPACITY_CHANGED"
    CLEARPATH_REQUESTED = "CLEARPATH_REQUESTED"
    CLEARPATH_UPDATED = "CLEARPATH_UPDATED"
    PLAN_CREATED = "PLAN_CREATED"
    PLAN_APPROVED = "PLAN_APPROVED"
    PLAN_REJECTED = "PLAN_REJECTED"
    TELEMETRY_RECEIVED = "TELEMETRY_RECEIVED"
    SIMULATION_STARTED = "SIMULATION_STARTED"
    SIMULATION_COMPLETED = "SIMULATION_COMPLETED"
    SIMULATION_FAILED = "SIMULATION_FAILED"


class EventCreate(BaseModel):
    event_type: EventType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = Field(min_length=1, max_length=120)
    correlation_id: UUID = Field(default_factory=uuid4)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(timezone.utc)


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    event_id: UUID = Field(validation_alias="id")
    mission_id: UUID
    event_type: EventType
    timestamp: datetime = Field(validation_alias="occurred_at")
    source: str
    correlation_id: UUID
    payload: dict[str, Any]
    created_at: datetime


class MissionEventEnvelope(BaseModel):
    type: str = "MISSION_EVENT"
    mission_id: UUID
    event: EventRead


class EventHistory(BaseModel):
    items: list[EventRead]
    limit: int
    has_more: bool