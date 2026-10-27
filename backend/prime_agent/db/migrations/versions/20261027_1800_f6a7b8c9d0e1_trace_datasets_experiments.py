"""Datasets of saved runs and experiments that replay them on a version.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-10-27 18:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trace_datasets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("harness_id", sa.String(36), sa.ForeignKey("harnesses.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("harness_id", "name", name="uq_trace_datasets_name"),
    )
    op.create_table(
        "trace_dataset_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("dataset_id", sa.String(36), sa.ForeignKey("trace_datasets.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("input", sa.Text(), nullable=False),
        sa.Column("expected", sa.Text(), nullable=True),
        sa.Column("source_session_id", sa.String(36), nullable=True),
        sa.Column("source_run_id", sa.String(64), nullable=True),
        sa.Column("source_version", sa.Integer(), nullable=True),
        sa.Column("source_status", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("dataset_id", "source_session_id", "source_run_id", name="uq_trace_dataset_items_run"),
    )
    op.create_table(
        "trace_experiments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("dataset_id", sa.String(36), sa.ForeignKey("trace_datasets.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("harness_id", sa.String(36), sa.ForeignKey("harnesses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("release_version", sa.Integer(), nullable=True),
        sa.Column("harness_version", sa.String(128), nullable=False),
        sa.Column("runtime", sa.String(64), nullable=False),
        sa.Column("composition_id", sa.String(128), nullable=True),
        sa.Column("composition_digest", sa.String(80), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "trace_experiment_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("experiment_id", sa.String(36), sa.ForeignKey("trace_experiments.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("item_id", sa.String(36), sa.ForeignKey("trace_dataset_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(36), nullable=True),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("experiment_id", "item_id", name="uq_trace_experiment_runs_item"),
    )


def downgrade() -> None:
    op.drop_table("trace_experiment_runs")
    op.drop_table("trace_experiments")
    op.drop_table("trace_dataset_items")
    op.drop_table("trace_datasets")
