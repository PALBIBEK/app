"""Alembic revision: the worker that holds each session (``supervisor.session.holds``)."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_session_holder"
down_revision: str | None = "0027_partial_outputs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("holder", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("sessions", "holder")
