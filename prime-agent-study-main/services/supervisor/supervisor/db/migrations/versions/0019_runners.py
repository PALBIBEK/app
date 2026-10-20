"""Alembic revision: customer runners and their command queue."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_runners"
down_revision: str | None = "0018_hand_leases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runners",
        sa.Column("tenant_id", sa.String(length=128), primary_key=True),
        sa.Column("name", sa.String(length=64), primary_key=True),
        sa.Column("token_sha256", sa.String(length=64), nullable=False),
        sa.Column("last_seen_ms", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "runner_commands",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("runner", sa.String(length=64), nullable=False),
        sa.Column("sandbox_id", sa.String(length=64), nullable=True),
        sa.Column("op", sa.String(length=32), nullable=False),
        sa.Column("args", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_ms", sa.BigInteger(), nullable=False),
        sa.Column("claimed_ms", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        "ix_runner_commands_queue", "runner_commands", ["tenant_id", "runner", "status", "created_ms"]
    )


def downgrade() -> None:
    op.drop_index("ix_runner_commands_queue", table_name="runner_commands")
    op.drop_table("runner_commands")
    op.drop_table("runners")
