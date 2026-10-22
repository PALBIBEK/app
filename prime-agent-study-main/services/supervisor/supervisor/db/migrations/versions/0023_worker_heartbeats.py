"""Alembic revision: worker heartbeats (``supervisor.temporal.fleet``).

One row per live session worker: how many runs it can carry, how many it carries now, and when
it last checked in. With the Temporal server's pollers and backlog, it says how the fleet is.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_worker_heartbeats"
down_revision: str | None = "0022_attachments_runs_artifacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(255), primary_key=True),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("task_queue", sa.String(255), nullable=False),
        sa.Column("home_queue", sa.String(255), nullable=False),
        sa.Column("max_runs", sa.Integer(), nullable=False),
        sa.Column("busy", sa.Integer(), nullable=False),
        sa.Column("started_at_ms", sa.BigInteger(), nullable=False),
        sa.Column("last_seen_ms", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.String(64), nullable=True),
    )
    op.create_index("ix_worker_heartbeats_queue", "worker_heartbeats", ["task_queue", "last_seen_ms"])


def downgrade() -> None:
    op.drop_index("ix_worker_heartbeats_queue", table_name="worker_heartbeats")
    op.drop_table("worker_heartbeats")
