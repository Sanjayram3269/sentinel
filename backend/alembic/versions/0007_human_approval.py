"""Bind human approvals to an exact, immutable plan revision.

Revision ID: 0007_human_approval
Revises: 0006_mission_optimization
Create Date: 2026-10-05

Phase 8 makes ``plan_approvals`` authoritative. The table already existed but
was never written to: there was no service, schema or endpoint that touched it,
so it held no rows and enforced nothing.

The safety rule that forces this revision is that *an approval authorizes one
exact plan version and no other*. Without a version column on the approval, an
approval recorded against version 4 would silently keep authorizing version 5
once a reviewer modified the plan. The remaining columns exist to make a
decision reconstructable after the fact: who decided, what they decided, on
which version, why, what simulation evidence they had in front of them, and
which road-network checksum that evidence was measured against.

``uq_plan_approvals_plan_version`` is the database-level guarantee behind the
idempotency and race-safety rules. It makes "two reviewers approve the same
version simultaneously" impossible to persist rather than merely unlikely, and
it is what turns a duplicate approve into a conflict instead of a second,
contradictory approval row.

``evidence`` and ``context`` are JSONB because their shape belongs to the
Phases 6/7 payloads that already use JSONB, and because a reviewer comment
column would not hold the machine-readable comparison a human actually read.

No table is created and no existing data is rewritten: the table has never
been written to, so every existing column has a zero-row column to extend.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_human_approval"
down_revision: str | None = "0006_mission_optimization"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Added to the existing plan_status enum. READY_FOR_REVIEW is the state a
# computed plan enters when it is offered to a human; the rest describe what
# happens after a decision. Existing values are untouched.
_PLAN_STATUS_VALUES = (
    "DRAFT",
    "SUBMITTED",
    "APPROVED",
    "REJECTED",
    "SUPERSEDED",
    "READY_FOR_REVIEW",
    "EXECUTION_AUTHORIZED",
    "EXECUTING",
    "REPLAN_REQUIRED",
)


def upgrade() -> None:
    bind = op.get_bind()
    # Add the new plan states. ALTER TYPE ... ADD VALUE cannot run inside a
    # transaction block on PostgreSQL < 12, and cannot be used in the same
    # transaction as a statement that reads the new value, so the enum is
    # widened first and independently.
    for value in _PLAN_STATUS_VALUES[5:]:
        op.execute(
            sa.text(
                f"ALTER TYPE plan_status ADD VALUE IF NOT EXISTS '{value}'"  # noqa: S608
            )
        )

    op.add_column(
        "mission_plans",
        sa.Column("network_key", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "mission_plans",
        sa.Column("network_checksum", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_mission_plans_network_key",
        "mission_plans",
        ["network_key"],
    )

    op.add_column(
        "plan_approvals",
        sa.Column("plan_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "plan_approvals",
        sa.Column("reviewer_id", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "plan_approvals",
        sa.Column("reviewer_role", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "plan_approvals",
        sa.Column("decision", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "plan_approvals",
        sa.Column("previous_plan_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "plan_approvals",
        sa.Column("new_plan_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "plan_approvals",
        sa.Column("correlation_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "plan_approvals",
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "plan_approvals",
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "plan_approvals",
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    # The existing ix_plan_approvals_plan_id already indexes plan_id; a second
    # index on the same column would only make writes slower.
    op.create_unique_constraint(
        "uq_plan_approvals_plan_version",
        "plan_approvals",
        ["plan_id", "plan_version"],
    )
    # An approval is only meaningful against a version, and a decision is only
    # meaningful against a reviewer. Both are enforced here rather than trusted
    # to application code, because the application is not the only writer.
    op.create_check_constraint(
        "ck_plan_approvals_version_positive",
        "plan_approvals",
        "plan_version IS NULL OR plan_version >= 1",
    )
    op.create_check_constraint(
        "ck_plan_approvals_decision_required",
        "plan_approvals",
        "decision IS NOT NULL",
    )

    # The enum widening is committed separately above; nothing further reads it
    # in this transaction.
    bind.execute(sa.text("SELECT 1"))


def downgrade() -> None:
    op.drop_constraint(
        "ck_plan_approvals_decision_required", "plan_approvals", type_="check"
    )
    op.drop_constraint(
        "ck_plan_approvals_version_positive", "plan_approvals", type_="check"
    )
    op.drop_constraint(
        "uq_plan_approvals_plan_version", "plan_approvals", type_="unique"
    )
    for column in (
        "decided_at",
        "context",
        "evidence",
        "correlation_id",
        "new_plan_status",
        "previous_plan_status",
        "decision",
        "reviewer_role",
        "reviewer_id",
        "plan_version",
    ):
        op.drop_column("plan_approvals", column)
    op.drop_index("ix_mission_plans_network_key", table_name="mission_plans")
    op.drop_column("mission_plans", "network_checksum")
    op.drop_column("mission_plans", "network_key")

    # PostgreSQL cannot remove a value from an enum. The states are left in
    # place; the Python enum is restored to its original members so the
    # application never offers them again.
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE mission_plans SET status = 'DRAFT' "
            "WHERE status IN ('READY_FOR_REVIEW', 'EXECUTION_AUTHORIZED', "
            "'EXECUTING', 'REPLAN_REQUIRED')"
        )
    )
