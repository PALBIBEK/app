"""Alembic revision: trace scores (``supervisor.traces.scores``).

Automatic checks and people's feedback on runs and spans, and on each run's rollup the
counts that lists filter on.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_trace_scores"
down_revision: str | None = "0024_run_traces"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trace_scores",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("span_id", sa.String(32), nullable=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("label", sa.String(32), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("evaluator", sa.String(64), nullable=False),
        sa.Column("principal_id", sa.String(128), nullable=True),
        sa.Column("composition_id", sa.String(255), nullable=True),
        sa.Column("composition_digest", sa.String(80), nullable=True),
        sa.Column("created_ms", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_trace_scores_run", "trace_scores", ["session_id", "run_id"])
    op.create_index(
        "ix_trace_scores_version", "trace_scores", ["tenant_id", "composition_id", "composition_digest", "name"]
    )
    with op.batch_alter_table("run_traces") as batch:
        batch.add_column(sa.Column("checks_failed", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("feedback_good", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("feedback_bad", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("run_traces") as batch:
        batch.drop_column("feedback_bad")
        batch.drop_column("feedback_good")
        batch.drop_column("checks_failed")
    op.drop_index("ix_trace_scores_version", table_name="trace_scores")
    op.drop_index("ix_trace_scores_run", table_name="trace_scores")
    op.drop_table("trace_scores")
