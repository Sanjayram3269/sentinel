"""Emergency incident records."""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from geoalchemy2 import Geometry
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKey
from app.models.enums import IncidentType, enum_values

if TYPE_CHECKING:
    from app.models.mission import Mission


class Incident(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "incidents"
    __table_args__ = (
        CheckConstraint("severity BETWEEN 1 AND 5", name="ck_incidents_severity"),
        Index("ix_incidents_mission_id", "mission_id"),
        Index("ix_incidents_type", "type"),
        Index("ix_incidents_occurred_at", "occurred_at"),
    )

    mission_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("missions.id", ondelete="SET NULL"), nullable=True
    )
    type: Mapped[IncidentType] = mapped_column(
        Enum(IncidentType, name="incident_type", values_callable=enum_values),
        nullable=False,
    )
    severity: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[object] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=True), nullable=False
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    mission: Mapped["Mission | None"] = relationship(back_populates="incidents")