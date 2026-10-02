"""Add event correlation, telemetry status, and dispatched mission state.

Revision ID: 0002_event_state_engine
Revises: 0001_initial_domain
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_event_state_engine"
down_revision: str | None = "0001_initial_domain"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE mission_status ADD VALUE IF NOT EXISTS 'DISPATCHED'")

    op.add_column(
        "events",
        sa.Column("correlation_id", sa.Uuid(), nullable=True),
    )
    op.execute("UPDATE events SET correlation_id = id WHERE correlation_id IS NULL")
    op.alter_column("events", "correlation_id", nullable=False)

    op.add_column(
        "vehicle_telemetry",
        sa.Column(
            "status",
            postgresql.ENUM(
                "AVAILABLE",
                "EN_ROUTE",
                "AT_SCENE",
                "TRANSPORTING",
                "OFFLINE",
                name="vehicle_status",
                create_type=False,
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("vehicle_telemetry", "status")
    op.drop_column("events", "correlation_id")