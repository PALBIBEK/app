"""Alembic revision: run trace rollups (``supervisor.traces``).

One row per run: its status, timing, cost and counts, rebuilt from the journal. Lists of
traces and their filters read only this table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_run_traces"
down_revision: str | None = "0023_worker_heartbeats"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_traces",
        sa.Column("session_id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("harness_version", sa.String(255), nullable=False),
        sa.Column("composition_id", sa.String(255), nullable=True),
        sa.Column("composition_digest", sa.String(80), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("started_ms", sa.BigInteger(), nullable=False),
        sa.Column("ended_ms", sa.BigInteger(), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("cost", sa.Float(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("steps", sa.Integer(), nullable=False),
        sa.Column("tool_calls", sa.Integer(), nullable=False),
        sa.Column("tool_errors", sa.Integer(), nullable=False),
        sa.Column("denials", sa.Integer(), nullable=False),
        sa.Column("errors", sa.Integer(), nullable=False),
        sa.Column("input_preview", sa.Text(), nullable=True),
        sa.Column("output_preview", sa.Text(), nullable=True),
        sa.Column("rollup", sa.JSON(), nullable=False),
        sa.Column("updated_ms", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_run_traces_tenant_started", "run_traces", ["tenant_id", "started_ms"])
    op.create_index("ix_run_traces_session_started", "run_traces", ["session_id", "started_ms"])
    op.create_index("ix_run_traces_composition", "run_traces", ["tenant_id", "composition_id", "composition_digest"])


def downgrade() -> None:
    op.drop_index("ix_run_traces_composition", table_name="run_traces")
    op.drop_index("ix_run_traces_session_started", table_name="run_traces")
    op.drop_index("ix_run_traces_tenant_started", table_name="run_traces")
    op.drop_table("run_traces")
