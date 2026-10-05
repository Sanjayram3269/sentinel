"""Add hospital reliability and mission-plan feasibility/rationale.

Revision ID: 0006_mission_optimization
Revises: 0005_road_network
Create Date: 2026-10-05

Phase 6 mission optimization reuses the existing ``hospitals`` and
``mission_plans`` tables rather than introducing a second hospital entity or a
second plan model, so this revision only adds the columns those tables were
genuinely missing. No table is created and no data is backfilled: every new
column is nullable, and NULL is read by the optimizer as "unavailable" and
reported as such instead of being defaulted to a number that would rank every
hospital identically without evidence.

``mission_plans.plan_payload`` already carries the selected hospital, route and
resources, so those selections are stored in the existing JSONB column rather
than in a new association table. That keeps plan history free of rows that can
be orphaned by a deleted hospital or vehicle.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_mission_optimization"
down_revision: str | None = "0005_road_network"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("hospitals", sa.Column("reliability", sa.Float(), nullable=True))
    op.add_column("mission_plans", sa.Column("feasible", sa.Boolean(), nullable=True))
    op.add_column("mission_plans", sa.Column("rationale", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("mission_plans", "rationale")
    op.drop_column("mission_plans", "feasible")
    op.drop_column("hospitals", "reliability")
