"""Alembic revision: additive composition_pins JSON on sessions."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_composition_pins"
down_revision: Union[str, None] = "0004_approvals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("composition_pins", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sessions", "composition_pins")
