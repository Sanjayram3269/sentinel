"""Time-bounded geographic hazards."""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from geoalchemy2 import Geometry
from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKey
from app.models.enums import HazardStatus, HazardType, enum_values

if TYPE_CHECKING:
    from app.models.mission import Mission


class Hazard(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "hazards"
    __table_args__ = (Index("ix_hazards_mission_id", "mission_id"),)

    mission_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("missions.id", ondelete="SET NULL")
    )
    hazard_type: Mapped[HazardType] = mapped_column(
        Enum(HazardType, name="hazard_type", values_callable=enum_values),
        nullable=False,
    )
    severity: Mapped[int] = mapped_column(Integer, nullable=False)
    geometry: Mapped[object] = mapped_column(
        Geometry(geometry_type="MULTIPOLYGON", srid=4326, spatial_index=True),
        nullable=False,
    )
    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expected_end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[HazardStatus] = mapped_column(
        Enum(HazardStatus, name="hazard_status", values_callable=enum_values),
        nullable=False,
        default=HazardStatus.ACTIVE,
        server_default=HazardStatus.ACTIVE.value,
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)

    mission: Mapped["Mission | None"] = relationship(back_populates="hazards")