"""Alembic revision: hand leases (one holder per hand, with a fencing token)."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_hand_leases"
down_revision: str | None = "0017_retire_pi_harness"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hand_leases",
        sa.Column("hand_key", sa.String(length=200), primary_key=True),
        sa.Column("holder", sa.String(length=200), nullable=True),
        sa.Column("token", sa.BigInteger(), nullable=False),
        sa.Column("expires_ms", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("hand_leases")
