"""Agents can propose a change by themselves when an issue keeps growing.

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-11-05 02:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5d6e7f8a9b0"
down_revision: str | None = "b4c5d6e7f8a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Idempotent: a database ``create_all`` made (adopted at an older revision) has it already.
    if sa.inspect(op.get_bind()).has_table("trace_auto_improve"):
        return
    op.create_table(
        "trace_auto_improve",
        sa.Column("harness_id", sa.String(length=36), sa.ForeignKey("harnesses.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dataset_id", sa.String(length=36), sa.ForeignKey("trace_datasets.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("min_runs", sa.Integer(), nullable=False),
        sa.Column("cooldown_minutes", sa.Integer(), nullable=False),
        sa.Column("rounds", sa.Integer(), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_proposal_id", sa.String(length=36), nullable=True),
        sa.Column("last_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_trace_auto_improve_user_id", "trace_auto_improve", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_trace_auto_improve_user_id", table_name="trace_auto_improve")
    op.drop_table("trace_auto_improve")
