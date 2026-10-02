"""Create the SENTINEL domain schema.

Revision ID: 0001_initial_domain
Revises:
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_domain"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ENUMS = (
    postgresql.ENUM(
        "CREATED", "ACTIVE", "PAUSED", "COMPLETED", "CANCELLED",
        name="mission_status",
    ),
    postgresql.ENUM(
        "MEDICAL", "FIRE", "ACCIDENT", "FLOOD", "EARTHQUAKE", "CYCLONE",
        "HAZMAT", "OTHER", name="incident_type",
    ),
    postgresql.ENUM(
        "AMBULANCE", "FIRE_TRUCK", "POLICE", "RESCUE", "OTHER",
        name="vehicle_type",
    ),
    postgresql.ENUM(
        "AVAILABLE", "EN_ROUTE", "AT_SCENE", "TRANSPORTING", "OFFLINE",
        name="vehicle_status",
    ),
    postgresql.ENUM(
        "ACTIVE", "BACKUP", "CONTINGENCY", "BLOCKED", "REJECTED",
        name="route_status",
    ),
    postgresql.ENUM(
        "FLOOD", "FIRE", "EARTHQUAKE", "CYCLONE", "ROAD_CLOSURE", "ACCIDENT",
        "OTHER", name="hazard_type",
    ),
    postgresql.ENUM("ACTIVE", "RESOLVED", "EXPIRED", name="hazard_status"),
    postgresql.ENUM("OPERATIONAL", "LIMITED", "CLOSED", name="operational_status"),
    postgresql.ENUM(
        "DRAFT", "SUBMITTED", "APPROVED", "REJECTED", "SUPERSEDED",
        name="plan_status",
    ),
    postgresql.ENUM("PENDING", "APPROVED", "REJECTED", "MODIFIED", name="approval_status"),
    postgresql.ENUM(
        "PENDING", "RUNNING", "COMPLETED", "FAILED", name="simulation_status"
    ),
    postgresql.ENUM(
        "ETA", "CONGESTION", "ROUTE_RISK", "HAZARD_IMPACT", "OTHER",
        name="prediction_type",
    ),
)


def _enum(enum_type: postgresql.ENUM) -> postgresql.ENUM:
    return postgresql.ENUM(*enum_type.enums, name=enum_type.name, create_type=False)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    for enum_type in ENUMS:
        enum_type.create(op.get_bind(), checkfirst=False)

    op.create_table(
        "missions",
        sa.Column("status", _enum(ENUMS[0]), server_default="CREATED", nullable=False),
        sa.Column("priority", sa.SmallInteger(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("priority BETWEEN 1 AND 5", name="ck_missions_priority"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_missions_status", "missions", ["status"])
    op.create_index("ix_missions_created_at", "missions", ["created_at"])

    op.create_table(
        "incidents",
        sa.Column("mission_id", sa.Uuid(), nullable=True),
        sa.Column("type", _enum(ENUMS[1]), nullable=False),
        sa.Column("severity", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("location", Geometry(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("severity BETWEEN 1 AND 5", name="ck_incidents_severity"),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_incidents_mission_id", "incidents", ["mission_id"])
    op.create_index("ix_incidents_type", "incidents", ["type"])
    op.create_index("ix_incidents_occurred_at", "incidents", ["occurred_at"])
    op.create_index("idx_incidents_location", "incidents", ["location"], postgresql_using="gist")

    op.create_table(
        "vehicles",
        sa.Column("mission_id", sa.Uuid(), nullable=True),
        sa.Column("vehicle_type", _enum(ENUMS[2]), nullable=False),
        sa.Column("status", _enum(ENUMS[3]), server_default="AVAILABLE", nullable=False),
        sa.Column("call_sign", sa.String(length=80), nullable=False),
        sa.Column("capability", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("current_location", Geometry(geometry_type="POINT", srid=4326, spatial_index=False)),
        sa.Column("heading", sa.Float()),
        sa.Column("speed", sa.Float()),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("call_sign"),
    )
    op.create_index("ix_vehicles_status", "vehicles", ["status"])
    op.create_index("ix_vehicles_vehicle_type", "vehicles", ["vehicle_type"])
    op.create_index("idx_vehicles_current_location", "vehicles", ["current_location"], postgresql_using="gist")

    op.create_table(
        "vehicle_telemetry",
        sa.Column("vehicle_id", sa.Uuid(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("position", Geometry(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False),
        sa.Column("speed", sa.Float()),
        sa.Column("heading", sa.Float()),
        sa.Column("altitude", sa.Float()),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_vehicle_telemetry_vehicle_observed", "vehicle_telemetry", ["vehicle_id", "observed_at"])
    op.create_index("idx_vehicle_telemetry_position", "vehicle_telemetry", ["position"], postgresql_using="gist")

    op.create_table(
        "routes",
        sa.Column("mission_id", sa.Uuid(), nullable=False),
        sa.Column("vehicle_id", sa.Uuid(), nullable=False),
        sa.Column("status", _enum(ENUMS[4]), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("geometry", Geometry(geometry_type="LINESTRING", srid=4326, spatial_index=False), nullable=False),
        sa.Column("distance_meters", sa.Float(), nullable=False),
        sa.Column("estimated_duration_seconds", sa.Integer(), nullable=False),
        sa.Column("risk_score", sa.Float()),
        sa.Column("confidence", sa.Float()),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("distance_meters >= 0", name="ck_routes_distance_nonnegative"),
        sa.CheckConstraint("estimated_duration_seconds >= 0", name="ck_routes_duration_nonnegative"),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_routes_mission_id", "routes", ["mission_id"])
    op.create_index("ix_routes_vehicle_id", "routes", ["vehicle_id"])
    op.create_index("ix_routes_status", "routes", ["status"])
    op.create_index("idx_routes_geometry", "routes", ["geometry"], postgresql_using="gist")

    op.create_table(
        "route_candidates",
        sa.Column("mission_id", sa.Uuid(), nullable=False),
        sa.Column("vehicle_id", sa.Uuid(), nullable=False),
        sa.Column("route_id", sa.Uuid(), nullable=True),
        sa.Column("route_rank", sa.Integer(), nullable=False),
        sa.Column("estimated_duration_seconds", sa.Integer(), nullable=False),
        sa.Column("distance_meters", sa.Float(), nullable=False),
        sa.Column("risk_score", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("hazard_exposure", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("backup_viable", sa.Boolean(), nullable=False),
        sa.Column("rationale", sa.Text()),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("route_rank >= 1", name="ck_route_candidates_rank_positive"),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["route_id"], ["routes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_route_candidates_mission_id", "route_candidates", ["mission_id"])
    op.create_index("ix_route_candidates_vehicle_id", "route_candidates", ["vehicle_id"])
    op.create_index("ix_route_candidates_route_rank", "route_candidates", ["route_rank"])

    op.create_table(
        "hospitals",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("location", Geometry(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False),
        sa.Column("capacity_total", sa.Integer(), nullable=False),
        sa.Column("capacity_available", sa.Integer(), nullable=False),
        sa.Column("capability", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("operational_status", _enum(ENUMS[7]), server_default="OPERATIONAL", nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("capacity_total >= 0 AND capacity_available >= 0 AND capacity_available <= capacity_total", name="ck_hospitals_capacity"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_hospitals_location", "hospitals", ["location"], postgresql_using="gist")

    op.create_table(
        "shelters",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("location", Geometry(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False),
        sa.Column("capacity_total", sa.Integer(), nullable=False),
        sa.Column("capacity_available", sa.Integer(), nullable=False),
        sa.Column("operational_status", _enum(ENUMS[7]), server_default="OPERATIONAL", nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("capacity_total >= 0 AND capacity_available >= 0 AND capacity_available <= capacity_total", name="ck_shelters_capacity"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_shelters_location", "shelters", ["location"], postgresql_using="gist")

    op.create_table(
        "traffic_signals",
        sa.Column("external_id", sa.String(length=120), nullable=False),
        sa.Column("location", Geometry(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False),
        sa.Column("current_phase", sa.String(length=80)),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_traffic_signals_external_id", "traffic_signals", ["external_id"], unique=True)
    op.create_index("idx_traffic_signals_location", "traffic_signals", ["location"], postgresql_using="gist")

    op.create_table(
        "hazards",
        sa.Column("mission_id", sa.Uuid(), nullable=True),
        sa.Column("hazard_type", _enum(ENUMS[5]), nullable=False),
        sa.Column("severity", sa.Integer(), nullable=False),
        sa.Column("geometry", Geometry(geometry_type="MULTIPOLYGON", srid=4326, spatial_index=False), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expected_end_time", sa.DateTime(timezone=True)),
        sa.Column("status", _enum(ENUMS[6]), server_default="ACTIVE", nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_hazards_mission_id", "hazards", ["mission_id"])
    op.create_index("idx_hazards_geometry", "hazards", ["geometry"], postgresql_using="gist")

    op.create_table(
        "mission_plans",
        sa.Column("mission_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", _enum(ENUMS[8]), server_default="DRAFT", nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("plan_payload", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("score", sa.Float()),
        sa.Column("confidence", sa.Float()),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_mission_plans_version_positive"),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mission_id", "version", name="uq_mission_plans_version"),
    )
    op.create_index("ix_mission_plans_mission_id", "mission_plans", ["mission_id"])

    op.create_table(
        "plan_approvals",
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("status", _enum(ENUMS[9]), server_default="PENDING", nullable=False),
        sa.Column("operator_id", sa.Uuid()),
        sa.Column("comment", sa.Text()),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["mission_plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_plan_approvals_plan_id", "plan_approvals", ["plan_id"])

    op.create_table(
        "events",
        sa.Column("mission_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("source", sa.String(length=120), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_events_mission_occurred", "events", ["mission_id", "occurred_at"])

    op.create_table(
        "audit_logs",
        sa.Column("mission_id", sa.Uuid(), nullable=True),
        sa.Column("actor_type", sa.String(length=80), nullable=False),
        sa.Column("actor_id", sa.Uuid()),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("entity_type", sa.String(length=120), nullable=False),
        sa.Column("entity_id", sa.Uuid()),
        sa.Column("before_state", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("after_state", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_mission_created", "audit_logs", ["mission_id", "created_at"])

    op.create_table(
        "simulation_runs",
        sa.Column("mission_id", sa.Uuid(), nullable=True),
        sa.Column("scenario_name", sa.String(length=160), nullable=False),
        sa.Column("scenario_seed", sa.Integer()),
        sa.Column("simulator", sa.String(length=120), nullable=False),
        sa.Column("status", _enum(ENUMS[10]), server_default="PENDING", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("configuration", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_simulation_runs_mission_id", "simulation_runs", ["mission_id"])

    op.create_table(
        "predictions",
        sa.Column("mission_id", sa.Uuid(), nullable=False),
        sa.Column("prediction_type", _enum(ENUMS[11]), nullable=False),
        sa.Column("vehicle_id", sa.Uuid(), nullable=True),
        sa.Column("route_id", sa.Uuid(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("horizon_seconds", sa.Integer()),
        sa.Column("predicted_value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("confidence", sa.Float()),
        sa.Column("model_name", sa.String(length=160)),
        sa.Column("model_version", sa.String(length=80)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["route_id"], ["routes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_predictions_mission_observed", "predictions", ["mission_id", "observed_at"])


def downgrade() -> None:
    for table_name in (
        "predictions",
        "simulation_runs",
        "audit_logs",
        "events",
        "plan_approvals",
        "mission_plans",
        "hazards",
        "traffic_signals",
        "shelters",
        "hospitals",
        "route_candidates",
        "routes",
        "vehicle_telemetry",
        "vehicles",
        "incidents",
        "missions",
    ):
        op.drop_table(table_name)
    for enum_type in reversed(ENUMS):
        enum_type.drop(op.get_bind(), checkfirst=False)