"""Add route candidate resilience features and lifecycle states.

Revision ID: 0004_route_resilience
Revises: 0003_prediction_intelligence
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry
from sqlalchemy.dialects import postgresql

revision: str = "0004_route_resilience"
down_revision: str | None = "0003_prediction_intelligence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for value in ("CANDIDATE", "DEGRADED", "FAILED", "COMPLETED", "ABORTED"):
            op.execute(f"ALTER TYPE route_status ADD VALUE IF NOT EXISTS '{value}'")
    resilience_role = postgresql.ENUM(
        "PRIMARY", "BACKUP", "CONTINGENCY", name="route_resilience_role"
    )
    resilience_role.create(op.get_bind(), checkfirst=True)

    op.add_column("route_candidates", sa.Column("planning_cycle_id", sa.Uuid(), nullable=True))
    op.execute(
        "UPDATE route_candidates SET planning_cycle_id = id "
        "WHERE planning_cycle_id IS NULL"
    )
    op.alter_column("route_candidates", "planning_cycle_id", nullable=False)
    op.add_column(
        "route_candidates",
        sa.Column("origin", Geometry(geometry_type="POINT", srid=4326, spatial_index=False)),
    )
    op.add_column(
        "route_candidates",
        sa.Column("destination", Geometry(geometry_type="POINT", srid=4326, spatial_index=False)),
    )
    op.add_column(
        "route_candidates",
        sa.Column("geometry", Geometry(geometry_type="LINESTRING", srid=4326, spatial_index=False)),
    )
    op.add_column(
        "route_candidates",
        sa.Column(
            "status",
            postgresql.ENUM(
                "CANDIDATE",
                "ACTIVE",
                "DEGRADED",
                "FAILED",
                "COMPLETED",
                "ABORTED",
                "BACKUP",
                "CONTINGENCY",
                "BLOCKED",
                "REJECTED",
                name="route_status",
                create_type=False,
            ),
            server_default="CANDIDATE",
            nullable=False,
        ),
    )
    op.add_column(
        "route_candidates",
        sa.Column("resilience_role", resilience_role, nullable=True),
    )
    op.add_column(
        "route_candidates",
        sa.Column("predicted_failure_probability", sa.Float(), nullable=True),
    )
    op.add_column("route_candidates", sa.Column("congestion_score", sa.Float(), nullable=True))
    op.add_column("route_candidates", sa.Column("score", sa.Float(), nullable=True))
    op.add_column(
        "route_candidates",
        sa.Column(
            "road_segment_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "route_candidates",
        sa.Column("provider", sa.String(length=120), server_default="legacy", nullable=True),
    )
    op.execute("UPDATE route_candidates SET provider = 'legacy' WHERE provider IS NULL")
    op.alter_column(
        "route_candidates",
        "provider",
        nullable=False,
        server_default="legacy",
    )
    op.add_column(
        "route_candidates",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
    )
    op.execute("UPDATE route_candidates SET updated_at = created_at WHERE updated_at IS NULL")
    op.alter_column("route_candidates", "updated_at", nullable=False)
    op.alter_column("route_candidates", "risk_score", nullable=True)
    op.create_check_constraint(
        "ck_route_candidates_failure_probability",
        "route_candidates",
        "predicted_failure_probability IS NULL OR "
        "predicted_failure_probability BETWEEN 0 AND 1",
    )
    op.create_check_constraint(
        "ck_route_candidates_congestion_score",
        "route_candidates",
        "congestion_score IS NULL OR congestion_score BETWEEN 0 AND 1",
    )
    op.create_check_constraint(
        "ck_route_candidates_score",
        "route_candidates",
        "score IS NULL OR score BETWEEN 0 AND 1",
    )

    op.create_index(
        "ix_route_candidates_mission_vehicle_cycle",
        "route_candidates",
        ["mission_id", "vehicle_id", "planning_cycle_id"],
    )
    op.create_index(
        "uq_route_candidates_cycle_role",
        "route_candidates",
        ["mission_id", "vehicle_id", "planning_cycle_id", "resilience_role"],
        unique=True,
        postgresql_where=sa.text("resilience_role IS NOT NULL"),
    )
    op.create_index(
        "idx_route_candidates_geometry",
        "route_candidates",
        ["geometry"],
        postgresql_using="gist",
    )
    op.create_index(
        "idx_route_candidates_origin",
        "route_candidates",
        ["origin"],
        postgresql_using="gist",
    )
    op.create_index(
        "idx_route_candidates_destination",
        "route_candidates",
        ["destination"],
        postgresql_using="gist",
    )


def downgrade() -> None:
    has_task5_candidate_data = op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM route_candidates WHERE "
            "origin IS NOT NULL OR destination IS NOT NULL OR geometry IS NOT NULL "
            "OR resilience_role IS NOT NULL "
            "OR predicted_failure_probability IS NOT NULL "
            "OR congestion_score IS NOT NULL OR score IS NOT NULL "
            "OR road_segment_ids <> '[]'::jsonb OR provider <> 'legacy' "
            "OR status <> 'CANDIDATE')"
        )
    )
    if has_task5_candidate_data:
        raise RuntimeError(
            "Cannot downgrade 0004 while Task 5 candidate data exists; "
            "export or explicitly archive it before retrying."
        )

    # Preserve the old NOT NULL contract with a documented conservative fallback.
    op.execute(
        "UPDATE route_candidates SET risk_score = 1.0 WHERE risk_score IS NULL"
    )

    # PostgreSQL cannot remove enum labels directly. Map rows first so the
    # pre-Task 5 ORM only encounters values it knows; unused labels remain.
    op.execute(
        "UPDATE routes SET status = CASE status::text "
        "WHEN 'CANDIDATE' THEN 'REJECTED' "
        "WHEN 'DEGRADED' THEN 'BLOCKED' "
        "WHEN 'FAILED' THEN 'BLOCKED' "
        "WHEN 'COMPLETED' THEN 'REJECTED' "
        "WHEN 'ABORTED' THEN 'REJECTED' "
        "ELSE status::text END::route_status "
        "WHERE status::text IN "
        "('CANDIDATE', 'DEGRADED', 'FAILED', 'COMPLETED', 'ABORTED')"
    )

    op.drop_constraint(
        "ck_route_candidates_score", "route_candidates", type_="check"
    )
    op.drop_constraint(
        "ck_route_candidates_congestion_score", "route_candidates", type_="check"
    )
    op.drop_constraint(
        "ck_route_candidates_failure_probability", "route_candidates", type_="check"
    )
    op.drop_index("idx_route_candidates_destination", table_name="route_candidates")
    op.drop_index("idx_route_candidates_origin", table_name="route_candidates")
    op.drop_index("idx_route_candidates_geometry", table_name="route_candidates")
    op.drop_index("uq_route_candidates_cycle_role", table_name="route_candidates")
    op.drop_index("ix_route_candidates_mission_vehicle_cycle", table_name="route_candidates")
    op.alter_column("route_candidates", "risk_score", nullable=False)
    op.drop_column("route_candidates", "updated_at")
    op.drop_column("route_candidates", "provider")
    op.drop_column("route_candidates", "road_segment_ids")
    op.drop_column("route_candidates", "score")
    op.drop_column("route_candidates", "congestion_score")
    op.drop_column("route_candidates", "predicted_failure_probability")
    op.drop_column("route_candidates", "resilience_role")
    op.drop_column("route_candidates", "status")
    op.drop_column("route_candidates", "geometry")
    op.drop_column("route_candidates", "destination")
    op.drop_column("route_candidates", "origin")
    op.drop_column("route_candidates", "planning_cycle_id")
    resilience_role = postgresql.ENUM(
        "PRIMARY", "BACKUP", "CONTINGENCY", name="route_resilience_role"
    )
    resilience_role.drop(op.get_bind(), checkfirst=True)