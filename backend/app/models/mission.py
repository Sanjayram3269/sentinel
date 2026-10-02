"""Mission aggregate root."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, Enum, Index, SmallInteger, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKey
from app.models.enums import MissionStatus, enum_values

if TYPE_CHECKING:
    from app.models.event import AuditLog, Event
    from app.models.hazard import Hazard
    from app.models.incident import Incident
    from app.models.plan import MissionPlan
    from app.models.prediction import Prediction
    from app.models.route import Route, RouteCandidate
    from app.models.simulation import SimulationRun
    from app.models.vehicle import Vehicle


class Mission(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "missions"
    __table_args__ = (
        CheckConstraint("priority BETWEEN 1 AND 5", name="ck_missions_priority"),
        Index("ix_missions_status", "status"),
        Index("ix_missions_created_at", "created_at"),
    )

    status: Mapped[MissionStatus] = mapped_column(
        Enum(MissionStatus, name="mission_status", values_callable=enum_values),
        nullable=False,
        default=MissionStatus.CREATED,
        server_default=MissionStatus.CREATED.value,
    )
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=3)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    incidents: Mapped[list["Incident"]] = relationship(
        back_populates="mission", cascade="all, delete-orphan"
    )
    vehicles: Mapped[list["Vehicle"]] = relationship(back_populates="mission")
    routes: Mapped[list["Route"]] = relationship(
        back_populates="mission", cascade="all, delete-orphan"
    )
    route_candidates: Mapped[list["RouteCandidate"]] = relationship(
        back_populates="mission", cascade="all, delete-orphan"
    )
    hazards: Mapped[list["Hazard"]] = relationship(back_populates="mission")
    plans: Mapped[list["MissionPlan"]] = relationship(
        back_populates="mission", cascade="all, delete-orphan"
    )
    events: Mapped[list["Event"]] = relationship(
        back_populates="mission", cascade="all, delete-orphan"
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(back_populates="mission")
    predictions: Mapped[list["Prediction"]] = relationship(
        back_populates="mission", cascade="all, delete-orphan"
    )
    simulation_runs: Mapped[list["SimulationRun"]] = relationship(
        back_populates="mission"
    )