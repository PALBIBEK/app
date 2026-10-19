"""Alembic revision: additive composition_pins JSON on sessions."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_composition_pins"
down_revision: str | None = "0004_approvals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("composition_pins", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sessions", "composition_pins")
