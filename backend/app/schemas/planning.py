"""Mission optimization API contracts.

These extend the domain rather than replacing anything: Phase 5 routing and
Phase 4 prediction contracts are untouched. The response exposes the
explanation alongside the selection, because a plan whose reasoning cannot be
read back is not auditable.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import PlanStatus, VehicleType
from app.services.optimization.whatif import WhatIfComponent


class PlanOptimizationRequest(BaseModel):
    """What the caller requires of this mission.

    Both requirement lists default to empty, and empty means *no requirement
    was stated*. SENTINEL's mission model has no clinical requirement column,
    so nothing here is inferred from the incident: the caller says what the
    mission needs, or the plan reports that it needs nothing.
    """

    required_capabilities: list[str] = Field(
        default_factory=list,
        max_length=32,
        description=(
            "Capabilities the destination hospital must declare, e.g. "
            "['trauma']. A hospital that declares nothing cannot satisfy this "
            "and is rejected rather than assumed suitable. This constrains the "
            "hospital only; resource requirements are stated separately via "
            "required_vehicle_types."
        ),
    )
    required_vehicle_types: list[VehicleType] = Field(
        default_factory=list,
        max_length=8,
        description="One resource of each listed type is required.",
    )
    hospital_ids: list[UUID] = Field(
        default_factory=list,
        max_length=64,
        description="Restrict the candidate hospitals. Empty means every hospital.",
    )
    vehicle_ids: list[UUID] = Field(
        default_factory=list,
        max_length=64,
        description="Restrict the candidate resources. Empty means every vehicle.",
    )
    correlation_id: UUID | None = Field(
        default=None,
        description="Carried onto the emitted PLAN_CREATED event.",
    )


class PlanOptimizationRead(BaseModel):
    """A computed plan: what was chosen, whether it is feasible, and why."""

    plan_id: UUID
    mission_id: UUID
    version: int
    status: PlanStatus
    objective: str
    feasible: bool
    score: float | None = Field(
        default=None,
        description=(
            "Normalised weighted cost of the selected plan; lower is better. "
            "None when no option could be scored."
        ),
    )
    score_coverage: float = Field(
        ge=0, le=1,
        description="Fraction of objective weight backed by an available factor.",
    )
    selected_hospital_id: UUID | None = None
    selected_route_id: UUID | None = None
    selected_resource_ids: list[UUID] = Field(default_factory=list)
    rationale: str
    plan_payload: dict[str, Any]
    infeasible_reasons: list[str] = Field(default_factory=list)
    event_id: UUID | None = None
    correlation_id: UUID | None = None
    created_at: datetime


class PlanRead(BaseModel):
    """A persisted plan, as stored."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    mission_id: UUID
    version: int
    status: PlanStatus
    objective: str
    feasible: bool | None = None
    score: float | None = None
    confidence: float | None = Field(
        default=None,
        description=(
            "Always null for optimizer-produced plans. The optimizer has no "
            "calibrated confidence in its decisions and will not invent one."
        ),
    )
    rationale: str | None = None
    plan_payload: dict[str, Any]
    score_coverage: float = Field(default=0.0, ge=0, le=1)
    selected_hospital_id: UUID | None = None
    selected_route_id: UUID | None = None
    selected_resource_ids: list[UUID] = Field(default_factory=list)
    infeasible_reasons: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class WhatIfRequest(BaseModel):
    """Remove one selected component and see what the plan would become."""

    component: WhatIfComponent
    component_id: UUID
    baseline_plan_id: UUID | None = Field(
        default=None,
        description=(
            "Plan to compare against. When omitted, the baseline is recomputed "
            "from the same requirements so the comparison is like-for-like."
        ),
    )
    required_capabilities: list[str] = Field(default_factory=list, max_length=32)
    required_vehicle_types: list[VehicleType] = Field(default_factory=list, max_length=8)


class WhatIfRead(BaseModel):
    """The recomputed plan and an explicit statement of what changed."""

    mission_id: UUID
    component: WhatIfComponent
    component_id: UUID
    baseline_feasible: bool
    recomputed_feasible: bool
    plan_changed: bool
    hospital_changed: bool
    route_changed: bool
    resources_changed: bool
    baseline_hospital_id: UUID | None = None
    recomputed_hospital_id: UUID | None = None
    baseline_route_id: UUID | None = None
    recomputed_route_id: UUID | None = None
    baseline_resource_ids: list[UUID] = Field(default_factory=list)
    recomputed_resource_ids: list[UUID] = Field(default_factory=list)
    baseline_eta_seconds: int | None = None
    recomputed_eta_seconds: int | None = None
    delta_eta_seconds: int | None = None
    explanation: str
    recomputed_infeasible_reasons: list[str] = Field(default_factory=list)
    recomputed_rationale: str
