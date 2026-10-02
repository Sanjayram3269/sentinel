"""Fixed emergency resources and traffic signal inventory."""

from typing import Any
from uuid import UUID

from geoalchemy2 import Geometry
from sqlalchemy import CheckConstraint, Enum, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKey
from app.models.enums import OperationalStatus, enum_values


class Hospital(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "hospitals"
    __table_args__ = (
        CheckConstraint(
            "capacity_total >= 0 AND capacity_available >= 0 "
            "AND capacity_available <= capacity_total",
            name="ck_hospitals_capacity",
        ),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    location: Mapped[object] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=True), nullable=False
    )
    capacity_total: Mapped[int] = mapped_column(Integer, nullable=False)
    capacity_available: Mapped[int] = mapped_column(Integer, nullable=False)
    capability: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    operational_status: Mapped[OperationalStatus] = mapped_column(
        Enum(
            OperationalStatus,
            name="operational_status",
            values_callable=enum_values,
        ),
        nullable=False,
        default=OperationalStatus.OPERATIONAL,
        server_default=OperationalStatus.OPERATIONAL.value,
    )


class Shelter(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "shelters"
    __table_args__ = (
        CheckConstraint(
            "capacity_total >= 0 AND capacity_available >= 0 "
            "AND capacity_available <= capacity_total",
            name="ck_shelters_capacity",
        ),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    location: Mapped[object] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=True), nullable=False
    )
    capacity_total: Mapped[int] = mapped_column(Integer, nullable=False)
    capacity_available: Mapped[int] = mapped_column(Integer, nullable=False)
    operational_status: Mapped[OperationalStatus] = mapped_column(
        Enum(
            OperationalStatus,
            name="operational_status",
            values_callable=enum_values,
        ),
        nullable=False,
        default=OperationalStatus.OPERATIONAL,
        server_default=OperationalStatus.OPERATIONAL.value,
    )


class TrafficSignal(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "traffic_signals"
    __table_args__ = (Index("ix_traffic_signals_external_id", "external_id", unique=True),)

    external_id: Mapped[str] = mapped_column(String(120), nullable=False)
    location: Mapped[object] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=True), nullable=False
    )
    current_phase: Mapped[str | None] = mapped_column(String(80))
    signal_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default="{}"
    )
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")