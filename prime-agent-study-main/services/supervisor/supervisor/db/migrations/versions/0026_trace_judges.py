"""Alembic revision: run summaries, score topics and judges (``supervisor.traces``).

A one-sentence summary on each run's rollup; a topic on each score (what a failure is
about, for grouping issues); and judges, the rubrics a model applies to finished runs.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_trace_judges"
down_revision: str | None = "0025_trace_scores"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("run_traces") as batch:
        batch.add_column(sa.Column("summary", sa.Text(), nullable=True))
        batch.add_column(sa.Column("summary_by", sa.String(16), nullable=True))
    with op.batch_alter_table("trace_scores") as batch:
        batch.add_column(sa.Column("topic", sa.String(128), nullable=True))
    op.create_table(
        "trace_judges",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("composition_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("rubric", sa.Text(), nullable=False),
        sa.Column("sample_rate", sa.Float(), nullable=False),
        sa.Column("model", sa.String(255), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("created_ms", sa.BigInteger(), nullable=False),
        sa.Column("updated_ms", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("tenant_id", "composition_id", "name", name="uq_trace_judges_name"),
    )


def downgrade() -> None:
    op.drop_table("trace_judges")
    with op.batch_alter_table("trace_scores") as batch:
        batch.drop_column("topic")
    with op.batch_alter_table("run_traces") as batch:
        batch.drop_column("summary_by")
        batch.drop_column("summary")
