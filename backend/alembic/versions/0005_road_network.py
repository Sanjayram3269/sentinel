"""Add persistent road network and road edge tables.

Revision ID: 0005_road_network
Revises: 0004_route_resilience
Create Date: 2026-10-03

This migration creates two empty tables only. It deliberately performs no
data import: OSM-derived rows are large, regenerable, and are loaded by the
operator-run ``scripts/import_road_network.py``. A database that has run this
migration behaves exactly as it did before, so the SUMO digital twin and the
``clearpath_demo`` scenario remain usable without any road data present.

Identity follows three levels. ``road_edges.id`` is the canonical SENTINEL
identifier and is what routing, scoring and resilience reference. The SUMO
edge id is stored per network in ``external_id``, which is unique only within
its network because netconvert derives those ids. ``osm_way_id`` is provenance
and is deliberately not unique: a single OSM way is split into many SUMO edges
where junctions, traffic lights and ramps divide it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry

revision: str = "0005_road_network"
down_revision: str | None = "0004_route_resilience"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "road_networks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("network_key", sa.String(length=64), nullable=False),
        sa.Column("proj_parameter", sa.Text(), nullable=False),
        sa.Column("orig_boundary", sa.String(length=120), nullable=False),
        sa.Column("source_checksum", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_road_networks"),
        sa.UniqueConstraint("network_key", name="uq_road_networks_network_key"),
    )
    op.create_index(
        "ix_road_networks_network_key", "road_networks", ["network_key"], unique=False
    )

    op.create_table(
        "road_edges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("network_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("external_id", sa.String(length=120), nullable=False),
        sa.Column("osm_way_id", sa.BigInteger(), nullable=True),
        sa.Column("from_node", sa.String(length=64), nullable=False),
        sa.Column("to_node", sa.String(length=64), nullable=False),
        sa.Column(
            "geometry",
            Geometry(geometry_type="LINESTRING", srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.Column("length_m", sa.Float(), nullable=False),
        sa.Column("speed_limit_kmh", sa.Float(), nullable=False),
        sa.Column("road_class", sa.String(length=40), nullable=False),
        sa.Column("lanes", sa.SmallInteger(), nullable=False),
        sa.Column("has_signal", sa.Boolean(), nullable=False),
        sa.CheckConstraint("length_m >= 0", name="ck_road_edges_length_nonnegative"),
        sa.CheckConstraint("speed_limit_kmh > 0", name="ck_road_edges_speed_positive"),
        sa.CheckConstraint("lanes >= 1", name="ck_road_edges_lanes_positive"),
        sa.ForeignKeyConstraint(
            ["network_id"],
            ["road_networks.id"],
            name="fk_road_edges_network_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_road_edges"),
        # A SUMO edge id is unique only inside the network that defines it.
        sa.UniqueConstraint(
            "network_id", "external_id", name="uq_road_edges_network_external"
        ),
    )
    op.create_index("ix_road_edges_network_id", "road_edges", ["network_id"], unique=False)
    op.create_index("ix_road_edges_osm_way_id", "road_edges", ["osm_way_id"], unique=False)
    op.create_index(
        "ix_road_edges_network_from_to",
        "road_edges",
        ["network_id", "from_node", "to_node"],
        unique=False,
    )
    op.create_index(
        "idx_road_edges_geometry", "road_edges", ["geometry"], postgresql_using="gist"
    )


def downgrade() -> None:
    op.drop_index("idx_road_edges_geometry", table_name="road_edges")
    op.drop_index("ix_road_edges_network_from_to", table_name="road_edges")
    op.drop_index("ix_road_edges_osm_way_id", table_name="road_edges")
    op.drop_index("ix_road_edges_network_id", table_name="road_edges")
    op.drop_table("road_edges")
    op.drop_index("ix_road_networks_network_key", table_name="road_networks")
    op.drop_table("road_networks")
