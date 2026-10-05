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
    String,
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
        Index("ix_mission_plans_network_key", "network_key"),
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
    # The road-network identity this plan was decided against. A plan whose
    # network has since been re-imported describes a world that no longer
    # exists, so approval must compare these against the current checksum
    # rather than assume the graph is unchanged. NULL means "not recorded";
    # the authorization gate treats that as unknown, not as matching.
    network_key: Mapped[str | None] = mapped_column(String(64))
    network_checksum: Mapped[str | None] = mapped_column(String(64))

    mission: Mapped["Mission"] = relationship(back_populates="plans")
    approvals: Mapped[list["PlanApproval"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )


class PlanApproval(UUIDPrimaryKey, Base):
    """A human decision about one exact plan version.

    The version binding is the whole point of this table: ``plan_version`` is
    captured at decision time and the unique constraint on
    ``(plan_id, plan_version)`` makes two conflicting decisions on the same
    revision impossible to persist. ``operator_id`` predates Phase 8 and is a
    UUID; the reviewer columns carry the explicit development identity, because
    this prototype has no authentication system and inventing one would be a
    worse lie than labelling the identity for what it is.
    """

    __tablename__ = "plan_approvals"
    __table_args__ = (
        Index("ix_plan_approvals_plan_id", "plan_id"),
        UniqueConstraint("plan_id", "plan_version", name="uq_plan_approvals_plan_version"),
        CheckConstraint(
            "plan_version IS NULL OR plan_version >= 1",
            name="ck_plan_approvals_version_positive",
        ),
        CheckConstraint("decision IS NOT NULL", name="ck_plan_approvals_decision_required"),
    )

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
    # The exact revision this decision authorizes. An approval for version 4
    # does not authorize version 5.
    plan_version: Mapped[int | None] = mapped_column(Integer)
    # Explicit prototype reviewer identity. Not an authentication system: this
    # prototype has none, and the string says so where a reader will see it.
    reviewer_id: Mapped[str | None] = mapped_column(String(120))
    reviewer_role: Mapped[str | None] = mapped_column(String(120))
    decision: Mapped[str | None] = mapped_column(String(32))
    previous_plan_status: Mapped[str | None] = mapped_column(String(32))
    new_plan_status: Mapped[str | None] = mapped_column(String(32))
    correlation_id: Mapped[UUID | None] = mapped_column()
    # What the reviewer had in front of them, and under which world. JSONB
    # because both belong to the Phase 6/7 payload shapes that are already
    # JSONB rather than to a fixed column per field.
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    context: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str | None] = mapped_column(Text)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    plan: Mapped["MissionPlan"] = relationship(back_populates="approvals")