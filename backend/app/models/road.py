"""Persistent road network reference data.

A road network is shared, mission-independent reference data: the same
Bengaluru street graph serves every mission. It is therefore modelled without
a mission foreign key and is never cascaded from operational state.

Identity is deliberately layered. ``RoadEdge.id`` is the canonical SENTINEL
identifier. ``external_id`` is the simulator edge identifier and is unique
only within a network, because netconvert derives edge ids from OSM ways and
renumbers the fragments it creates at junctions. ``osm_way_id`` is provenance
and is intentionally not unique: one OSM way can become dozens of SUMO edges.
"""

from datetime import datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKey


class RoadNetwork(UUIDPrimaryKey, Base):
    """One imported street network, identified by its stable key."""

    __tablename__ = "road_networks"
    __table_args__ = (
        UniqueConstraint("network_key", name="uq_road_networks_network_key"),
        Index("ix_road_networks_network_key", "network_key"),
    )

    network_key: Mapped[str] = mapped_column(String(64), nullable=False)
    # Text, not String: a proj-string is unbounded in practice, and the
    # migration column is TEXT. Declaring String here made alembic report drift.
    proj_parameter: Mapped[str] = mapped_column(Text, nullable=False)
    orig_boundary: Mapped[str] = mapped_column(String(120), nullable=False)
    source_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    # Imported networks are immutable reference data identified by checksum,
    # so a creation timestamp alone is meaningful and an updated_at is not.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    edges: Mapped[list["RoadEdge"]] = relationship(
        back_populates="network",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class RoadEdge(UUIDPrimaryKey, Base):
    """One drivable edge of a road network, in WGS84."""

    __tablename__ = "road_edges"
    __table_args__ = (
        UniqueConstraint(
            "network_id", "external_id", name="uq_road_edges_network_external"
        ),
        CheckConstraint("length_m >= 0", name="ck_road_edges_length_nonnegative"),
        CheckConstraint("speed_limit_kmh > 0", name="ck_road_edges_speed_positive"),
        CheckConstraint("lanes >= 1", name="ck_road_edges_lanes_positive"),
        Index("ix_road_edges_network_id", "network_id"),
        Index("ix_road_edges_osm_way_id", "osm_way_id"),
        Index("ix_road_edges_network_from_to", "network_id", "from_node", "to_node"),
    )

    network_id: Mapped[Any] = mapped_column(
        ForeignKey("road_networks.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    external_id: Mapped[str] = mapped_column(String(120), nullable=False)
    osm_way_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    from_node: Mapped[str] = mapped_column(String(64), nullable=False)
    to_node: Mapped[str] = mapped_column(String(64), nullable=False)
    geometry: Mapped[object] = mapped_column(
        Geometry(geometry_type="LINESTRING", srid=4326, spatial_index=True),
        nullable=False,
    )
    length_m: Mapped[float] = mapped_column(Float, nullable=False)
    speed_limit_kmh: Mapped[float] = mapped_column(Float, nullable=False)
    road_class: Mapped[str] = mapped_column(String(40), nullable=False)
    lanes: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    has_signal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    network: Mapped["RoadNetwork"] = relationship(back_populates="edges")
