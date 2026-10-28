"""Alembic revision: the reply a model is writing, as it streams (``supervisor.journal.partials``)."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_partial_outputs"
down_revision: str | None = "0026_trace_judges"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "partial_outputs",
        sa.Column("session_id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("thinking", sa.Text(), nullable=False),
        sa.Column("updated_ms", sa.BigInteger(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("partial_outputs")
