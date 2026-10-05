"""Versioned response plans and human approvals."""

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKey
from app.models.enums import ApprovalStatus, PlanStatus, enum_values

if TYPE_CHECKING:
    from app.models.mission import Mission


class MissionPlan(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "mission_plans"
    __table_args__ = (
        UniqueConstraint("mission_id", "version", name="uq_mission_plans_version"),
        CheckConstraint("version >= 1", name="ck_mission_plans_version_positive"),
        Index("ix_mission_plans_mission_id", "mission_id"),
    )

    mission_id: Mapped[UUID] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[PlanStatus] = mapped_column(
        Enum(PlanStatus, name="plan_status", values_callable=enum_values),
        nullable=False,
        default=PlanStatus.DRAFT,
        server_default=PlanStatus.DRAFT.value,
    )
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    plan_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    score: Mapped[float | None] = mapped_column(Float)
    # Deliberately left unset by the optimizer. The field means "how confident
    # are we in this decision"; the optimizer has no calibrated quantity of
    # that kind, and a hard-constraint-plus-weighted-sum result does not
    # justify a number. Data coverage is recorded separately in the payload.
    confidence: Mapped[float | None] = mapped_column(Float)
    # NULL distinguishes "no feasibility was recorded" from an explicit False
    # for a plan that was computed and found to have no feasible combination.
    feasible: Mapped[bool | None] = mapped_column(Boolean)
    rationale: Mapped[str | None] = mapped_column(Text)

    mission: Mapped["Mission"] = relationship(back_populates="plans")
    approvals: Mapped[list["PlanApproval"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )


class PlanApproval(UUIDPrimaryKey, Base):
    __tablename__ = "plan_approvals"
    __table_args__ = (Index("ix_plan_approvals_plan_id", "plan_id"),)

    plan_id: Mapped[UUID] = mapped_column(
        ForeignKey("mission_plans.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus, name="approval_status", values_callable=enum_values),
        nullable=False,
        default=ApprovalStatus.PENDING,
        server_default=ApprovalStatus.PENDING.value,
    )
    operator_id: Mapped[UUID | None] = mapped_column()
    comment: Mapped[str | None] = mapped_column(Text)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    plan: Mapped["MissionPlan"] = relationship(back_populates="approvals")