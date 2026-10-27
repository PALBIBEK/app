"""Proposed changes to an agent, drafted from its traces and gated on its datasets.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-10-28 09:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "trace_proposals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("harness_id", sa.String(36), sa.ForeignKey("harnesses.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("dataset_id", sa.String(36), sa.ForeignKey("trace_datasets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("base_manifest", JSON_DOCUMENT, nullable=False),
        sa.Column("candidate_manifest", JSON_DOCUMENT, nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("changed", JSON_DOCUMENT, nullable=True),
        sa.Column("evidence", JSON_DOCUMENT, nullable=True),
        sa.Column("candidate", JSON_DOCUMENT, nullable=True),
        sa.Column("baseline_experiment_id", sa.String(36), nullable=True),
        sa.Column("candidate_experiment_id", sa.String(36), nullable=True),
        sa.Column("gate", JSON_DOCUMENT, nullable=True),
        sa.Column("release_version", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("trace_proposals")
