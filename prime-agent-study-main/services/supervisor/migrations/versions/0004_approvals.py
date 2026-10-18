"""Alembic revision: approvals table for human-in-the-loop."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_approvals"
down_revision: Union[str, None] = "0003_harness"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "approvals",
        sa.Column("approval_id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("sessions.id"),
            nullable=False,
        ),
        sa.Column("turn_id", sa.String(36), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("choice", sa.Text(), nullable=True),
        sa.Column("timeout_s", sa.Numeric(18, 3), nullable=False),
        sa.Column("harness_request_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_approvals_session_id", "approvals", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_approvals_session_id", table_name="approvals")
    op.drop_table("approvals")
