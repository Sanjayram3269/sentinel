"""Emergency vehicles and append-only position telemetry."""

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from geoalchemy2 import Geometry
from sqlalchemy import DateTime, Enum, Float, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKey
from app.models.enums import VehicleStatus, VehicleType, enum_values

if TYPE_CHECKING:
    from app.models.mission import Mission
    from app.models.route import Route, RouteCandidate


class Vehicle(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "vehicles"
    __table_args__ = (
        Index("ix_vehicles_status", "status"),
        Index("ix_vehicles_vehicle_type", "vehicle_type"),
    )

    mission_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("missions.id", ondelete="SET NULL"), nullable=True
    )
    vehicle_type: Mapped[VehicleType] = mapped_column(
        Enum(VehicleType, name="vehicle_type", values_callable=enum_values),
        nullable=False,
    )
    status: Mapped[VehicleStatus] = mapped_column(
        Enum(VehicleStatus, name="vehicle_status", values_callable=enum_values),
        nullable=False,
        default=VehicleStatus.AVAILABLE,
        server_default=VehicleStatus.AVAILABLE.value,
    )
    call_sign: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    capability: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    current_location: Mapped[object | None] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=True)
    )
    heading: Mapped[float | None] = mapped_column(Float)
    speed: Mapped[float | None] = mapped_column(Float)

    mission: Mapped["Mission | None"] = relationship(back_populates="vehicles")
    telemetry: Mapped[list["VehicleTelemetry"]] = relationship(
        back_populates="vehicle", cascade="all, delete-orphan"
    )
    routes: Mapped[list["Route"]] = relationship(back_populates="vehicle")
    route_candidates: Mapped[list["RouteCandidate"]] = relationship(
        back_populates="vehicle"
    )


class VehicleTelemetry(UUIDPrimaryKey, Base):
    __tablename__ = "vehicle_telemetry"
    __table_args__ = (
        Index("ix_vehicle_telemetry_vehicle_observed", "vehicle_id", "observed_at"),
    )

    vehicle_id: Mapped[UUID] = mapped_column(
        ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    position: Mapped[object] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=True), nullable=False
    )
    speed: Mapped[float | None] = mapped_column(Float)
    heading: Mapped[float | None] = mapped_column(Float)
    altitude: Mapped[float | None] = mapped_column(Float)
    status: Mapped[VehicleStatus | None] = mapped_column(
        Enum(VehicleStatus, name="vehicle_status", values_callable=enum_values)
    )
    source: Mapped[str] = mapped_column(String(80), nullable=False)

    vehicle: Mapped["Vehicle"] = relationship(back_populates="telemetry")