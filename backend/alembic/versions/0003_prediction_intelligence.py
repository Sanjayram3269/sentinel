"""Add prediction probability, provenance, explainability, and correlation.

Revision ID: 0003_prediction_intelligence
Revises: 0002_event_state_engine
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_prediction_intelligence"
down_revision: str | None = "0002_event_state_engine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE prediction_type ADD VALUE IF NOT EXISTS 'ROUTE_FAILURE'")

    op.add_column("predictions", sa.Column("correlation_id", sa.Uuid(), nullable=True))
    op.execute(
        "UPDATE predictions SET correlation_id = id WHERE correlation_id IS NULL"
    )
    op.alter_column("predictions", "correlation_id", nullable=False)

    op.add_column("predictions", sa.Column("probability", sa.Float(), nullable=True))
    op.add_column(
        "predictions",
        sa.Column(
            "factors",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "predictions", sa.Column("source", sa.String(length=120), nullable=True)
    )
    op.execute(
        "UPDATE predictions SET source = COALESCE(model_name, 'sentinel_prediction_engine') "
        "WHERE source IS NULL"
    )
    op.alter_column("predictions", "source", nullable=False)
    op.add_column(
        "predictions",
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("predictions", "metadata")
    op.drop_column("predictions", "source")
    op.drop_column("predictions", "factors")
    op.drop_column("predictions", "probability")
    op.drop_column("predictions", "correlation_id")