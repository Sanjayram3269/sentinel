"""Simulation API and digital-twin contracts."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.models.enums import SimulationStatus


class SimulationMode(str, Enum):
    BASELINE = "BASELINE"
    CLEARPATH = "CLEARPATH"


class SafetyDecision(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class SignalAction(BaseModel):
    signal_id: str = Field(min_length=1, max_length=120)
    traffic_signal_id: UUID
    requested_phase: str = Field(min_length=1, max_length=80)
    activation_time_seconds: int = Field(ge=0)
    maximum_duration_seconds: int = Field(ge=1, le=300)
    reason: str = Field(min_length=1, max_length=240)
    route_id: UUID


class SignalDefinition(BaseModel):
    traffic_signal_id: UUID
    signal_id: str = Field(min_length=1, max_length=120)
    edge_id: str = Field(min_length=1, max_length=120)
    valid_phases: list[str] = Field(min_length=2, max_length=32)
    preemption_phase: str
    release_phase: str
    safe_transitions: dict[str, list[str]]
    maximum_duration_seconds: int = Field(ge=1, le=300)
    initial_phase: str


class TrafficFlow(BaseModel):
    demand_id: str = Field(min_length=1, max_length=120)
    edge_ids: list[str] = Field(min_length=1, max_length=500)
    vehicle_count: int = Field(ge=0, le=1000)
    depart_period_seconds: float = Field(gt=0, le=3600, allow_inf_nan=False)
    vehicle_type_id: str = Field(default="passenger", min_length=1, max_length=120)


class SimulationRequest(BaseModel):
    vehicle_id: UUID
    route_id: UUID
    network_id: str = Field(min_length=1, max_length=160)
    route_edge_ids: list[str] = Field(min_length=1, max_length=500)
    traffic_signal_ids: list[UUID] = Field(default_factory=list, max_length=100)
    traffic_flows: list[TrafficFlow] = Field(default_factory=list, max_length=100)
    emergency_vehicle_configuration: dict[str, Any] = Field(default_factory=dict)
    seed: int = Field(ge=0, le=2_147_483_647)
    max_simulation_seconds: int = Field(default=1800, ge=1, le=86_400)
    correlation_id: UUID | None = None

    @field_validator("traffic_flows")
    @classmethod
    def bound_total_traffic_vehicles(cls, value: list[TrafficFlow]) -> list[TrafficFlow]:
        if sum(flow.vehicle_count for flow in value) > 5000:
            raise ValueError("total traffic vehicle count cannot exceed 5000")
        return value


class SimulationScenario(BaseModel):
    mission_id: UUID
    vehicle_id: UUID
    route_id: UUID
    mode: SimulationMode
    network_id: str
    route_edge_ids: list[str]
    traffic_signals: list[SignalDefinition]
    traffic_flows: list[TrafficFlow]
    emergency_vehicle_id: str
    emergency_vehicle_configuration: dict[str, Any]
    seed: int
    max_simulation_seconds: int
    start_time: datetime
    correlation_id: UUID
    simulation_only: bool = True

    @field_validator("start_time")
    @classmethod
    def start_time_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("start_time must include a timezone")
        return value.astimezone(timezone.utc)


class SimulationMetrics(BaseModel):
    emergency_vehicle_travel_time_seconds: float | None = Field(default=None, ge=0)
    total_delay_seconds: float | None = Field(default=None, ge=0)
    stopped_time_seconds: float | None = Field(default=None, ge=0)
    number_of_stops: int | None = Field(default=None, ge=0)
    average_speed_meters_per_second: float | None = Field(default=None, ge=0)
    route_completed: bool | None = None
    simulation_duration_seconds: float | None = Field(default=None, ge=0)
    background_average_delay_seconds: float | None = Field(default=None, ge=0)
    background_vehicle_throughput: int | None = Field(default=None, ge=0)


class SignalActionResult(BaseModel):
    action: SignalAction
    decision: SafetyDecision
    reason_code: str
    explanation: str
    released_at_seconds: int | None = None


class SimulationRunRead(BaseModel):
    simulation_id: UUID
    mission_id: UUID
    mode: SimulationMode
    status: SimulationStatus
    seed: int | None
    network_id: str | None
    simulator: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    metrics: SimulationMetrics | None
    clearpath_actions: list[SignalActionResult]
    correlation_id: UUID
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    error_code: str | None = None
    error_message: str | None = None


class SimulationHistory(BaseModel):
    items: list[SimulationRunRead]
    limit: int
    has_more: bool


class SimulationComparisonMetrics(BaseModel):
    travel_time_delta_seconds: float | None
    travel_time_improvement_percent: float | None
    stopped_time_delta_seconds: float | None
    stops_delta: int | None


class SimulationComparisonRead(BaseModel):
    mission_id: UUID
    seed: int
    baseline: SimulationRunRead
    clearpath: SimulationRunRead
    comparison: SimulationComparisonMetrics
    correlation_id: UUID