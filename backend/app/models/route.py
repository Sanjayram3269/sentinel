"""Selected routes and ranked route alternatives."""

from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from geoalchemy2 import Geometry
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
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKey
from app.models.enums import ResilienceRole, RouteStatus, enum_values

if TYPE_CHECKING:
    from app.models.mission import Mission
    from app.models.vehicle import Vehicle


class Route(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "routes"
    __table_args__ = (
        CheckConstraint("distance_meters >= 0", name="ck_routes_distance_nonnegative"),
        CheckConstraint(
            "estimated_duration_seconds >= 0", name="ck_routes_duration_nonnegative"
        ),
        Index("ix_routes_mission_id", "mission_id"),
        Index("ix_routes_vehicle_id", "vehicle_id"),
        Index("ix_routes_status", "status"),
    )

    mission_id: Mapped[UUID] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), nullable=False
    )
    vehicle_id: Mapped[UUID] = mapped_column(
        ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[RouteStatus] = mapped_column(
        Enum(RouteStatus, name="route_status", values_callable=enum_values),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    geometry: Mapped[object] = mapped_column(
        Geometry(geometry_type="LINESTRING", srid=4326, spatial_index=True),
        nullable=False,
    )
    distance_meters: Mapped[float] = mapped_column(Float, nullable=False)
    estimated_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_score: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)

    mission: Mapped["Mission"] = relationship(back_populates="routes")
    vehicle: Mapped["Vehicle"] = relationship(back_populates="routes")
    candidates: Mapped[list["RouteCandidate"]] = relationship(back_populates="route")


class RouteCandidate(UUIDPrimaryKey, Base):
    __tablename__ = "route_candidates"
    __table_args__ = (
        CheckConstraint("route_rank >= 1", name="ck_route_candidates_rank_positive"),
        CheckConstraint(
            "predicted_failure_probability IS NULL OR "
            "predicted_failure_probability BETWEEN 0 AND 1",
            name="ck_route_candidates_failure_probability",
        ),
        CheckConstraint(
            "congestion_score IS NULL OR congestion_score BETWEEN 0 AND 1",
            name="ck_route_candidates_congestion_score",
        ),
        CheckConstraint(
            "score IS NULL OR score BETWEEN 0 AND 1",
            name="ck_route_candidates_score",
        ),
        Index("ix_route_candidates_mission_id", "mission_id"),
        Index("ix_route_candidates_vehicle_id", "vehicle_id"),
        Index("ix_route_candidates_route_rank", "route_rank"),
        Index("idx_route_candidates_origin", "origin", postgresql_using="gist"),
        Index(
            "idx_route_candidates_destination",
            "destination",
            postgresql_using="gist",
        ),
        Index(
            "ix_route_candidates_mission_vehicle_cycle",
            "mission_id",
            "vehicle_id",
            "planning_cycle_id",
        ),
        Index(
            "uq_route_candidates_cycle_role",
            "mission_id",
            "vehicle_id",
            "planning_cycle_id",
            "resilience_role",
            unique=True,
            postgresql_where=text("resilience_role IS NOT NULL"),
        ),
    )

    mission_id: Mapped[UUID] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), nullable=False
    )
    vehicle_id: Mapped[UUID] = mapped_column(
        ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False
    )
    route_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("routes.id", ondelete="SET NULL")
    )
    planning_cycle_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), default=uuid4, nullable=False
    )
    origin: Mapped[object | None] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=False)
    )
    destination: Mapped[object | None] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=False)
    )
    geometry: Mapped[object | None] = mapped_column(
        Geometry(geometry_type="LINESTRING", srid=4326, spatial_index=True)
    )
    status: Mapped[RouteStatus] = mapped_column(
        Enum(RouteStatus, name="route_status", values_callable=enum_values),
        nullable=False,
        default=RouteStatus.CANDIDATE,
        server_default=RouteStatus.CANDIDATE.value,
    )
    resilience_role: Mapped[ResilienceRole | None] = mapped_column(
        Enum(
            ResilienceRole,
            name="route_resilience_role",
            values_callable=enum_values,
        )
    )
    route_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    distance_meters: Mapped[float] = mapped_column(Float, nullable=False)
    risk_score: Mapped[float | None] = mapped_column(Float)
    predicted_failure_probability: Mapped[float | None] = mapped_column(Float)
    congestion_score: Mapped[float | None] = mapped_column(Float)
    score: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    hazard_exposure: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    road_segment_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    backup_viable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        default="baseline_development_provider",
        server_default="legacy",
    )
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    mission: Mapped["Mission"] = relationship(back_populates="route_candidates")
    vehicle: Mapped["Vehicle"] = relationship(back_populates="route_candidates")
    route: Mapped["Route | None"] = relationship(back_populates="candidates")