"""Routing, candidate, activation, monitoring, and resilience API contracts."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.enums import ResilienceRole, RouteStatus
from app.schemas.domain import GeoPoint


class ResilienceLevel(str, Enum):
    HIGH = "HIGH_RESILIENCE"
    MEDIUM = "MEDIUM_RESILIENCE"
    LOW = "LOW_RESILIENCE"
    NONE = "NO_RESILIENCE"


class RouteProposal(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    geometry: list[GeoPoint] = Field(min_length=2, max_length=5000)
    distance_meters: float = Field(ge=0, allow_inf_nan=False)
    estimated_duration_seconds: int = Field(ge=0)
    risk_score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    predicted_failure_probability: float | None = Field(
        default=None, ge=0, le=1, allow_inf_nan=False
    )
    congestion_score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    hazard_exposure: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    road_segment_ids: list[str] = Field(default_factory=list, max_length=10000)
    reachable: bool | None = None


class RouteCandidateRequest(BaseModel):
    vehicle_id: UUID
    origin: GeoPoint
    destination: GeoPoint
    # Optional. When omitted or empty the routes are generated from the imported
    # road network instead of being taken from the caller, which is the normal
    # path now. Supplying candidates keeps the previous behaviour exactly.
    candidates: list[RouteProposal] = Field(default_factory=list, max_length=50)
    routing_parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def candidate_endpoints_match(self) -> "RouteCandidateRequest":
        for candidate in self.candidates:
            if candidate.geometry[0] != self.origin:
                raise ValueError("candidate geometry must start at origin")
            if candidate.geometry[-1] != self.destination:
                raise ValueError("candidate geometry must end at destination")
        return self


class RouteActivationRequest(BaseModel):
    correlation_id: UUID | None = None


class RouteCandidateRead(BaseModel):
    candidate_id: UUID
    route_id: UUID | None
    mission_id: UUID
    vehicle_id: UUID
    planning_cycle_id: UUID
    origin: GeoPoint
    destination: GeoPoint
    geometry: list[GeoPoint]
    road_segment_ids: list[str]
    distance_meters: float
    estimated_duration_seconds: int
    risk_score: float | None
    predicted_failure_probability: float | None
    hazard_exposure: dict[str, Any]
    congestion_score: float | None
    resilience_role: ResilienceRole | None
    status: RouteStatus
    score: float | None
    score_coverage: float
    viable: bool
    rationale: str | None
    provider: str
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def timestamps_are_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must include a timezone")
        return value.astimezone(timezone.utc)


class RouteCandidateCollection(BaseModel):
    mission_id: UUID
    vehicle_id: UUID
    planning_cycle_id: UUID
    provider: str
    candidates: list[RouteCandidateRead]
    resilience: "ResilienceRead"


class RouteRead(BaseModel):
    id: UUID
    mission_id: UUID
    vehicle_id: UUID
    status: RouteStatus
    name: str
    geometry: list[GeoPoint]
    distance_meters: float
    estimated_duration_seconds: int
    risk_score: float | None
    resilience_role: ResilienceRole | None
    created_at: datetime
    updated_at: datetime


class RouteCollection(BaseModel):
    items: list[RouteRead]
    limit: int


class ResilienceRead(BaseModel):
    mission_id: UUID
    vehicle_id: UUID | None
    planning_cycle_id: UUID | None
    primary_route_id: UUID | None
    backup_route_id: UUID | None
    contingency_route_id: UUID | None
    resilience_level: ResilienceLevel
    resilience_score: float = Field(ge=0, le=1)
    route_diversity: float = Field(ge=0, le=1)
    # Optional: the resilience engine reports None when no candidate carries a
    # failure signal or a route risk. The engine has always been explicit about
    # that case ("Failure exposure is unknown"), and the response schema has to
    # be able to express it -- otherwise generating routes with no predicted
    # risk at all cannot be serialised.
    failure_exposure: float | None = Field(default=None, ge=0, le=1)
    primary_available: bool
    backup_available: bool
    contingency_available: bool
    explanation: list[str]


class RouteActivationRead(BaseModel):
    route: RouteRead
    event_id: UUID
    correlation_id: UUID


class RouteHealthRead(BaseModel):
    route_id: UUID
    mission_id: UUID
    vehicle_id: UUID
    health: str
    failure_probability: float | None
    deviation_meters: float | None
    failure_detected: bool
    backup_degraded: bool
    replan_required: bool
    reason: str | None
    recommended_action: str | None
    emitted_event_ids: list[UUID]