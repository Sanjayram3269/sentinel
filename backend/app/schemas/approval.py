"""Human-in-the-loop API contracts (Phase 8).

Every decision request carries the ``plan_version`` it is deciding about. That
is not a convenience for the client: it is the mechanism that stops a delayed or
replayed request from approving a plan that has since changed underneath it.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApprovalDecision(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ModificationField(str, Enum):
    """The only things a reviewer may change.

    A free-form payload would let a reviewer (or a bug) rewrite the optimizer's
    score, feasibility or objective, which are facts about how the plan was
    computed rather than choices. Only these selections are negotiable.
    """

    ROUTE = "ROUTE"
    HOSPITAL = "HOSPITAL"
    RESOURCE = "RESOURCE"


class ExecutionMode(str, Enum):
    """There is no other value. Real actuation is not implemented."""

    PROTOTYPE = "PROTOTYPE"


class ApprovalRequest(BaseModel):
    """Approve or reject one exact plan version."""

    plan_version: int = Field(ge=1)
    reviewer_id: str = Field(min_length=1, max_length=120)
    comment: str | None = Field(default=None, max_length=2000)
    reviewer_role: str | None = Field(default=None, max_length=120)
    #: Set when the reviewer is confirming they read the evidence. Recorded as
    #: reviewer attestation, not as a substitute for the evidence itself.
    evidence_reviewed: bool = False


class RejectionRequest(ApprovalRequest):
    """A rejection must say why."""

    comment: str = Field(min_length=1, max_length=2000)


class PlanModification(BaseModel):
    """One reviewer override, expressed in domain terms."""

    field: ModificationField
    #: The route or hospital UUID being selected.
    value: UUID | None = None
    #: Resource overrides replace the whole assignment, so they carry a list.
    value_list: list[UUID] | None = None
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("value_list")
    @classmethod
    def only_resources_use_lists(cls, value: list[UUID] | None) -> list[UUID] | None:
        return value

    def model_post_init(self, __context: Any) -> None:
        if self.field is ModificationField.RESOURCE:
            if self.value_list is None:
                raise ValueError("A RESOURCE modification requires value_list")
        elif self.value_list is not None:
            raise ValueError("Only a RESOURCE modification may carry value_list")


class ModifyPlanRequest(BaseModel):
    plan_version: int = Field(ge=1)
    reviewer_id: str = Field(min_length=1, max_length=120)
    modifications: list[PlanModification] = Field(min_length=1, max_length=16)
    comment: str | None = Field(default=None, max_length=2000)

    @field_validator("modifications")
    @classmethod
    def require_unique_fields(cls, value: list[PlanModification]) -> list[PlanModification]:
        seen = [item.field for item in value]
        if len(set(seen)) != len(seen):
            raise ValueError("Each modifiable field may appear at most once")
        return value


class ReplanRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=120)
    correlation_id: UUID | None = None


class ObserveRequest(BaseModel):
    max_speed_deviation_mps: float | None = Field(default=None, ge=0.0, le=100.0)


class ApprovalRecordRead(BaseModel):
    """A persisted decision, as stored."""

    model_config = ConfigDict(from_attributes=True)

    approval_id: UUID
    plan_id: UUID
    plan_version: int
    decision: str
    reviewer_id: str
    reviewer_role: str | None
    comment: str | None
    previous_plan_status: str | None
    new_plan_status: str
    decided_at: datetime
    created_at: datetime
    evidence: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)


class ApprovalRead(BaseModel):
    """Response of a decision endpoint."""

    approval: ApprovalRecordRead
    plan_id: UUID
    plan_version: int
    plan_status: str
    #: Always False. Present so no reader has to infer it.
    execution_authorized: bool = False
    requires_human_approval: bool = False
    idempotent_replay: bool = False


class ModifyPlanRead(BaseModel):
    previous_plan_id: UUID
    previous_plan_version: int
    previous_plan_status: str
    new_plan_id: UUID
    new_plan_version: int
    new_plan_status: str
    applied_modifications: list[dict[str, Any]] = Field(default_factory=list)
    requires_human_approval: bool = True
    previous_approval_invalidated: bool = True


class GateReasonRead(BaseModel):
    code: str
    detail: str


class GateDecisionRead(BaseModel):
    decision: str
    reasons: list[GateReasonRead] = Field(default_factory=list)
    plan_id: UUID | None = None
    plan_version: int | None = None
    approval_id: UUID | None = None
    reviewer_id: str | None = None


class ExecutionRead(BaseModel):
    plan_id: UUID
    plan_version: int
    mission_id: UUID
    execution_mode: ExecutionMode
    authorized_by_approval_id: UUID | None
    reviewer_id: str | None
    status: str
    started_at: datetime
    notice: str


class ObservationRead(BaseModel):
    plan_id: UUID
    plan_version: int
    observed_samples: int
    deviation_detected: bool
    reasons: list[str] = Field(default_factory=list)
    detail: dict[str, Any] = Field(default_factory=dict)
    replan_required: bool = False
    recommended_replan_reason: str | None = None


class ReplanRead(BaseModel):
    replan_requested: bool
    reason: str | None
    new_plan_id: UUID | None
    new_plan_version: int | None
    previous_plan_id: UUID | None
    previous_plan_version: int | None
    previous_plan_status: str | None
    auto_approved: bool = False
    requires_human_approval: bool = True
    note: str
